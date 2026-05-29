"""
JimTools — All other PDF operations.
Compression lives in compress.py because it's the most complex piece.
Everything else lives here.
"""

import os
import zipfile
from datetime import datetime
from pathlib import Path

import pikepdf
import fitz  # PyMuPDF


# ============================================================================
# MERGE — combine multiple PDFs into one
# ============================================================================

def merge_pdfs(input_paths: list, output_path: str) -> dict:
    """Combine PDFs in the order given. Returns metadata about the result."""
    merged = pikepdf.Pdf.new()
    page_counts = []
    for path in input_paths:
        src = pikepdf.open(path)
        page_counts.append(len(src.pages))
        for page in src.pages:
            merged.pages.append(page)
        src.close()
    merged.save(output_path)
    merged.close()
    return {
        "success": True,
        "output_path": output_path,
        "total_pages": sum(page_counts),
        "files_merged": len(input_paths),
        "size_bytes": os.path.getsize(output_path),
    }


# ============================================================================
# SPLIT — extract page ranges
# ============================================================================

def split_pdf(input_path: str, ranges: list, output_dir: str, base_name: str) -> list:
    """
    Split a PDF by page ranges. Each range is a tuple (start, end), 1-indexed inclusive.
    Returns list of output file paths.
    Example: ranges=[(1,5), (6,10)] -> two PDFs, pages 1-5 and pages 6-10.
    """
    src = pikepdf.open(input_path)
    outputs = []
    for i, (start, end) in enumerate(ranges, start=1):
        out_path = os.path.join(output_dir, f"{base_name}_Part_{i}.pdf")
        new_pdf = pikepdf.Pdf.new()
        for page_idx in range(start - 1, min(end, len(src.pages))):
            new_pdf.pages.append(src.pages[page_idx])
        new_pdf.save(out_path)
        new_pdf.close()
        outputs.append(out_path)
    src.close()
    return outputs


def split_every_n_pages(input_path: str, n: int, output_dir: str, base_name: str) -> list:
    """Split into chunks of N pages each."""
    src = pikepdf.open(input_path)
    total = len(src.pages)
    src.close()
    ranges = []
    start = 1
    while start <= total:
        end = min(start + n - 1, total)
        ranges.append((start, end))
        start = end + 1
    return split_pdf(input_path, ranges, output_dir, base_name)


# ============================================================================
# ROTATE — rotate pages
# ============================================================================

def rotate_pdf(input_path: str, output_path: str, rotation: int, pages: list = None) -> dict:
    """
    Rotate pages by 90, 180, or 270 degrees.
    If pages is None, rotate all pages. Otherwise rotate only specified 1-indexed pages.
    """
    if rotation not in (90, 180, 270):
        raise ValueError("Rotation must be 90, 180, or 270")
    pdf = pikepdf.open(input_path)
    target_pages = pages if pages else range(1, len(pdf.pages) + 1)
    for page_num in target_pages:
        if 1 <= page_num <= len(pdf.pages):
            pdf.pages[page_num - 1].rotate(rotation, relative=True)
    pdf.save(output_path)
    pdf.close()
    return {"success": True, "output_path": output_path, "size_bytes": os.path.getsize(output_path)}


# ============================================================================
# EXTRACT / REORDER / DELETE pages
# ============================================================================

def extract_pages(input_path: str, output_path: str, page_numbers: list) -> dict:
    """Extract specific pages (1-indexed) in the order given."""
    src = pikepdf.open(input_path)
    new_pdf = pikepdf.Pdf.new()
    for page_num in page_numbers:
        if 1 <= page_num <= len(src.pages):
            new_pdf.pages.append(src.pages[page_num - 1])
    new_pdf.save(output_path)
    new_pdf.close()
    src.close()
    return {"success": True, "output_path": output_path, "pages_extracted": len(page_numbers)}


def delete_pages(input_path: str, output_path: str, page_numbers_to_delete: list) -> dict:
    """Delete specified pages, keep the rest."""
    src = pikepdf.open(input_path)
    keep = [i + 1 for i in range(len(src.pages)) if (i + 1) not in page_numbers_to_delete]
    src.close()
    return extract_pages(input_path, output_path, keep)


# ============================================================================
# PROTECT / UNLOCK — password handling
# ============================================================================

def protect_pdf(input_path: str, output_path: str, user_password: str, owner_password: str = None) -> dict:
    """Add a password to a PDF."""
    if not owner_password:
        owner_password = user_password
    pdf = pikepdf.open(input_path)
    pdf.save(
        output_path,
        encryption=pikepdf.Encryption(user=user_password, owner=owner_password, R=4),
    )
    pdf.close()
    return {"success": True, "output_path": output_path}


def unlock_pdf(input_path: str, output_path: str, password: str) -> dict:
    """Remove password from a PDF (requires knowing the password)."""
    try:
        pdf = pikepdf.open(input_path, password=password)
        pdf.save(output_path)
        pdf.close()
        return {"success": True, "output_path": output_path}
    except pikepdf.PasswordError:
        return {"success": False, "error": "Wrong password. We can't crack passwords — you need to know it."}


# ============================================================================
# WATERMARK — add text watermark
# ============================================================================

def add_text_watermark(
    input_path: str,
    output_path: str,
    text: str,
    position: str = "diagonal",  # 'diagonal', 'top', 'bottom', 'center', 'corner'
    opacity: float = 0.3,
    font_size: int = 50,
    color: tuple = (0.7, 0.7, 0.7),  # RGB 0-1
) -> dict:
    """
    Add a text watermark across all pages.

    Note on rotation: PyMuPDF's insert_text and insert_textbox only accept
    rotation in multiples of 90°. For arbitrary angles (45° diagonal), we use
    TextWriter combined with a rotation matrix via write_text.
    """
    import math
    doc = fitz.open(input_path)
    for page in doc:
        rect = page.rect
        if position == "diagonal":
            # Diagonal watermark using TextWriter + rotation matrix.
            # Trick: place the text at the page CENTER first, then rotate the
            # whole thing -45° around that same page center. This avoids the
            # text flying off the page that happens if you rotate around (0,0).
            cx, cy = rect.width / 2, rect.height / 2
            text_width = fitz.get_text_length(text, fontsize=font_size)
            tw = fitz.TextWriter(rect, color=color, opacity=opacity)
            tw.append(
                (cx - text_width / 2, cy + font_size / 3),
                text,
                fontsize=font_size,
            )
            mat = fitz.Matrix(-45)  # rotation only; pivot supplied below
            tw.write_text(page, morph=(fitz.Point(cx, cy), mat))
        elif position == "center":
            page.insert_textbox(rect, text, fontsize=font_size, color=color,
                               fill_opacity=opacity, align=fitz.TEXT_ALIGN_CENTER)
        elif position == "top":
            top_rect = fitz.Rect(rect.x0, rect.y0 + 20, rect.x1, rect.y0 + 60)
            page.insert_textbox(top_rect, text, fontsize=font_size, color=color,
                               fill_opacity=opacity, align=fitz.TEXT_ALIGN_CENTER)
        elif position == "bottom":
            bot_rect = fitz.Rect(rect.x0, rect.y1 - 60, rect.x1, rect.y1 - 20)
            page.insert_textbox(bot_rect, text, fontsize=font_size, color=color,
                               fill_opacity=opacity, align=fitz.TEXT_ALIGN_CENTER)
        elif position == "corner":
            corner_rect = fitz.Rect(rect.x1 - 200, rect.y0 + 20, rect.x1 - 20, rect.y0 + 60)
            page.insert_textbox(corner_rect, text, fontsize=font_size, color=color,
                               fill_opacity=opacity, align=fitz.TEXT_ALIGN_RIGHT)
    doc.save(output_path)
    doc.close()
    return {"success": True, "output_path": output_path}


# ============================================================================
# PAGE NUMBERS — add page numbers
# ============================================================================

def add_page_numbers(
    input_path: str,
    output_path: str,
    position: str = "bottom-center",  # bottom-center, bottom-right, top-center, top-right
    start_from: int = 1,
    format_str: str = "{page} of {total}",  # also: "{page}", "Page {page}"
) -> dict:
    """Add page numbers to every page."""
    doc = fitz.open(input_path)
    total = len(doc)
    for i, page in enumerate(doc):
        page_num = i + start_from
        text = format_str.format(page=page_num, total=total + start_from - 1)
        rect = page.rect
        if position == "bottom-center":
            box = fitz.Rect(rect.x0, rect.y1 - 30, rect.x1, rect.y1 - 10)
            align = fitz.TEXT_ALIGN_CENTER
        elif position == "bottom-right":
            box = fitz.Rect(rect.x1 - 100, rect.y1 - 30, rect.x1 - 20, rect.y1 - 10)
            align = fitz.TEXT_ALIGN_RIGHT
        elif position == "top-center":
            box = fitz.Rect(rect.x0, rect.y0 + 10, rect.x1, rect.y0 + 30)
            align = fitz.TEXT_ALIGN_CENTER
        else:  # top-right
            box = fitz.Rect(rect.x1 - 100, rect.y0 + 10, rect.x1 - 20, rect.y0 + 30)
            align = fitz.TEXT_ALIGN_RIGHT
        page.insert_textbox(box, text, fontsize=10, color=(0, 0, 0), align=align)
    doc.save(output_path)
    doc.close()
    return {"success": True, "output_path": output_path}


# ============================================================================
# PDF <-> IMAGES
# ============================================================================

def pdf_to_images(input_path: str, output_dir: str, base_name: str, dpi: int = 150, fmt: str = "jpg") -> list:
    """Render each page as an image. fmt is 'jpg' or 'png'."""
    doc = fitz.open(input_path)
    outputs = []
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(dpi=dpi)
        out_path = os.path.join(output_dir, f"{base_name}_page_{i:03d}.{fmt}")
        if fmt == "jpg":
            pix.pil_save(out_path, format="JPEG", quality=90)
        else:
            pix.save(out_path)
        outputs.append(out_path)
    doc.close()
    return outputs


def images_to_pdf(image_paths: list, output_path: str) -> dict:
    """Combine images into a single PDF, one image per page."""
    doc = fitz.open()
    for img_path in image_paths:
        try:
            img = fitz.open(img_path)
            pdf_bytes = img.convert_to_pdf()
            img.close()
            img_pdf = fitz.open("pdf", pdf_bytes)
            doc.insert_pdf(img_pdf)
            img_pdf.close()
        except Exception as e:
            raise ValueError(f"Could not convert {os.path.basename(img_path)}: {e}") from e
    doc.save(output_path)
    doc.close()
    return {"success": True, "output_path": output_path, "pages": len(image_paths)}


# ============================================================================
# FILE INSPECTOR — show everything about a PDF
# ============================================================================

def inspect_pdf_detailed(input_path: str) -> dict:
    """Detailed inspection — what's in this PDF?"""
    info = {
        "file_size_bytes": os.path.getsize(input_path),
        "file_size_mb": round(os.path.getsize(input_path) / 1024 / 1024, 2),
    }
    try:
        doc = fitz.open(input_path)
        info["page_count"] = len(doc)
        info["pdf_version"] = doc.pdf_version() if hasattr(doc, "pdf_version") else "unknown"
        info["is_encrypted"] = doc.is_encrypted
        info["metadata"] = doc.metadata or {}
        # Count images
        total_images = 0
        for page in doc:
            total_images += len(page.get_images())
        info["embedded_images"] = total_images
        # Fonts
        fonts = set()
        for page in doc:
            for f in page.get_fonts():
                fonts.add(f[3])  # font name
        info["fonts"] = sorted(fonts)
        # Has text layer
        has_text = any(p.get_text().strip() for p in doc)
        info["has_text_layer"] = has_text
        info["is_scanned"] = not has_text and total_images > 0
        doc.close()
    except Exception as e:
        info["error"] = str(e)
    return info


# ============================================================================
# GSL-SPECIFIC: BULK RENAMER
# ============================================================================

def bulk_rename_to_zip(
    files: list,  # list of (current_path, original_filename) tuples
    naming_pattern: dict,  # {'student_name': 'X', 'document_type': 'Y', 'date': 'Z', 'university': 'W'}
    output_zip_path: str,
) -> dict:
    """
    Rename a batch of files following a naming convention and return them as a ZIP.

    naming_pattern keys: student_name, document_type, date, university (any can be omitted)
    Pattern result: "{StudentName}_{DocumentType}_{Date}_{University}.pdf"

    For multi-file rename, each file should specify its own document_type
    (or files get auto-numbered as _001, _002, etc.)
    """
    parts = []
    if naming_pattern.get("student_name"):
        parts.append(naming_pattern["student_name"].strip().replace(" ", ""))
    if naming_pattern.get("document_type"):
        parts.append(naming_pattern["document_type"].strip().replace(" ", ""))
    if naming_pattern.get("date"):
        parts.append(naming_pattern["date"].strip().replace(" ", ""))
    if naming_pattern.get("university"):
        parts.append(naming_pattern["university"].strip().replace(" ", ""))

    if not parts:
        parts = ["Renamed"]

    base = "_".join(parts)
    renamed = []
    with zipfile.ZipFile(output_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, (current_path, original_filename) in enumerate(files, start=1):
            ext = os.path.splitext(original_filename)[1] or ".pdf"
            new_name = f"{base}_{i:03d}{ext}" if len(files) > 1 else f"{base}{ext}"
            zf.write(current_path, arcname=new_name)
            renamed.append({"original": original_filename, "new_name": new_name})
    return {"success": True, "output_path": output_zip_path, "renamed_files": renamed}


# ============================================================================
# GSL-SPECIFIC: DOCUMENT STAMPER
# ============================================================================

# ============================================================================
# FORM FILLER — fill PDF form fields
# ============================================================================

def fill_form_fields(input_path: str, output_path: str, fields_data: list) -> dict:
    """
    Fill PDF form fields. fields_data is a list of {"name": ..., "value": ...}.
    Handles Text, CheckBox, RadioButton, ComboBox, ListBox widgets.
    Returns {"filled": N, "skipped": N}.
    """
    import fitz
    doc = fitz.open(input_path)
    values = {item["name"]: item["value"] for item in fields_data}
    filled = 0
    skipped = 0
    for page in doc:
        for widget in page.widgets():
            name = widget.field_name
            if name not in values:
                continue
            val = values[name]
            t = widget.field_type_string
            if t == "Sig":
                skipped += 1
                continue
            try:
                if t == "CheckBox":
                    widget.field_value = bool(val) and val not in ("false", "False", "0", "Off", "")
                elif t in ("ComboBox", "ListBox"):
                    widget.field_value = val
                else:
                    widget.field_value = str(val)
                widget.update()
                filled += 1
            except Exception:
                skipped += 1
    doc.save(output_path, deflate=True, garbage=3)
    doc.close()
    return {"filled": filled, "skipped": skipped}


# ============================================================================
# ORGANIZE PAGES — reorder and/or delete pages visually
# ============================================================================

def organize_pages(input_path: str, output_path: str, page_indices: list) -> dict:
    """
    Create a new PDF containing only the pages at page_indices (0-based),
    in the order given.  Used by the Organize Pages tool.
    """
    import pikepdf
    with pikepdf.open(input_path) as src:
        total = len(src.pages)
        dst = pikepdf.Pdf.new()
        for idx in page_indices:
            if 0 <= idx < total:
                dst.pages.append(src.pages[idx])
        dst.save(output_path)
    size = os.path.getsize(output_path)
    return {"page_count": len(page_indices), "size_bytes": size}


# ============================================================================
# SIGN PDF — stamp a drawn/uploaded signature image onto page(s)
# ============================================================================

def stamp_signature(input_path: str, output_path: str, sig_bytes: bytes, placements: list) -> dict:
    """
    Place a signature image onto one or more pages of a PDF.
    placements: list of {page (1-indexed), rect: {x0,y0,x1,y1}} in absolute PDF points.
    app.py converts normalised coords → absolute before calling this function.
    sig_bytes: PNG/JPG bytes of the signature (transparent PNG preferred).
    """
    import fitz
    doc = fitz.open(input_path)
    count = 0
    for p in placements:
        page_idx = int(p.get("page", 1)) - 1
        if page_idx < 0 or page_idx >= len(doc):
            continue
        page = doc[page_idx]
        r = p.get("rect", {})
        x0 = float(r.get("x0", 0))
        y0 = float(r.get("y0", 0))
        x1 = float(r.get("x1", x0 + 100))
        y1 = float(r.get("y1", y0 + 50))
        # Clamp to page bounds
        pw, ph = page.rect.width, page.rect.height
        x0 = max(0, min(x0, pw))
        y0 = max(0, min(y0, ph))
        x1 = max(x0 + 10, min(x1, pw))
        y1 = max(y0 + 5, min(y1, ph))
        rect = fitz.Rect(x0, y0, x1, y1)
        page.insert_image(rect, stream=sig_bytes, keep_proportion=True, overlay=True)
        count += 1
    doc.save(output_path, deflate=True, garbage=3)
    doc.close()
    return {"placed": count}


STAMP_PRESETS = {
    "COPY": {"color": (0.7, 0.0, 0.0), "size": 70},
    "ORIGINAL": {"color": (0.0, 0.5, 0.0), "size": 70},
    "SUBMITTED": {"color": (0.0, 0.0, 0.7), "size": 60},
    "CONFIDENTIAL": {"color": (0.7, 0.0, 0.0), "size": 55},
    "VERIFIED": {"color": (0.0, 0.3, 0.6), "size": 50},
    "DRAFT": {"color": (0.5, 0.5, 0.5), "size": 70},
}


def stamp_document(
    input_path: str,
    output_path: str,
    stamp_text: str,  # one of STAMP_PRESETS or custom
    position: str = "diagonal",  # diagonal, corner, center
    opacity: float = 0.35,
) -> dict:
    """Apply a stamp preset (or custom text) across all pages."""
    preset = STAMP_PRESETS.get(stamp_text.upper(), {"color": (0.7, 0.0, 0.0), "size": 60})
    return add_text_watermark(
        input_path=input_path,
        output_path=output_path,
        text=stamp_text,
        position=position,
        opacity=opacity,
        font_size=preset["size"],
        color=preset["color"],
    )
