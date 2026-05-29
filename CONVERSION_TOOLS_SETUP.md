# JimTools v8 — New Conversion Tools Setup

v8 adds 5 conversion tools. **Everything that already worked is unchanged** — these are pure additions.

## New tools

| Tool | What it does | Needs |
|------|--------------|-------|
| PDF → Word | PDF to editable .docx | Python library (auto) |
| Word → PDF | .docx/.doc/.odt/.rtf to PDF | LibreOffice |
| PDF → Excel | Extract tables to .xlsx | Python library (auto) |
| Excel → PDF | .xlsx/.csv/.ods to PDF | LibreOffice |
| OCR PDF | Make scans searchable | Python library + Tesseract |

Each tool checks its own requirements and shows a yellow "needs setup" banner with instructions if something is missing — the rest of JimTools keeps working regardless.

---

## Step 1 — Install the Python libraries

Open Command Prompt and run:

```
pip install pdf2docx pdfplumber openpyxl ocrmypdf
```

This covers PDF→Word, PDF→Excel, and the OCR engine wrapper. Takes 2–3 minutes.

## Step 2 — Install LibreOffice (for Word→PDF and Excel→PDF)

These two use LibreOffice's converter — the same engine that powers most online tools.

1. Go to **https://www.libreoffice.org/download/download/**
2. Download the Windows installer, run it, accept defaults
3. Restart JimTools (`python app.py`)

JimTools finds LibreOffice automatically in the standard install location. No PATH setup needed.

## Step 3 — Install Tesseract (for OCR only)

OCR needs the Tesseract engine plus Ghostscript (you already have Ghostscript from the Compress tool).

1. Go to **https://github.com/UB-Mannheim/tesseract/wiki**
2. Download the Windows installer (64-bit)
3. During install, **tick the extra language packs** you need (e.g. Bengali) under "Additional language data"
4. Accept defaults otherwise
5. Restart JimTools

The OCR tool's language dropdown auto-fills with whatever languages Tesseract has installed.

---

## What works without any extra setup

PDF → Word and PDF → Excel work as soon as the `pip install` in Step 1 finishes — no LibreOffice or Tesseract needed for those two.

Word → PDF / Excel → PDF need LibreOffice (Step 2).
OCR needs Tesseract (Step 3).

---

## File hand-off between tools

Every converter plugs into the existing "Continue to…" system. Convert a Word file to PDF, and the result is pre-loaded into Compress, Organize, Sign, etc. with one click — no re-uploading.

---

## Notes on quality

- **PDF → Word** works best on text-based PDFs. For scanned documents, run **OCR PDF** first, then PDF → Word.
- **PDF → Excel** extracts ruled/structured tables. If a PDF has no detectable table, you'll get a workbook with a "No tables found" note rather than an error.
- **Word/Excel → PDF** fidelity depends on fonts installed on the server PC; LibreOffice substitutes missing fonts, which can slightly shift layout.
