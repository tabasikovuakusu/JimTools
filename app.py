"""
JimTools — Flask web app (v5 — Fixed & Complete)
==================================================
Run with: python app.py
Then open: http://localhost:5000 (this machine) or http://<machine-ip>:5000 (other office PCs)

Default password:   (set JIMTOOLS_PASSWORD env var to change)
"""

import os, sys, secrets, threading, time, uuid
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for, flash, abort
from werkzeug.utils import secure_filename

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jimtools_app.services.compress import compress_to_target, auto_split_to_target, find_ghostscript
from jimtools_app.services import tools
from jimtools_app.services import converters

# ── CONFIG ────────────────────────────────────────────────────────────────────
# ── FIRST-RUN PASSWORD SETUP ─────────────────────────────────────────────────
_PASSWORD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".password")

def _load_password():
    env_pw = os.environ.get("JIMTOOLS_PASSWORD", "")
    if env_pw: return env_pw
    if os.path.exists(_PASSWORD_FILE):
        pw = open(_PASSWORD_FILE).read().strip()
        if pw: return pw
    return ""

def _save_password(pw: str):
    open(_PASSWORD_FILE, "w").write(pw)

APP_PASSWORD = _load_password()
MAX_UPLOAD_MB      = 200
UPLOAD_DIR         = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
OUTPUT_DIR         = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
CLEANUP_AFTER_MINUTES = 60

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__, template_folder="jimtools_app/templates", static_folder="jimtools_app/static")
app.config["MAX_CONTENT_LENGTH"]    = MAX_UPLOAD_MB * 1024 * 1024
app.config["MAX_FORM_MEMORY_SIZE"]  = MAX_UPLOAD_MB * 1024 * 1024  # fix 413 on base64 form fields
app.config["MAX_FORM_PARTS"]        = 10000
app.secret_key = secrets.token_hex(32)

# ── ROBUST TEMP FILE REGISTRY ─────────────────────────────────────────────────
_temp_storage = {}
_temp_lock    = threading.Lock()

def save_temp_meta(file_key, file_path, meta=None):
    with _temp_lock:
        _temp_storage[file_key] = {"path": file_path, "meta": meta or {}, "created": time.time()}

def get_temp_meta(file_key):
    with _temp_lock:
        entry = _temp_storage.get(file_key)
        if entry is None: return None
        if not os.path.exists(entry["path"]):
            _temp_storage.pop(file_key, None); return None
        return entry

def delete_temp_meta(file_key, delete_file=True):
    with _temp_lock:
        entry = _temp_storage.pop(file_key, None)
    if delete_file and entry and os.path.exists(entry["path"]):
        try: os.remove(entry["path"])
        except: pass

def cleanup_temp_storage():
    cutoff = time.time() - (CLEANUP_AFTER_MINUTES * 60)
    with _temp_lock:
        expired = [k for k, v in _temp_storage.items() if v.get("created", 0) < cutoff]
        for k in expired:
            entry = _temp_storage.pop(k, None)
            if entry and os.path.exists(entry["path"]):
                try: os.remove(entry["path"])
                except: pass

# ── HELPERS ───────────────────────────────────────────────────────────────────
def require_login():
    if not APP_PASSWORD: return redirect(url_for("setup"))
    if not session.get("logged_in"): return redirect(url_for("login"))
    return None

def safe_save_upload(file_storage, prefix="upload"):
    if not file_storage or not file_storage.filename: return None
    original  = secure_filename(file_storage.filename)
    saved_path = os.path.join(UPLOAD_DIR, f"{prefix}_{uuid.uuid4().hex[:8]}_{original}")
    file_storage.save(saved_path)
    return saved_path

def unique_output_path(suggested_name):
    return os.path.join(OUTPUT_DIR, f"{uuid.uuid4().hex[:8]}_{secure_filename(suggested_name)}")

# ── FILE PASSTHROUGH HELPER ──────────────────────────────────────────────────
def resolve_upload(request, pass_key_field="pass_key", file_field="file", prefix="upload"):
    """Resolve an upload from either a pass_key (staged server file) or a fresh file upload."""
    pk = request.form.get(pass_key_field, "")
    if pk:
        entry = get_temp_meta(pk)
        if entry:
            return entry["path"], True, entry["meta"].get("original_name","file.pdf")
    f = request.files.get(file_field)
    if f and f.filename:
        return safe_save_upload(f, prefix=prefix), False, secure_filename(f.filename)
    return None, False, None

# ── AUTH ──────────────────────────────────────────────────────────────────────
@app.route("/setup", methods=["GET","POST"])
def setup():
    global APP_PASSWORD
    if APP_PASSWORD:
        return redirect(url_for("login"))
    if request.method == "POST":
        pw1 = request.form.get("password","").strip()
        pw2 = request.form.get("confirm","").strip()
        if len(pw1) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif pw1 != pw2:
            flash("Passwords do not match.", "error")
        else:
            _save_password(pw1)
            APP_PASSWORD = pw1
            session["logged_in"] = True; session.permanent = True
            flash("Password set. Welcome to JimTools.", "success")
            return redirect(url_for("home"))
    return render_template("setup.html")


@app.route("/login", methods=["GET","POST"])
def login():
    global APP_PASSWORD
    APP_PASSWORD = _load_password()
    if not APP_PASSWORD:
        return redirect(url_for("setup"))
    if request.method == "POST":
        if request.form.get("password","") == APP_PASSWORD:
            session["logged_in"] = True; session.permanent = True
            return redirect(url_for("home"))
        flash("Wrong password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/")
def home():
    if not session.get("logged_in"): return redirect(url_for("login"))
    return render_template("home.html")

# ── COMPRESS ──────────────────────────────────────────────────────────────────
@app.route("/tool/compress", methods=["GET","POST"])
def tool_compress():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_compress.html")
    preset = request.form.get("preset","custom")
    target_mb = {"canada":5.0,"newzealand":10.0,"australia":10.0,"uk":6.0}.get(preset)
    if not target_mb:
        try: target_mb = float(request.form.get("custom_mb","5"))
        except: return jsonify({"error":"Invalid custom size"}),400
    # Support pass_key from previous tool (e.g. Organize → Compress)
    pk = request.form.get("pass_key","")
    if pk:
        entry = get_temp_meta(pk)
        if entry:
            files_to_compress = [(entry["path"], entry["meta"].get("original_name","file.pdf"), False)]
        else:
            return jsonify({"error":"Staged file expired — please re-upload"}),400
    else:
        raw = request.files.getlist("files")
        files_to_compress = [(safe_save_upload(f,prefix="compress"), secure_filename(f.filename), True) for f in raw if f and f.filename]
    if not files_to_compress: return jsonify({"error":"No files uploaded"}),400
    results = []
    for in_path, fname, should_delete in files_to_compress:
        if not in_path: continue
        f_name = fname
        out_path = unique_output_path(f"compressed_{os.path.basename(f_name)}")
        try:
            r = compress_to_target(in_path, out_path, target_mb)
            results.append({"original_name":os.path.basename(f_name),"download_url":url_for("download",path=os.path.basename(r.output_path)),"original_mb":round(r.original_size_bytes/1024/1024,2),"final_mb":round(r.final_size_bytes/1024/1024,2),"target_mb":target_mb,"hit_target":r.hit_target,"needs_split":r.needs_split,"setting_used":r.setting_used,"message":r.message,"color_pages":r.pages_in_color,"gray_pages":r.pages_in_gray,"saved_pct":round((1-r.final_size_bytes/r.original_size_bytes)*100,1) if r.original_size_bytes else 0})
        except Exception as e: results.append({"original_name":os.path.basename(f_name),"error":str(e)})
        finally:
            if should_delete and os.path.exists(in_path): os.remove(in_path)
    return jsonify({"results":results})

# ── AUTO-SPLIT ────────────────────────────────────────────────────────────────
@app.route("/tool/auto-split", methods=["GET","POST"])
def tool_auto_split():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_auto_split.html")
    try: target_mb = float(request.form.get("target_mb","5"))
    except: return jsonify({"error":"Invalid target"}),400
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}),400
    in_path = safe_save_upload(f, prefix="split")
    base = os.path.splitext(secure_filename(f.filename))[0]
    try:
        outputs = auto_split_to_target(in_path, OUTPUT_DIR, target_mb, f"{uuid.uuid4().hex[:8]}_{base}")
        parts = [{"name":f"{base}_Part_{i+1}.pdf","size_mb":round(os.path.getsize(p)/1024/1024,2),"download_url":url_for("download",path=os.path.basename(p))} for i,p in enumerate(outputs)]
        return jsonify({"parts":parts,"total_parts":len(parts)})
    finally:
        if os.path.exists(in_path): os.remove(in_path)

# ── MERGE ─────────────────────────────────────────────────────────────────────
@app.route("/tool/merge", methods=["GET","POST"])
def tool_merge():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_merge.html")
    files = request.files.getlist("files")
    if len(files) < 2: return jsonify({"error":"Upload at least 2 PDFs"}),400
    paths = [p for p in [safe_save_upload(f,prefix="merge") for f in files] if p]
    out_path = unique_output_path("merged.pdf")
    try:
        r = tools.merge_pdfs(paths, out_path)
        # Register as pass-file for downstream tools
        import shutil as _sh
        pass_key = uuid.uuid4().hex[:12]
        pass_path = os.path.join(UPLOAD_DIR, f"pass_{pass_key}_merged.pdf")
        _sh.copy2(out_path, pass_path)
        save_temp_meta(pass_key, pass_path, {"original_name": "merged.pdf"})
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path)),"total_pages":r["total_pages"],"files_merged":r["files_merged"],"size_mb":round(r["size_bytes"]/1024/1024,2),"pass_key":pass_key})
    finally:
        for p in paths:
            if os.path.exists(p): os.remove(p)

# ── SPLIT ─────────────────────────────────────────────────────────────────────
@app.route("/tool/split", methods=["GET","POST"])
def tool_split():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_split.html")
    mode = request.form.get("mode","ranges")
    in_path, is_pass, orig_name = resolve_upload(request, file_field="file", prefix="split")
    if not in_path: return jsonify({"error":"No file uploaded"}),400
    base = os.path.splitext(secure_filename(orig_name))[0]
    try:
        if mode == "every_n":
            n = int(request.form.get("every_n","10"))
            outputs = tools.split_every_n_pages(in_path, n, OUTPUT_DIR, f"{uuid.uuid4().hex[:8]}_{base}")
        else:
            raw = request.form.get("ranges","")
            ranges = []
            for chunk in raw.split(","):
                chunk = chunk.strip()
                parts = chunk.split("-")
                if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                    ranges.append((int(parts[0]), int(parts[1])))
            if not ranges: return jsonify({"error":"No valid ranges. Example: 1-5,6-10"}),400
            outputs = tools.split_pdf(in_path, ranges, OUTPUT_DIR, f"{uuid.uuid4().hex[:8]}_{base}")
        parts = [{"name":os.path.basename(p),"size_mb":round(os.path.getsize(p)/1024/1024,2),"download_url":url_for("download",path=os.path.basename(p))} for p in outputs]
        return jsonify({"parts":parts})
    finally:
        if os.path.exists(in_path): os.remove(in_path)

# ── ROTATE ────────────────────────────────────────────────────────────────────
@app.route("/tool/rotate", methods=["GET","POST"])
def tool_rotate():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_rotate.html")
    in_path, is_pass, orig_name = resolve_upload(request, file_field="file", prefix="rotate")
    if not in_path: return jsonify({"error":"No file uploaded"}),400
    out_path = unique_output_path(f"rotated_{secure_filename(orig_name)}")
    try:
        tools.rotate_pdf(in_path, out_path, int(request.form.get("rotation","90")))
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path))})
    finally:
        if os.path.exists(in_path): os.remove(in_path)

# ── PROTECT / UNLOCK ──────────────────────────────────────────────────────────
@app.route("/tool/protect", methods=["GET","POST"])
def tool_protect():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_protect.html")
    pw = request.form.get("password","").strip()
    if not pw: return jsonify({"error":"Password required"}),400
    in_path, is_pass, orig_name = resolve_upload(request, file_field="file", prefix="protect")
    if not in_path: return jsonify({"error":"No file uploaded"}),400
    out_path = unique_output_path(f"protected_{secure_filename(orig_name)}")
    try: tools.protect_pdf(in_path, out_path, pw); return jsonify({"download_url":url_for("download",path=os.path.basename(out_path))})
    finally:
        if os.path.exists(in_path): os.remove(in_path)

@app.route("/tool/unlock", methods=["GET","POST"])
def tool_unlock():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_unlock.html")
    f  = request.files.get("file"); pw = request.form.get("password","")
    if not f or not pw: return jsonify({"error":"File and password required"}),400
    in_path = safe_save_upload(f,prefix="unlock"); out_path = unique_output_path(f"unlocked_{secure_filename(f.filename)}")
    try:
        r = tools.unlock_pdf(in_path, out_path, pw)
        if not r["success"]: return jsonify({"error":r["error"]}),400
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path))})
    finally:
        if os.path.exists(in_path): os.remove(in_path)

# ── WATERMARK ─────────────────────────────────────────────────────────────────
@app.route("/tool/watermark", methods=["GET","POST"])
def tool_watermark():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_watermark.html")
    text = request.form.get("text","").strip()
    if not text: return jsonify({"error":"Watermark text required"}),400
    in_path, is_pass, orig_name = resolve_upload(request, file_field="file", prefix="wm")
    if not in_path: return jsonify({"error":"No file uploaded"}),400
    out_path = unique_output_path(f"watermarked_{secure_filename(orig_name)}")
    try:
        tools.add_text_watermark(in_path, out_path, text, position=request.form.get("position","diagonal"), opacity=float(request.form.get("opacity","0.3")))
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path))})
    finally:
        if os.path.exists(in_path): os.remove(in_path)

# ── PAGE NUMBERS ──────────────────────────────────────────────────────────────
@app.route("/tool/page-numbers", methods=["GET","POST"])
def tool_page_numbers():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_page_numbers.html")
    in_path, is_pass, orig_name = resolve_upload(request, file_field="file", prefix="pgnum")
    if not in_path: return jsonify({"error":"No file uploaded"}),400
    out_path = unique_output_path(f"numbered_{secure_filename(orig_name)}")
    try:
        tools.add_page_numbers(in_path, out_path, position=request.form.get("position","bottom-center"), format_str=request.form.get("format","{page} of {total}"))
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path))})
    finally:
        if os.path.exists(in_path): os.remove(in_path)

# ── PDF ↔ IMAGES ──────────────────────────────────────────────────────────────
@app.route("/tool/pdf-to-images", methods=["GET","POST"])
def tool_pdf_to_images():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_pdf_to_images.html")
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}),400
    in_path = safe_save_upload(f,prefix="p2i"); base = os.path.splitext(secure_filename(f.filename))[0]
    try:
        outputs = tools.pdf_to_images(in_path, OUTPUT_DIR, f"{uuid.uuid4().hex[:8]}_{base}", dpi=int(request.form.get("dpi","150")), fmt=request.form.get("format","jpg"))
        import zipfile
        zip_path = unique_output_path(f"{base}_images.zip")
        with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as zf:
            for p in outputs: zf.write(p, arcname=os.path.basename(p)); os.remove(p)
        return jsonify({"download_url":url_for("download",path=os.path.basename(zip_path)),"page_count":len(outputs)})
    finally:
        if os.path.exists(in_path): os.remove(in_path)

@app.route("/tool/images-to-pdf", methods=["GET","POST"])
def tool_images_to_pdf():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_images_to_pdf.html")
    files = request.files.getlist("files")
    if not files: return jsonify({"error":"No images"}),400
    paths = [p for p in [safe_save_upload(f,prefix="i2p") for f in files] if p]
    out_path = unique_output_path("combined.pdf")
    try:
        tools.images_to_pdf(paths, out_path)
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path)),"page_count":len(paths)})
    finally:
        for p in paths:
            if os.path.exists(p): os.remove(p)

# ── FILE INSPECTOR ────────────────────────────────────────────────────────────
@app.route("/tool/inspect", methods=["GET","POST"])
def tool_inspect():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_inspect.html")
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}),400
    in_path = safe_save_upload(f,prefix="inspect")
    try: return jsonify(tools.inspect_pdf_detailed(in_path))
    finally:
        if os.path.exists(in_path): os.remove(in_path)

# ── BULK RENAMER ──────────────────────────────────────────────────────────────
@app.route("/tool/bulk-rename", methods=["GET","POST"])
def tool_bulk_rename():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_bulk_rename.html")
    files = request.files.getlist("files")
    if not files: return jsonify({"error":"No files"}),400
    pattern = {"student_name":request.form.get("student_name","").strip(),"document_type":request.form.get("document_type","").strip(),"date":request.form.get("date","").strip(),"university":request.form.get("university","").strip()}
    saved = [(safe_save_upload(f,prefix="rename"), f.filename) for f in files]
    saved = [s for s in saved if s[0]]
    zip_path = unique_output_path("renamed_files.zip")
    try:
        r = tools.bulk_rename_to_zip(saved, pattern, zip_path)
        return jsonify({"download_url":url_for("download",path=os.path.basename(zip_path)),"renamed_files":r["renamed_files"]})
    finally:
        for p,_ in saved:
            if os.path.exists(p): os.remove(p)

# ── DOCUMENT STAMPER ──────────────────────────────────────────────────────────
@app.route("/tool/stamp", methods=["GET","POST"])
def tool_stamp():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_stamp.html", presets=list(tools.STAMP_PRESETS.keys()))
    in_path, is_pass, orig_name = resolve_upload(request, file_field="file", prefix="stamp")
    if not in_path: return jsonify({"error":"No file uploaded"}),400
    out_path = unique_output_path(f"stamped_{secure_filename(orig_name)}")
    try:
        tools.stamp_document(in_path, out_path, request.form.get("stamp_text","COPY"), position=request.form.get("position","diagonal"), opacity=float(request.form.get("opacity","0.35")))
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path))})
    finally:
        if os.path.exists(in_path): os.remove(in_path)

# ── API: PAGE THUMBNAILS ──────────────────────────────────────────────────────
@app.route("/api/thumbnails", methods=["POST"])
def api_thumbnails():
    redir = require_login()
    if redir: return redir
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}),400
    file_key  = uuid.uuid4().hex[:12]
    saved_path = os.path.join(UPLOAD_DIR, f"thumb_{file_key}_{secure_filename(f.filename)}")
    f.save(saved_path)
    try:
        import fitz, base64
        doc = fitz.open(saved_path)
        mat = fitz.Matrix(0.22, 0.22)
        thumbnails = [{"page":i+1,"data":base64.b64encode(page.get_pixmap(matrix=mat).tobytes("jpeg")).decode()} for i,page in enumerate(doc)]
        doc.close()
        save_temp_meta(file_key, saved_path)
        return jsonify({"file_key":file_key,"file_name":secure_filename(f.filename),"total_pages":len(thumbnails),"thumbnails":thumbnails})
    except Exception as e:
        if os.path.exists(saved_path): os.remove(saved_path)
        return jsonify({"error":str(e)}),500

# ── API: FORM FIELDS ──────────────────────────────────────────────────────────
@app.route("/api/form-fields", methods=["POST"])
def api_form_fields():
    redir = require_login()
    if redir: return redir
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}),400
    file_key   = uuid.uuid4().hex[:12]
    saved_path = os.path.join(UPLOAD_DIR, f"form_{file_key}_{secure_filename(f.filename)}")
    f.save(saved_path)
    try:
        import fitz, base64
        doc = fitz.open(saved_path)
        pages_data = []; total_fields = 0; page_dims = []
        for page_num, page in enumerate(doc):
            pw, ph = page.rect.width, page.rect.height
            page_dims.append({"width": pw, "height": ph})
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5,1.5))
            fields = []
            for widget in page.widgets():
                rect = widget.rect
                fields.append({"name":widget.field_name,"value":widget.field_value or "","type":widget.field_type_string,"choices":list(widget.choice_values) if hasattr(widget,"choice_values") and widget.choice_values else [],"rect":{"x0":round(rect.x0/pw,5),"y0":round(rect.y0/ph,5),"x1":round(rect.x1/pw,5),"y1":round(rect.y1/ph,5)}})
                total_fields += 1
            pages_data.append({"page":page_num+1,"img":base64.b64encode(pix.tobytes("jpeg")).decode(),"fields":fields})
        doc.close()
        # Store page dims so form-fill route can convert sig coords to PDF points
        save_temp_meta(file_key, saved_path, {"page_dims": page_dims})
        return jsonify({"file_key":file_key,"file_name":secure_filename(f.filename),"total_pages":len(pages_data),"total_fields":total_fields,"pages":pages_data})
    except Exception as e:
        if os.path.exists(saved_path): os.remove(saved_path)
        return jsonify({"error":str(e)}),500

# ── FORM FILLER ───────────────────────────────────────────────────────────────
@app.route("/tool/form-fill", methods=["GET","POST"])
def tool_form_fill():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_form_fill.html")
    import json as _json, base64 as _b64
    file_key        = request.form.get("file_key","")
    fields_data     = _json.loads(request.form.get("fields","[]"))
    sig_placements  = _json.loads(request.form.get("sig_placements","[]"))
    # Signature arrives as a file blob (avoids 413 from MAX_FORM_MEMORY_SIZE on base64 strings)
    sig_file        = request.files.get("sig_file")
    sig_b64         = ""  # legacy fallback
    entry = get_temp_meta(file_key)
    if not entry: return jsonify({"error":"Session expired — please re-upload the file"}),400
    saved_file = entry["path"]; page_dims = entry["meta"].get("page_dims",[])
    out_path = unique_output_path("filled_form.pdf")
    try:
        result = tools.fill_form_fields(saved_file, out_path, fields_data)
        if not os.path.exists(out_path): return jsonify({"error":"Fill produced no output — PDF may be locked or XFA-based"}),500
        sig_placed = 0
        if sig_placements and (sig_file or (sig_b64 and sig_b64.startswith("data:"))):
            sig_bytes = sig_file.read() if sig_file else _b64.b64decode(sig_b64.split(",",1)[1])
            abs_placements = []
            for p in sig_placements:
                page_num = p.get("page",1); pi = page_num - 1
                if pi < 0 or pi >= len(page_dims): continue
                dim = page_dims[pi]; pdf_w,pdf_h = dim["width"],dim["height"]
                norm = p.get("norm",{})
                nx,ny,nw,nh = norm.get("x",0),norm.get("y",0),norm.get("w",0.1),norm.get("h",0.05)
                abs_placements.append({"page":page_num,"rect":{"x0":nx*pdf_w,"y0":ny*pdf_h,"x1":(nx+nw)*pdf_w,"y1":(ny+nh)*pdf_h}})
            if abs_placements:
                sig_out = unique_output_path("filled_signed_form.pdf")
                sr = tools.stamp_signature(out_path, sig_out, sig_bytes, abs_placements)
                out_path = sig_out; sig_placed = sr.get("placed",0)
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path)),"size_mb":round(os.path.getsize(out_path)/1024/1024,2),"filled":result.get("filled",0),"skipped":result.get("skipped",0),"sig_placed":sig_placed})
    except Exception as e: return jsonify({"error":f"Could not fill form: {str(e)}"}),500
    # NOTE: deliberately NOT deleting temp file here — user may fill & download multiple times.
    # The 60-minute cleanup thread will remove it automatically.

# ── ORGANIZE PAGES ────────────────────────────────────────────────────────────
@app.route("/tool/organize", methods=["GET","POST"])
def tool_organize():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_organize.html")
    file_key = request.form.get("file_key","")
    entry = get_temp_meta(file_key)
    if not entry: return jsonify({"error":"Session expired — please re-upload the file"}),400
    try:
        page_indices = [int(p)-1 for p in request.form.get("page_order","").split(",") if p.strip().isdigit()]
        if not page_indices: return jsonify({"error":"No pages selected"}),400
        out_path = unique_output_path("organized.pdf")
        r = tools.organize_pages(entry["path"], out_path, page_indices)
        # Register as pass-file so Compress/Watermark/etc can receive it directly
        import shutil as _sh
        pass_key = uuid.uuid4().hex[:12]
        pass_path = os.path.join(UPLOAD_DIR, f"pass_{pass_key}_organized.pdf")
        _sh.copy2(out_path, pass_path)
        save_temp_meta(pass_key, pass_path, {"original_name": "organized.pdf"})
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path)),"page_count":r["page_count"],"size_mb":round(r["size_bytes"]/1024/1024,2),"pass_key":pass_key})
    except Exception as e: return jsonify({"error":f"Could not organize: {str(e)}"}),500
    # NOTE: deliberately NOT deleting — user may apply multiple times (undo/redo flow).

# ── VISUAL MERGE ──────────────────────────────────────────────────────────────
@app.route("/tool/merge-visual", methods=["POST"])
def tool_merge_visual():
    redir = require_login()
    if redir: return redir
    import json as _j
    page_list = _j.loads(request.form.get("page_list","[]"))
    if not page_list: return jsonify({"error":"No pages"}),400
    file_map = {}
    for item in page_list:
        key = item["file_key"]
        if key not in file_map:
            entry = get_temp_meta(key)
            if entry: file_map[key] = entry["path"]
    if not file_map: return jsonify({"error":"Session expired — please re-upload the files"}),400
    try:
        import fitz
        dst = fitz.open()
        for item in page_list:
            key = item["file_key"]; pi = item["page"]-1
            rotation = int(item.get("rotation", 0)) % 360
            if key in file_map:
                src = fitz.open(file_map[key])
                if 0 <= pi < len(src):
                    dst.insert_pdf(src, from_page=pi, to_page=pi)
                    if rotation:
                        last = dst[-1]
                        last.set_rotation((last.rotation + rotation) % 360)
                src.close()
        out_path = unique_output_path("merged.pdf")
        dst.save(out_path); total = len(dst); dst.close()
        # Register output as a "pass file" so other tools can receive it without re-upload
        import shutil as _sh
        pass_key = uuid.uuid4().hex[:12]
        pass_path = os.path.join(UPLOAD_DIR, f"pass_{pass_key}_organized.pdf")
        _sh.copy2(out_path, pass_path)
        save_temp_meta(pass_key, pass_path, {"original_name":"organized.pdf"})
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path)),"total_pages":total,"size_mb":round(os.path.getsize(out_path)/1024/1024,2),"pass_key":pass_key})
    except Exception as e: return jsonify({"error":f"Could not merge: {str(e)}"}),500
    finally:
        for key in list(file_map.keys()): delete_temp_meta(key, delete_file=True)

# ── SIGN PDF ──────────────────────────────────────────────────────────────────
@app.route("/api/sign-upload", methods=["POST"])
def api_sign_upload():
    redir = require_login()
    if redir: return redir
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}),400
    file_key   = uuid.uuid4().hex[:12]
    saved_path = os.path.join(UPLOAD_DIR, f"sign_{file_key}_{secure_filename(f.filename)}")
    f.save(saved_path)
    try:
        import fitz, base64
        doc = fitz.open(saved_path)
        thumbnails = []; page_dims = []
        mat = fitz.Matrix(0.28,0.28)
        for i,page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat)
            thumbnails.append({"page":i+1,"data":base64.b64encode(pix.tobytes("jpeg")).decode(),"w":page.rect.width,"h":page.rect.height})
            page_dims.append({"width":page.rect.width,"height":page.rect.height})
        doc.close()
        save_temp_meta(file_key, saved_path, {"page_dims":page_dims})
        return jsonify({"file_key":file_key,"file_name":secure_filename(f.filename),"total_pages":len(thumbnails),"thumbnails":thumbnails,"page_dims":page_dims})
    except Exception as e:
        if os.path.exists(saved_path): os.remove(saved_path)
        return jsonify({"error":str(e)}),500

@app.route("/tool/sign-pdf", methods=["GET","POST"])
def tool_sign_pdf():
    redir = require_login()
    if redir: return redir
    if request.method == "GET": return render_template("tool_sign_pdf.html")
    import json as _j, base64 as _b
    file_key   = request.form.get("file_key","")
    sig_b64    = request.form.get("signature","")
    placements = _j.loads(request.form.get("placements","[]"))
    if not file_key or not sig_b64: return jsonify({"error":"Missing file or signature"}),400
    if not placements: return jsonify({"error":"No placement defined — click a page to place signature"}),400
    entry = get_temp_meta(file_key)
    if not entry: return jsonify({"error":"Session expired — please re-upload the file"}),400
    saved_file = entry["path"]; page_dims = entry["meta"].get("page_dims",[])
    # Accept either blob file upload (preferred) or base64 fallback
    sig_file = request.files.get("sig_file")
    if sig_file:
        sig_bytes = sig_file.read()
    else:
        if sig_b64.startswith("data:"): sig_b64 = sig_b64.split(",",1)[1]
        try: sig_bytes = _b.b64decode(sig_b64)
        except: return jsonify({"error":"Invalid signature data"}),400
    abs_placements = []
    for p in placements:
        page_num = p.get("page",1); pi = page_num-1
        if pi < 0 or pi >= len(page_dims): continue
        dim = page_dims[pi]; pdf_w,pdf_h = dim["width"],dim["height"]
        norm = p.get("norm",{})
        nx,ny,nw,nh = norm.get("x",0),norm.get("y",0),norm.get("w",0.1),norm.get("h",0.05)
        abs_placements.append({"page":page_num,"rect":{"x0":nx*pdf_w,"y0":ny*pdf_h,"x1":(nx+nw)*pdf_w,"y1":(ny+nh)*pdf_h}})
    out_path = unique_output_path("signed.pdf")
    try:
        r = tools.stamp_signature(saved_file, out_path, sig_bytes, abs_placements)
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path)),"size_mb":round(os.path.getsize(out_path)/1024/1024,2),"placed":r["placed"]})
    except Exception as e: return jsonify({"error":f"Could not sign: {str(e)}"}),500
    # NOTE: deliberately NOT deleting temp file — user may sign and re-download multiple times.

# ============================================================================
# CONVERTERS — PDF <-> Word / Excel, OCR  (all offline)
# ============================================================================

def _register_pass_file(out_path, nice_name):
    """Copy an output file into the pass-registry so downstream tools can use it."""
    import shutil as _sh
    pk = uuid.uuid4().hex[:12]
    pp = os.path.join(UPLOAD_DIR, f"pass_{pk}_{secure_filename(nice_name)}")
    _sh.copy2(out_path, pp)
    save_temp_meta(pk, pp, {"original_name": nice_name})
    return pk


@app.route("/tool/pdf-to-word", methods=["GET","POST"])
def tool_pdf_to_word():
    redir = require_login()
    if redir: return redir
    if request.method == "GET":
        return render_template("tool_pdf_to_word.html", caps=converters.capabilities())
    in_path, is_pass, orig_name = resolve_upload(request, file_field="file", prefix="p2w")
    if not in_path: return jsonify({"error":"No file uploaded"}),400
    base = os.path.splitext(secure_filename(orig_name))[0]
    out_path = unique_output_path(f"{base}.docx")
    try:
        r = converters.pdf_to_word(in_path, out_path)
        pk = _register_pass_file(out_path, f"{base}.docx")
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path)),
                        "size_mb":round(r["size_bytes"]/1024/1024,2),"pass_key":pk})
    except Exception as e:
        return jsonify({"error":str(e)}),500
    finally:
        if not is_pass and os.path.exists(in_path): os.remove(in_path)


@app.route("/tool/word-to-pdf", methods=["GET","POST"])
def tool_word_to_pdf():
    redir = require_login()
    if redir: return redir
    if request.method == "GET":
        return render_template("tool_word_to_pdf.html", caps=converters.capabilities())
    in_path, is_pass, orig_name = resolve_upload(request, file_field="file", prefix="w2p")
    if not in_path: return jsonify({"error":"No file uploaded"}),400
    base = os.path.splitext(secure_filename(orig_name))[0]
    try:
        final = unique_output_path(f"{base}.pdf")
        r = converters.office_to_pdf(in_path, OUTPUT_DIR, output_path=final)
        pk = _register_pass_file(final, f"{base}.pdf")
        return jsonify({"download_url":url_for("download",path=os.path.basename(final)),
                        "size_mb":round(os.path.getsize(final)/1024/1024,2),"pass_key":pk})
    except Exception as e:
        return jsonify({"error":str(e)}),500
    finally:
        if not is_pass and os.path.exists(in_path): os.remove(in_path)


@app.route("/tool/pdf-to-excel", methods=["GET","POST"])
def tool_pdf_to_excel():
    redir = require_login()
    if redir: return redir
    if request.method == "GET":
        return render_template("tool_pdf_to_excel.html", caps=converters.capabilities())
    in_path, is_pass, orig_name = resolve_upload(request, file_field="file", prefix="p2x")
    if not in_path: return jsonify({"error":"No file uploaded"}),400
    base = os.path.splitext(secure_filename(orig_name))[0]
    out_path = unique_output_path(f"{base}.xlsx")
    try:
        r = converters.pdf_to_excel(in_path, out_path)
        pk = _register_pass_file(out_path, f"{base}.xlsx")
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path)),
                        "size_mb":round(r["size_bytes"]/1024/1024,2),
                        "tables_found":r["tables_found"],"pages_with_tables":r["pages_with_tables"],
                        "total_pages":r["total_pages"],"pass_key":pk})
    except Exception as e:
        return jsonify({"error":str(e)}),500
    finally:
        if not is_pass and os.path.exists(in_path): os.remove(in_path)


@app.route("/tool/excel-to-pdf", methods=["GET","POST"])
def tool_excel_to_pdf():
    redir = require_login()
    if redir: return redir
    if request.method == "GET":
        return render_template("tool_excel_to_pdf.html", caps=converters.capabilities())
    in_path, is_pass, orig_name = resolve_upload(request, file_field="file", prefix="x2p")
    if not in_path: return jsonify({"error":"No file uploaded"}),400
    base = os.path.splitext(secure_filename(orig_name))[0]
    try:
        final = unique_output_path(f"{base}.pdf")
        r = converters.office_to_pdf(in_path, OUTPUT_DIR, output_path=final)
        pk = _register_pass_file(final, f"{base}.pdf")
        return jsonify({"download_url":url_for("download",path=os.path.basename(final)),
                        "size_mb":round(os.path.getsize(final)/1024/1024,2),"pass_key":pk})
    except Exception as e:
        return jsonify({"error":str(e)}),500
    finally:
        if not is_pass and os.path.exists(in_path): os.remove(in_path)


@app.route("/tool/ocr-pdf", methods=["GET","POST"])
def tool_ocr_pdf():
    redir = require_login()
    if redir: return redir
    if request.method == "GET":
        return render_template("tool_ocr_pdf.html",
                               caps=converters.capabilities(),
                               languages=converters.available_ocr_languages())
    in_path, is_pass, orig_name = resolve_upload(request, file_field="file", prefix="ocr")
    if not in_path: return jsonify({"error":"No file uploaded"}),400
    base = os.path.splitext(secure_filename(orig_name))[0]
    out_path = unique_output_path(f"{base}_searchable.pdf")
    language = request.form.get("language","eng")
    force = request.form.get("force","") in ("1","true","on","yes")
    try:
        r = converters.ocr_pdf(in_path, out_path, language=language, force=force)
        pk = _register_pass_file(out_path, f"{base}_searchable.pdf")
        return jsonify({"download_url":url_for("download",path=os.path.basename(out_path)),
                        "size_mb":round(r["size_bytes"]/1024/1024,2),"language":r["language"],"pass_key":pk})
    except Exception as e:
        return jsonify({"error":str(e)}),500
    finally:
        if not is_pass and os.path.exists(in_path): os.remove(in_path)


# ── PRELOAD ENDPOINT ─────────────────────────────────────────────────────────
@app.route("/api/preload/<pass_key>")
def api_preload(pass_key):
    redir = require_login()
    if redir: return redir
    entry = get_temp_meta(pass_key)
    if not entry: return jsonify({"error":"File expired or not found"}),404
    return jsonify({
        "pass_key": pass_key,
        "original_name": entry["meta"].get("original_name","file.pdf"),
        "size_mb": round(os.path.getsize(entry["path"])/1024/1024,2),
    })

# ── DOWNLOAD ──────────────────────────────────────────────────────────────────
@app.route("/download/<path:path>")
def download(path):
    redir = require_login()
    if redir: return redir
    safe = os.path.basename(path)
    full = os.path.join(OUTPUT_DIR, safe)
    real_output = os.path.realpath(OUTPUT_DIR)
    real_full   = os.path.realpath(full)
    if not real_full.startswith(real_output + os.sep) and real_full != real_output: abort(404)
    if not os.path.exists(full): abort(404)
    nice_name = safe.split("_",1)[1] if "_" in safe else safe
    return send_file(full, as_attachment=True, download_name=nice_name)

# ── CLEANUP ───────────────────────────────────────────────────────────────────
def cleanup_old_files():
    while True:
        try:
            cutoff = time.time() - (CLEANUP_AFTER_MINUTES * 60)
            with _temp_lock:
                protected = {os.path.abspath(e["path"]) for e in _temp_storage.values() if os.path.exists(e["path"])}
            for d in [UPLOAD_DIR, OUTPUT_DIR]:
                for fname in os.listdir(d):
                    fpath = os.path.join(d, fname)
                    if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                        if os.path.abspath(fpath) not in protected:
                            try: os.remove(fpath)
                            except: pass
            cleanup_temp_storage()
        except: pass
        time.sleep(300)

def health_check():
    print("="*60)
    print("JimTools — starting up")
    print("="*60)
    try: gs = find_ghostscript(); print(f"[OK] Ghostscript: {gs}")
    except RuntimeError as e: print(f"[FAIL] {e}")
    try:
        import pikepdf, fitz
        print(f"[OK] pikepdf {pikepdf.__version__}")
        print(f"[OK] PyMuPDF loaded")
    except ImportError as e: print(f"[FAIL] {e}")
    print(f"[OK] Upload dir: {UPLOAD_DIR}")
    print(f"[OK] Output dir: {OUTPUT_DIR}")
    print(f"[OK] Password: {'*' * len(APP_PASSWORD)} (set JIMTOOLS_PASSWORD env var to change)")
    print(f"[OK] Auto-delete after {CLEANUP_AFTER_MINUTES} min")
    print(f"[OK] Temp registry: active (multi-step tools are robust)")
    print("="*60)

if __name__ == "__main__":
    health_check()
    threading.Thread(target=cleanup_old_files, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False)
