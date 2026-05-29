"""
JimTools — Document Converters
================================
Office <-> PDF conversions, OCR, and table extraction.
Completely separate from tools.py — adding/removing this file does not affect
any existing tool.

External dependencies (checked at runtime, never at import):
  - pdf2docx        (pip)  — PDF -> Word
  - pdfplumber      (pip)  — PDF -> Excel (table extraction)
  - openpyxl        (pip)  — writing .xlsx
  - LibreOffice  (system)  — Word/Excel/PPT -> PDF   (soffice --headless)
  - ocrmypdf        (pip)  — OCR (needs Tesseract + Ghostscript system installs)
"""

import os
import sys
import shutil
import subprocess
import tempfile
import uuid


# ============================================================================
# CAPABILITY DETECTION — each tool checks its own deps and degrades gracefully
# ============================================================================

def find_libreoffice():
    """Return the path to the LibreOffice/soffice executable, or None."""
    # Explicit common Windows locations first
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    # Fall back to PATH lookup
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return None


def have_pdf2docx():
    try:
        import pdf2docx  # noqa
        return True
    except Exception:
        return False


def have_pdfplumber():
    try:
        import pdfplumber  # noqa
        import openpyxl    # noqa
        return True
    except Exception:
        return False


def have_ocrmypdf():
    try:
        import ocrmypdf  # noqa
        return True
    except Exception:
        return False


def capabilities():
    """Return a dict describing which converters are available right now."""
    lo = find_libreoffice()
    return {
        "pdf_to_word":  have_pdf2docx(),
        "word_to_pdf":  lo is not None,
        "pdf_to_excel": have_pdfplumber(),
        "excel_to_pdf": lo is not None,
        "ocr":          have_ocrmypdf(),
        "libreoffice_path": lo,
    }


# ============================================================================
# PDF -> WORD  (pdf2docx)
# ============================================================================

def pdf_to_word(input_path: str, output_path: str, start=None, end=None) -> dict:
    """Convert a PDF to an editable .docx using pdf2docx."""
    if not have_pdf2docx():
        raise RuntimeError("PDF to Word needs the 'pdf2docx' library. Install with: pip install pdf2docx")
    from pdf2docx import Converter
    cv = Converter(input_path)
    try:
        # pdf2docx wants start=0 (not None); end=None means "to last page"
        cv.convert(output_path, start=(start or 0), end=end)
    finally:
        cv.close()
    if not os.path.exists(output_path):
        raise RuntimeError("Conversion produced no output — the PDF may be a scan with no text layer. Try OCR first.")
    return {"size_bytes": os.path.getsize(output_path)}


# ============================================================================
# PDF -> EXCEL  (pdfplumber tables -> openpyxl)
# ============================================================================

def pdf_to_excel(input_path: str, output_path: str) -> dict:
    """
    Extract tables from a PDF and write them to an .xlsx workbook.
    Each page's tables go onto their own sheet. Pages with no detectable
    table are skipped (but counted) so the user knows.
    """
    if not have_pdfplumber():
        raise RuntimeError("PDF to Excel needs 'pdfplumber' and 'openpyxl'. Install with: pip install pdfplumber openpyxl")
    import pdfplumber
    import openpyxl
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default sheet; we add our own

    tables_found = 0
    pages_with_tables = 0
    sheet_index = 0

    with pdfplumber.open(input_path) as pdf:
        total_pages = len(pdf.pages)
        for pidx, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            if not tables:
                continue
            pages_with_tables += 1
            for tidx, table in enumerate(tables):
                tables_found += 1
                sheet_index += 1
                title = f"P{pidx+1}" + (f"-T{tidx+1}" if len(tables) > 1 else "")
                ws = wb.create_sheet(title=title[:31])  # Excel 31-char sheet name limit
                for row in table:
                    # Replace None cells with empty string, normalise newlines
                    cleaned = [(c.replace("\n", " ").strip() if isinstance(c, str) else c) for c in row]
                    ws.append(cleaned)
                # Auto-width (capped)
                for col_cells in ws.columns:
                    width = max((len(str(c.value)) if c.value else 0) for c in col_cells)
                    ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(max(width + 2, 8), 60)

    if sheet_index == 0:
        # No tables anywhere — create an informational sheet so the file is valid
        ws = wb.create_sheet(title="No tables found")
        ws["A1"] = "No tables were detected in this PDF."
        ws["A2"] = "PDF to Excel works on documents that contain ruled/structured tables."
        ws["A3"] = "If this is a scanned document, run OCR first, then try again."

    wb.save(output_path)
    return {
        "size_bytes": os.path.getsize(output_path),
        "tables_found": tables_found,
        "pages_with_tables": pages_with_tables,
        "total_pages": total_pages,
    }


# ============================================================================
# OFFICE -> PDF  (LibreOffice headless)
# ============================================================================

def office_to_pdf(input_path: str, output_dir: str, output_path: str = None) -> dict:
    """
    Convert any LibreOffice-supported document (docx, doc, xlsx, xls, pptx,
    odt, rtf, csv, etc.) to PDF using a headless LibreOffice subprocess.
    If output_path is given, the final PDF is moved there (unique name, avoids collisions).
    Returns the path of the produced PDF.
    """
    soffice = find_libreoffice()
    if not soffice:
        raise RuntimeError(
            "Word/Excel to PDF needs LibreOffice installed. "
            "Download the free version from libreoffice.org, then restart JimTools."
        )

    os.makedirs(output_dir, exist_ok=True)

    # LibreOffice needs a writable user profile dir; give it a unique temp one
    # so concurrent conversions don't clash and so we don't touch the user's profile.
    # Use a unique temp dir for BOTH LibreOffice's user profile AND its output.
    # This prevents a race condition where two concurrent conversions of files with
    # the same basename (e.g. "report.docx") would produce the same output filename
    # in a shared output_dir, with the second overwriting or missing the first.
    work_dir = tempfile.mkdtemp(prefix="gsl_lo_")
    profile_uri = "file:///" + work_dir.replace("\\", "/").lstrip("/")

    args = [
        soffice,
        "--headless", "--norestore", "--nolockcheck", "--nodefault",
        f"-env:UserInstallation={profile_uri}",
        "--convert-to", "pdf",
        "--outdir", work_dir,   # isolated per-call dir, not shared OUTPUT_DIR
        input_path,
    ]

    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,  # 3 min ceiling for very large files
        )
        base = os.path.splitext(os.path.basename(input_path))[0]
        tmp_pdf = os.path.join(work_dir, base + ".pdf")
        if not os.path.exists(tmp_pdf):
            err = proc.stderr.decode(errors="ignore") or proc.stdout.decode(errors="ignore")
            raise RuntimeError(f"LibreOffice could not convert this file. Details: {err[:300]}")
        # Move to the caller-supplied output_dir now that we know it succeeded
        os.makedirs(output_dir, exist_ok=True)
        out_pdf = os.path.join(output_dir, base + ".pdf")
        shutil.move(tmp_pdf, out_pdf)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Conversion timed out (file too large or LibreOffice is busy). Try a smaller file.")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # If caller wants a specific unique output path (avoids filename collisions), honour it
    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        shutil.move(out_pdf, output_path)
        out_pdf = output_path
    return {"output_path": out_pdf, "size_bytes": os.path.getsize(out_pdf)}


# ============================================================================
# OCR  (ocrmypdf — adds searchable text layer to scanned PDFs)
# ============================================================================

def ocr_pdf(input_path: str, output_path: str, language: str = "eng", force: bool = False) -> dict:
    """
    Add an OCR text layer to a (usually scanned) PDF, making it searchable
    and selectable. Uses ocrmypdf (Tesseract under the hood).
    language: Tesseract language code, e.g. 'eng', 'ben', 'eng+ben'.
    force: if True, OCR even pages that already have text (re-OCR).
    """
    if not have_ocrmypdf():
        raise RuntimeError(
            "OCR needs 'ocrmypdf' plus Tesseract + Ghostscript. "
            "Install with: pip install ocrmypdf  (and install Tesseract-OCR for Windows)."
        )
    import ocrmypdf

    kwargs = {
        "language": language,
        "deskew": True,             # straighten skewed scans
        "rotate_pages": True,       # auto-fix page orientation
        "optimize": 1,              # light optimisation
        "progress_bar": False,
    }
    if force:
        kwargs["force_ocr"] = True   # re-OCR everything
    else:
        kwargs["skip_text"] = True   # skip pages that already have a text layer

    try:
        ocrmypdf.ocr(input_path, output_path, **kwargs)
    except ocrmypdf.exceptions.PriorOcrFoundError:
        raise RuntimeError("This PDF already has searchable text. Use 'Force re-OCR' if you want to redo it.")
    except Exception as e:
        raise RuntimeError(f"OCR failed: {e}")

    if not os.path.exists(output_path):
        raise RuntimeError("OCR produced no output.")
    return {"size_bytes": os.path.getsize(output_path), "language": language}


# ============================================================================
# TESSERACT LANGUAGE DETECTION (for the OCR tool's language dropdown)
# ============================================================================

def available_ocr_languages():
    """Return the list of installed Tesseract languages, or a sensible default."""
    try:
        import ocrmypdf  # ensures tesseract is wired
        out = subprocess.run(["tesseract", "--list-langs"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        lines = out.stdout.decode(errors="ignore").splitlines()
        langs = [l.strip() for l in lines[1:] if l.strip()]  # first line is a header
        return langs or ["eng"]
    except Exception:
        return ["eng"]
