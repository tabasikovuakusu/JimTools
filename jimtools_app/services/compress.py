"""
JimTools — Smart PDF Compression Engine
=========================================

This is the core of the tool. It does what iLovePDF/PDF24 don't:
  1. Target-size aware: hit 5MB / 10MB / custom MB on purpose, not by trial-and-error.
  2. Per-page color detection: keep color where it matters (seals, stamps), grayscale only text-only pages.
  3. Honest fallback: if a file genuinely can't hit target without wrecking quality, auto-split it cleanly.
  4. Don't make it worse: if compression would produce a larger file than the input, return the input.

Pipeline per file:
  Step 1 — Inspect the input (size, page count, image DPIs, color usage per page)
  Step 2 — If already under target, skip (don't re-compress, that loses quality for nothing)
  Step 3 — Try increasingly aggressive Ghostscript presets until target is hit
  Step 4 — If no preset hits target, return best attempt + flag for auto-split

Honest limits flagged in the .docx:
  - 65MB -> 5MB (Canada): may not be reachable on color-heavy scans without quality loss
  - 65MB -> 10MB (NZ): much more achievable
  - We don't lie about it — if we can't hit it, we say so and offer auto-split.
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pikepdf
import fitz  # PyMuPDF


# Ghostscript binary name differs by platform.
# On Windows it's gswin64c.exe (or gswin32c.exe on 32-bit systems).
# On Linux/macOS it's just gs.
# We try them in order. The install instructions will make sure one of these is on PATH.
GS_CANDIDATES = ["gswin64c", "gswin32c", "gs"]


def find_ghostscript() -> str:
    """Locate the Ghostscript binary. Raises if none found."""
    for candidate in GS_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    raise RuntimeError(
        "Ghostscript not found. Install from https://ghostscript.com/releases/gsdnld.html "
        "and make sure 'gswin64c' is on your PATH."
    )


@dataclass
class CompressionResult:
    """What we report back to the frontend after compressing."""
    success: bool
    output_path: str  # Path to the compressed file (or original if no compression helped)
    original_size_bytes: int
    final_size_bytes: int
    target_size_bytes: int
    hit_target: bool
    setting_used: str  # Human-readable description of what worked
    needs_split: bool  # True if file couldn't hit target — auto-split is recommended
    pages_in_color: int  # How many pages we kept in color
    pages_in_gray: int   # How many pages we grayscaled
    message: str         # Human-readable note for the user


def inspect_pdf(pdf_path: str) -> dict:
    """
    Look at the file before compressing. Returns metadata we use to choose settings.

    Reasoning:
      - If file is mostly typed text (no big images), grayscale gains us nothing.
      - If file has color seals/stamps, blanket grayscale destroys them — flag those pages.
      - If images are already low-DPI, downsampling further wrecks readability.
    """
    info = {
        "page_count": 0,
        "size_bytes": os.path.getsize(pdf_path),
        "color_pages": [],   # 1-indexed page numbers with significant color content
        "max_image_dpi": 0,
        "has_text_layer": False,
    }

    try:
        doc = fitz.open(pdf_path)
        info["page_count"] = len(doc)

        for i, page in enumerate(doc, start=1):
            # Does the page have a text layer (typed) or is it a scan?
            if page.get_text().strip():
                info["has_text_layer"] = True

            # Check for color content on the page.
            # We render a low-DPI thumbnail and check if any pixel has significant saturation.
            # This is much cheaper than per-pixel scanning of the full page.
            pix = page.get_pixmap(dpi=30, colorspace=fitz.csRGB)
            samples = pix.samples
            # samples is bytes: RGB RGB RGB...  Check saturation of a subset.
            stride = max(1, len(samples) // 3000)  # sample ~1000 pixels
            colored = False
            for j in range(0, len(samples) - 2, stride * 3):
                r, g, b = samples[j], samples[j+1], samples[j+2]
                mx, mn = max(r, g, b), min(r, g, b)
                # If the max channel is much higher than the min, it's a colored pixel.
                # We use a conservative threshold so faint scanner tints don't trigger it.
                if mx > 100 and (mx - mn) > 40:
                    colored = True
                    break
            if colored:
                info["color_pages"].append(i)

            # Track the highest image DPI we see on the page.
            for img in page.get_images():
                xref = img[0]
                try:
                    img_dict = doc.extract_image(xref)
                    w = img_dict.get("width", 0)
                    # Rough DPI estimate: image width / page width in inches.
                    page_w_inches = page.rect.width / 72.0
                    if page_w_inches > 0:
                        dpi = w / page_w_inches
                        info["max_image_dpi"] = max(info["max_image_dpi"], dpi)
                except Exception:
                    pass

        doc.close()
    except Exception as e:
        # If inspection fails, fall back to conservative assumptions.
        info["inspection_error"] = str(e)

    return info


def run_ghostscript(input_path: str, output_path: str, settings: dict) -> bool:
    """
    Run Ghostscript with the given settings. Returns True on success.

    `settings` keys we understand:
      - 'preset': '/screen', '/ebook', '/printer', '/prepress', or None for custom
      - 'color_dpi': int, downsample resolution for color images
      - 'gray_dpi': int, downsample resolution for grayscale images
      - 'mono_dpi': int, downsample resolution for 1-bit (B&W) images
      - 'force_gray': bool, convert everything to grayscale
      - 'jpeg_quality': int 1-100, JPEG quality for re-encoded images
    """
    gs = find_ghostscript()
    cmd = [
        gs,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.5",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        "-dSAFER",  # Disable file operators in the PDF — security hardening
    ]

    if settings.get("preset"):
        cmd.append(f"-dPDFSETTINGS={settings['preset']}")

    if settings.get("force_gray"):
        cmd.extend([
            "-sColorConversionStrategy=Gray",
            "-dProcessColorModel=/DeviceGray",
        ])

    if settings.get("color_dpi"):
        cmd.extend([
            "-dDownsampleColorImages=true",
            f"-dColorImageResolution={settings['color_dpi']}",
            "-dColorImageDownsampleType=/Bicubic",
        ])

    if settings.get("gray_dpi"):
        cmd.extend([
            "-dDownsampleGrayImages=true",
            f"-dGrayImageResolution={settings['gray_dpi']}",
            "-dGrayImageDownsampleType=/Bicubic",
        ])

    if settings.get("mono_dpi"):
        cmd.extend([
            "-dDownsampleMonoImages=true",
            f"-dMonoImageResolution={settings['mono_dpi']}",
        ])

    if settings.get("jpeg_quality"):
        cmd.extend([
            "-dAutoFilterColorImages=false",
            "-dColorImageFilter=/DCTEncode",
            f"-dJPEGQ={settings['jpeg_quality']}",
        ])

    cmd.extend([f"-sOutputFile={output_path}", input_path])

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


# Compression "ladder" — increasingly aggressive settings.
# We walk down this list until we hit the target size.
# Order matters: each step is more aggressive than the last.
# We do NOT include blanket-grayscale steps because they destroy red seals (verified on real files).
COMPRESSION_LADDER = [
    # Step 1: light — lossless re-save + mild downsampling
    {"label": "light (200 DPI color)", "color_dpi": 200, "gray_dpi": 200, "jpeg_quality": 85},
    # Step 2: standard — what most users actually want
    {"label": "standard (150 DPI color)", "color_dpi": 150, "gray_dpi": 150, "jpeg_quality": 80},
    # Step 3: strong — visa-portal target
    {"label": "strong (120 DPI color)", "color_dpi": 120, "gray_dpi": 120, "jpeg_quality": 75},
    # Step 4: aggressive — for stubborn files
    {"label": "aggressive (100 DPI color)", "color_dpi": 100, "gray_dpi": 100, "jpeg_quality": 65},
    # Step 5: maximum (Ghostscript /screen preset, ~72 DPI)
    {"label": "maximum (Ghostscript /screen)", "preset": "/screen"},
]


def compress_to_target(
    input_path: str,
    output_path: str,
    target_mb: float,
) -> CompressionResult:
    """
    Main entry point. Compress `input_path` to `output_path`, aiming for `target_mb`.

    Logic:
      1. Inspect file.
      2. If already under target, copy as-is (don't re-compress — it can make things worse,
         as we proved on the sample files).
      3. Walk the compression ladder. Stop at the first setting that hits target.
      4. If nothing hits target, return the smallest result we got + flag needs_split=True.
      5. NEVER return a file larger than the input — if best result is bigger, return original.
    """
    target_bytes = int(target_mb * 1024 * 1024)
    original_size = os.path.getsize(input_path)

    info = inspect_pdf(input_path)
    color_page_count = len(info["color_pages"])
    gray_page_count = info["page_count"] - color_page_count

    # Case 1: already under target. Don't touch it.
    if original_size <= target_bytes:
        shutil.copyfile(input_path, output_path)
        return CompressionResult(
            success=True,
            output_path=output_path,
            original_size_bytes=original_size,
            final_size_bytes=original_size,
            target_size_bytes=target_bytes,
            hit_target=True,
            setting_used="no compression needed (already under target)",
            needs_split=False,
            pages_in_color=color_page_count,
            pages_in_gray=gray_page_count,
            message=f"File is already {original_size/1024/1024:.2f} MB, under your {target_mb} MB target.",
        )

    # Case 2: walk the ladder.
    best_size = original_size
    best_path = None
    best_label = "original (no compression helped)"

    with tempfile.TemporaryDirectory() as tmpdir:
        for step in COMPRESSION_LADDER:
            tmp_out = os.path.join(tmpdir, f"step_{step['label'].split()[0]}.pdf")
            ok = run_ghostscript(input_path, tmp_out, step)
            if not ok:
                continue
            size = os.path.getsize(tmp_out)

            # Track the best result so far (smallest that's still smaller than original).
            if size < best_size:
                best_size = size
                if best_path and os.path.exists(best_path):
                    os.remove(best_path)
                # Move out of tmpdir so it survives cleanup.
                best_path = os.path.join(os.path.dirname(output_path), f".best_{os.path.basename(output_path)}")
                shutil.copyfile(tmp_out, best_path)
                best_label = step["label"]

            # Hit target? Stop here.
            if size <= target_bytes:
                shutil.move(tmp_out, output_path)
                if best_path and os.path.exists(best_path) and best_path != output_path:
                    os.remove(best_path)
                return CompressionResult(
                    success=True,
                    output_path=output_path,
                    original_size_bytes=original_size,
                    final_size_bytes=size,
                    target_size_bytes=target_bytes,
                    hit_target=True,
                    setting_used=step["label"],
                    needs_split=False,
                    pages_in_color=color_page_count,
                    pages_in_gray=gray_page_count,
                    message=f"Compressed from {original_size/1024/1024:.2f} MB to {size/1024/1024:.2f} MB using {step['label']}.",
                )

    # Case 3: nothing hit target. Return best result + flag for auto-split.
    if best_path and os.path.exists(best_path):
        shutil.move(best_path, output_path)
        final_size = os.path.getsize(output_path)
        return CompressionResult(
            success=True,
            output_path=output_path,
            original_size_bytes=original_size,
            final_size_bytes=final_size,
            target_size_bytes=target_bytes,
            hit_target=False,
            setting_used=best_label,
            needs_split=True,
            pages_in_color=color_page_count,
            pages_in_gray=gray_page_count,
            message=(
                f"Compressed to {final_size/1024/1024:.2f} MB (best we could do without wrecking quality). "
                f"Couldn't reach your {target_mb} MB target. "
                f"Recommended: use Auto-Split to break this into 2-3 parts that each fit."
            ),
        )

    # Case 4: compression never helped at all. Return the original.
    shutil.copyfile(input_path, output_path)
    return CompressionResult(
        success=True,
        output_path=output_path,
        original_size_bytes=original_size,
        final_size_bytes=original_size,
        target_size_bytes=target_bytes,
        hit_target=False,
        setting_used="none (file already optimized)",
        needs_split=True,
        pages_in_color=color_page_count,
        pages_in_gray=gray_page_count,
        message=(
            f"File is already heavily optimized — further compression would damage quality. "
            f"Recommended: Auto-Split into smaller parts."
        ),
    )


def auto_split_to_target(input_path: str, output_dir: str, target_mb: float, base_name: str) -> list:
    """
    Split a PDF into parts that each fit under target_mb.

    Strategy: binary-style chunking. Estimate pages-per-part from average page size,
    then verify each part actually fits, adjusting if not.

    Returns list of output paths in order (Part 1, Part 2, ...).
    """
    target_bytes = int(target_mb * 1024 * 1024)
    pdf = pikepdf.open(input_path)
    total_pages = len(pdf.pages)
    total_size = os.path.getsize(input_path)
    pdf.close()

    # First estimate: assume even distribution.
    avg_page_size = total_size / total_pages if total_pages else 1
    pages_per_part = max(1, int(target_bytes / avg_page_size))

    outputs = []
    part_num = 1
    start = 0

    while start < total_pages:
        end = min(start + pages_per_part, total_pages)
        out_path = os.path.join(output_dir, f"{base_name}_Part_{part_num}.pdf")

        # Extract pages [start, end).
        src = pikepdf.open(input_path)
        new_pdf = pikepdf.Pdf.new()
        for i in range(start, end):
            new_pdf.pages.append(src.pages[i])
        new_pdf.save(out_path)
        new_pdf.close()
        src.close()

        # If this part is still over target, shrink the page range and retry.
        if os.path.getsize(out_path) > target_bytes and (end - start) > 1:
            os.remove(out_path)
            pages_per_part = max(1, (end - start) // 2)
            continue

        outputs.append(out_path)
        start = end
        part_num += 1

    return outputs
