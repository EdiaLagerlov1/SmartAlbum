"""
SmartAlbum - F01: שרת Flask שחושף את ה-API ל-frontend-rtl.

ממשק חייב (ראה INTERFACES.md סעיף 1, הערת architect): Flask + Werkzeug,
לא FastAPI - PyPI חסום בסביבת ה-CI הזו, Flask/Werkzeug כבר מותקנים.

אחריות המודול הזה: שכבת ה-HTTP בלבד (routing, ולידציית קלט, JSON
serialization). *אין* כאן לוגיקת עיבוד/בחירה/פריסה - כל זה נקרא דרך
db.py / storage.py / image_pipeline.api לפי החוזה ב-INTERFACES.md.

Endpoints (F01):
  POST /api/projects                      - יצירת פרויקט חדש.
  POST /api/projects/<project_id>/photos   - העלאת קובץ יחיד (multipart,
                                              שדה "file").
  GET  /api/projects/<project_id>/photos   - רשימת תמונות + סטטוס.
  GET  /                                   - מגיש static/index.html.

חיבור DB: חיבור sqlite3 *חדש* per-request (נפתח ב-get_db(), נסגר
ב-teardown_appcontext) - לא חיבור גלובלי משותף בין threads (dev server
של Flask הוא multi-threaded כברירת מחדל).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timezone

from flask import Flask, g, jsonify, request, send_from_directory

import db
import storage
from image_pipeline import api as image_pipeline

# --- ולידציה בצד שרת (F01): רק JPEG, עד 20MB לקובץ -------------------------
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg"}
_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/jpg"}
_MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20MB לקובץ (F01)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = Flask(__name__, static_folder=_STATIC_DIR, static_url_path="/static")

# init_db() נקרא פעם אחת באתחול המודול - לא בתוך request handler.
db.init_db()


# ---------------------------------------------------------------------------
# ניהול חיבור DB per-request (לא גלובלי משותף בין threads).
# ---------------------------------------------------------------------------
def get_db() -> "db.sqlite3.Connection":
    if "db_conn" not in g:
        g.db_conn = db.get_connection()
    return g.db_conn


@app.teardown_appcontext
def _close_db(_exc: BaseException | None) -> None:
    conn = g.pop("db_conn", None)
    if conn is not None:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_error(message: str, status: int, **extra):
    body = {"error": True, "message": message}
    body.update(extra)
    return jsonify(body), status


def _get_project_or_404(conn, project_id: str):
    return conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# F01: יצירת פרויקט
# ---------------------------------------------------------------------------
@app.route("/api/projects", methods=["POST"])
def create_project():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return _json_error("שם פרויקט הוא שדה חובה", 400)

    project_id = str(uuid.uuid4())
    created_at = _now_iso()

    conn = get_db()
    conn.execute(
        "INSERT INTO projects (id, name, created_at, status) VALUES (?, ?, ?, 'draft')",
        (project_id, name, created_at),
    )
    conn.commit()

    # מכין את מבנה התיקיות בדיסק לפרויקט (originals/thumbnails/exports).
    storage.ensure_project_dirs(project_id)

    return (
        jsonify(
            {
                "project_id": project_id,
                "name": name,
                "created_at": created_at,
                "status": "draft",
            }
        ),
        201,
    )


# ---------------------------------------------------------------------------
# F01: רשימת פרויקטים (נוח ל-UI לפתיחה מחדש של פרויקט קיים)
# ---------------------------------------------------------------------------
@app.route("/api/projects", methods=["GET"])
def list_projects():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, created_at, status FROM projects ORDER BY created_at DESC"
    ).fetchall()
    return jsonify([dict(row) for row in rows])


# ---------------------------------------------------------------------------
# F01: העלאת תמונה יחידה
# ---------------------------------------------------------------------------
def _validate_upload(file_storage) -> str | None:
    """ולידציה בסיסית לפני פנייה ל-pipeline. מחזיר הודעת שגיאה או None אם תקין."""
    filename = file_storage.filename or ""
    if not filename:
        return "לא התקבל שם קובץ"

    ext = os.path.splitext(filename)[1].lower()
    content_type = (file_storage.content_type or "").lower()

    if ext not in _ALLOWED_EXTENSIONS:
        return f"סוג קובץ לא נתמך ({ext or 'לא ידוע'}) - רק JPEG מותר"

    if content_type and content_type not in _ALLOWED_CONTENT_TYPES:
        return f"Content-Type לא נתמך ({content_type}) - רק JPEG מותר"

    return None


def _save_upload_to_tempfile(file_storage, max_size: int):
    """שומר את תוכן ההעלאה לקובץ זמני, ומפסיק מוקדם אם עוברים את max_size
    (כדי לא לקרוא קובץ ענק שלם לזיכרון ולא לקרוס עם 500). מחזיר (path, size)
    או מעלה ValueError אם הקובץ גדול מהמותר."""
    fd, tmp_path = tempfile.mkstemp(prefix="smartalbum_upload_")
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = file_storage.stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    raise ValueError("file too large")
                out.write(chunk)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return tmp_path, total


@app.route("/api/projects/<project_id>/photos", methods=["POST"])
def upload_photo(project_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    if "file" not in request.files:
        return _json_error("לא נשלח קובץ (שדה 'file' חסר)", 400)

    file_storage = request.files["file"]

    validation_error = _validate_upload(file_storage)
    if validation_error is not None:
        return (
            jsonify(
                {
                    "photo_id": None,
                    "status": "failed",
                    "error_reason": validation_error,
                    "original_filename": file_storage.filename,
                }
            ),
            200,
        )

    try:
        tmp_path, _size = _save_upload_to_tempfile(file_storage, _MAX_FILE_SIZE_BYTES)
    except ValueError:
        return (
            jsonify(
                {
                    "photo_id": None,
                    "status": "failed",
                    "error_reason": "הקובץ גדול מהמותר (מקסימום 20MB לקובץ)",
                    "original_filename": file_storage.filename,
                }
            ),
            200,
        )

    try:
        result = image_pipeline.process_upload(
            project_id=project_id,
            saved_file_path=tmp_path,
            original_filename=file_storage.filename,
            db=conn,
        )
    finally:
        # הקובץ הזמני תמיד נמחק - process_upload כותב עותק עצמאי ל-originals/.
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    return (
        jsonify(
            {
                "photo_id": result.photo_id,
                "status": result.status,
                "error_reason": result.error_reason,
                "original_filename": file_storage.filename,
            }
        ),
        200,
    )


# ---------------------------------------------------------------------------
# F01/F10: רשימת תמונות + סטטוס עבור פרויקט
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/photos", methods=["GET"])
def list_photos(project_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    rows = conn.execute(
        """
        SELECT id, original_filename, file_size_bytes, uploaded_at, thumb_path,
               width, height, flag, error_reason
        FROM photos
        WHERE project_id = ?
        ORDER BY uploaded_at ASC
        """,
        (project_id,),
    ).fetchall()

    photos = []
    for row in rows:
        status = "failed" if row["error_reason"] else ("done" if row["thumb_path"] else "pending")
        photos.append(
            {
                "photo_id": row["id"],
                "original_filename": row["original_filename"],
                "file_size_bytes": row["file_size_bytes"],
                "uploaded_at": row["uploaded_at"],
                "width": row["width"],
                "height": row["height"],
                "flag": row["flag"],
                "status": status,
                "error_reason": row["error_reason"],
                "thumbnail_url": f"/api/projects/{project_id}/photos/{row['id']}/thumbnail"
                if row["thumb_path"]
                else None,
            }
        )

    return jsonify(photos)


# ---------------------------------------------------------------------------
# נוחות ל-UI: הגשת thumbnail בודד (לא חלק מהחוזה הפורמלי, רק עטיפה דקה
# מעל storage.thumbnail_path שה-frontend צריך כדי להציג תצוגה מקדמת).
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/photos/<photo_id>/thumbnail", methods=["GET"])
def get_thumbnail(project_id: str, photo_id: str):
    conn = get_db()
    row = conn.execute(
        "SELECT thumb_path FROM photos WHERE project_id = ? AND id = ?",
        (project_id, photo_id),
    ).fetchone()
    if row is None or not row["thumb_path"] or not os.path.isfile(row["thumb_path"]):
        return _json_error("תמונה לא נמצאה", 404)

    directory, filename = os.path.split(row["thumb_path"])
    return send_from_directory(directory, filename, mimetype="image/jpeg")


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(_STATIC_DIR, "index.html")


# ---------------------------------------------------------------------------
# Error handlers גלובליים - תשובת JSON מסודרת (לא stack trace / HTML) ל-/api.
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def _handle_404(err):
    if request.path.startswith("/api/"):
        return _json_error("לא נמצא", 404)
    return err


@app.errorhandler(413)
def _handle_413(err):
    return _json_error("הקובץ שנשלח גדול מהמותר", 413, error_reason="payload_too_large")


@app.errorhandler(500)
def _handle_500(err):
    if request.path.startswith("/api/"):
        return _json_error("שגיאת שרת לא צפויה", 500)
    return err


if __name__ == "__main__":
    app.run(debug=True, port=5000)
