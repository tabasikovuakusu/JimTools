# JimTools — Private Offline PDF Workstation

A self-hosted PDF workstation that runs on your own machine. No cloud uploads. No file size limits. No ads. No daily caps. Everything stays private.

Runs on one PC, accessible from every other machine on the same network through a browser.

> Built by **Zunaed Zaman Jim** &nbsp;·&nbsp; Built with [Claude](https://claude.ai) (Anthropic)

---

## What it does

### Workflow tools
| Tool | Description |
|------|-------------|
| Smart Compress | Target-size compression — Canada 5MB, NZ 10MB, UK 6MB, AU 10MB, or custom |
| Auto-Split to Fit | Splits a file into portal-sized parts when compression can't hit the target |
| Organize Pages | Multi-file visual page organizer — drag, rotate, delete, reorder across PDFs |
| Fill PDF Form | Visual form filler — click fields on the rendered page and type |
| Sign PDF | Draw or upload a signature, place it anywhere, transparent PNG support |
| Document Stamper | COPY / ORIGINAL / SUBMITTED / VERIFIED / DRAFT stamps |
| Bulk Renamer | Batch rename documents with consistent naming conventions |
| File Inspector | Size, pages, fonts, metadata, encryption status |

### PDF essentials
Merge · Split · Rotate · Watermark · Page Numbers · Protect · Unlock · PDF ↔ Images

### Convert
| Tool | Description |
|------|-------------|
| PDF → Word | Editable .docx via pdf2docx (best open-source accuracy) |
| Word → PDF | Via LibreOffice headless |
| PDF → Excel | Extract tables into real .xlsx spreadsheet cells |
| Excel → PDF | Via LibreOffice headless |
| OCR PDF | Make scanned PDFs searchable — tested more accurate than iLovePDF on non-Latin scripts |

---

## How it compares

| | Online tools (iLovePDF, Smallpdf, Sejda) | JimTools |
|---|---|---|
| Privacy | Files go to external servers | Never leaves your machine |
| File size limit | 100–200MB (free tier) | 200MB (adjustable) |
| Daily task limit | Yes | None |
| Ads | Yes | None |
| Cost | Free / subscription | Free, self-hosted |
| Offline | No | Yes |
| Tool chaining | Re-upload between steps | One-click pass-through |

---

## Requirements

- Python 3.10+
- Ghostscript — required for Smart Compress
- LibreOffice — optional, for Word/Excel ↔ PDF
- Tesseract OCR — optional, for OCR PDF

---

## Installation

```bash
# 1. Clone
git clone https://github.com/ZunaedZamanJim/JimTools.git
cd JimTools

# 2. Install Python libraries
pip install flask pikepdf pymupdf pdf2docx pdfplumber openpyxl ocrmypdf

# 3. Install Ghostscript from ghostscript.com

# 4. (Optional) Install LibreOffice from libreoffice.org

# 5. (Optional) Install Tesseract from github.com/UB-Mannheim/tesseract/wiki
#    Add extra languages e.g. Bengali:
#    curl -L -o tessdata/ben.traineddata https://github.com/tesseract-ocr/tessdata/raw/main/ben.traineddata

# 6. Start
python app.py
```

Open `http://localhost:5000` — you'll be prompted to create a password on first run.

### LAN access

Find your IP (`ipconfig` on Windows, `ifconfig` on Linux/macOS), open port 5000 in your firewall.  
Other machines on the same network open `http://YOUR-IP:5000`.

---

## Configuration

**Change password:**  
Delete the `.password` file and restart (triggers first-run setup again), or set the `JIMTOOLS_PASSWORD` environment variable (takes priority).

**Adjust limits in `app.py`:**
```python
MAX_UPLOAD_MB = 200          # max upload size
CLEANUP_AFTER_MINUTES = 60   # auto-delete processed files
```

**Auto-start on Windows:** create `start_silent.bat`:
```bat
@echo off
cd /d C:\JimTools
start /min python app.py
```
Press Win+R → `shell:startup` → place the file there.

---

## Project structure

```
app.py                       Flask routes
jimtools_app/
  services/
    compress.py              Smart Compress engine (Ghostscript)
    tools.py                 Core PDF operations
    converters.py            Word/Excel/OCR conversions
  templates/
    base.html                Layout, CSS, tool-chaining system
    home.html                Tool grid
    tool_*.html              One template per tool
```

Adding a tool: function in `tools.py` → route in `app.py` → template → card in `home.html`.

---

## Security

- Designed for **LAN use behind your own network** — do not expose to the internet
- No rate limiting or brute-force protection
- Files auto-delete after 60 minutes
- Files are not encrypted at rest during processing

---

## License

MIT License — see [LICENSE](LICENSE)  
Copyright (c) 2026 Zunaed Zaman Jim

---

## Credits

Built by **Zunaed Zaman Jim**  
Built with [Claude](https://claude.ai) (Anthropic)

Dependencies: Flask · PyMuPDF · pikepdf · pdf2docx · pdfplumber · openpyxl · ocrmypdf  
External: Ghostscript · LibreOffice · Tesseract OCR
