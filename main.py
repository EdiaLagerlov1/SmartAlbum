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
                                              תומך בפרמטר query אופציונל
                                              ?flag=none|must|exclude לסינון
                                              (F03, issue #5).
  GET  /                                   - מגיש static/index.html.

Endpoints (F10 - issue #11, ראה INTERFACES.md סעיף 5 לחוזה המלא):
  GET    /api/projects/<project_id>/status - סטטוס גס (projects.status) +
                                              per-stage (processing_jobs).
  POST   /api/projects/<project_id>/retry  - ניסיון חוזר: קורא שוב
                                              ל-image_pipeline.process_upload
                                              רק על קבצים שנכשלו/לא הושלמו.
  DELETE /api/projects/<project_id>        - מחיקת פרויקט (db.delete_project
                                              בלבד - גם DB וגם קבצים).

Endpoints (F03, issue #4 - עטיפת HTTP דקה מעל selection_engine.api, ראה
INTERFACES.md סעיף 4.2):
  PATCH /api/projects/<project_id>/photos/<photo_id> - עדכון flag בודד
                                              (body: {"flag": "none"|"must"|"exclude"}).
  GET   /api/projects/<project_id>/must-count - ספירת תמונות 'must' +
                                              אינדיקציה אם עברו את הגבול (60).

Endpoints (F05, issue #6 - עטיפת HTTP דקה מעל selection_engine.api.tag_event_stages,
ראה INTERFACES.md סעיף 4.2):
  POST  /api/projects/<project_id>/tag-stages - מריץ תיוג שלבי אירוע לכל
                                              תמונות הפרויקט (מעדכן photos.event_stage/
                                              event_stage_confidence/needs_stage_review).

Endpoints (F06, issue #7 - עטיפת HTTP דקה מעל selection_engine.api.run_selection,
ראה INTERFACES.md סעיף 4.2):
  POST  /api/projects/<project_id>/run-selection - מריץ את אלגוריתם הבחירה
                                              (עד 60 תמונות, כולל must,
                                              ללא exclude, מצמצם כפילויות
                                              לפי similarity_group_id).
                                              מחזיר status="ok"/"too_many_must"/
                                              "too_few_photos" - 200 בכל
                                              המקרים (כמו upload_photo, ה-UI
                                              מחליט מה להציג לפי status, לא
                                              לפי HTTP status code).

Endpoints (F07, issue #8 - עטיפת HTTP דקה מעל layout_engine.api.generate_layout,
ראה INTERFACES.md סעיף 4.3):
  POST  /api/projects/<project_id>/generate-layout - שולף את תמונות הפרויקט
                                              עם selected_for_album=1 (ממוין
                                              לפי selection_order - "רשימת
                                              ה-photo_id הרשמית" לפי החוזה),
                                              ובונה 15 כפולות + תבניות.
                                              מחזיר status="ok"/"error" - 200
                                              בכל המקרים (כמו run-selection).

Endpoints (F08, issue #9 - עטיפת HTTP דקה מעל layout_engine.api.replace_photo_in_slot/
change_template/get_spreads, ראה INTERFACES.md סעיף 4.3 - "מודל save/restore":
אין draft נפרד, כל קריאה כאן כותבת/מחזירה ישירות מ-SQLite):
  GET   /api/projects/<project_id>/spreads        - מצב הכפולות הנוכחי
                                              (כולל thumbnail_url/width/height
                                              לכל תמונה) - לרינדור ה-UI וגם
                                              ל"שחזור" אחרי פתיחה מחדש.
  POST  /api/projects/<project_id>/spreads/<spread_id>/replace-photo -
                                              body: {"old_photo_id", "new_photo_id",
                                              "allow_duplicate": false}.
                                              מחזיר status="ok"/"error" - 200
                                              בכל המקרים (עקבי עם run-selection/
                                              generate-layout), 404 אם הפרויקט
                                              לא קיים.
  POST  /api/projects/<project_id>/spreads/<spread_id>/change-template -
                                              body: {"template_id"}. מחזיר
                                              status="ok"/"error" - 200 בכל
                                              המקרים, 404 אם הפרויקט לא קיים.
  GET   /api/templates                     - קטלוג 6 התבניות הקבוע
                                              (templates.json) - נוחות ל-UI
                                              לבניית בורר-תבניות, בלי לשכפל
                                              את הקובץ בצד frontend.

חיבור DB: חיבור sqlite3 *חדש* per-request (נפתח ב-get_db(), נסגר
ב-teardown_appcontext) - לא חיבור גלובלי משותף בין threads (dev server
של Flask הוא multi-threaded כברירת מחדל).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone

from flask import Flask, g, jsonify, request, send_from_directory

import db
import storage
from image_pipeline import api as image_pipeline
from layout_engine import api as layout_engine
from selection_engine import api as selection_engine

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


def _get_photo_or_404(conn, project_id: str, photo_id: str):
    return conn.execute(
        "SELECT * FROM photos WHERE project_id = ? AND id = ?", (project_id, photo_id)
    ).fetchone()


def _get_spread_or_404(conn, project_id: str, spread_id: str):
    return conn.execute(
        "SELECT id FROM spreads WHERE project_id = ? AND id = ?", (project_id, spread_id)
    ).fetchone()


def _thumbnail_urls_for_photos(conn, project_id: str, photo_ids: list[str]) -> dict[str, dict]:
    """שולף thumb_path/width/height לכל photo_id רלוונטי בשאילתה אחת (לא
    per-photo) ובונה thumbnail_url - אותה קונבנציה כמו list_photos (F01)."""
    distinct_ids = list(dict.fromkeys(photo_ids))
    if not distinct_ids:
        return {}
    placeholders = ",".join("?" for _ in distinct_ids)
    rows = conn.execute(
        f"""SELECT id, thumb_path, width, height FROM photos
            WHERE project_id = ? AND id IN ({placeholders})""",
        (project_id, *distinct_ids),
    ).fetchall()
    return {
        row["id"]: {
            "width": row["width"],
            "height": row["height"],
            "thumbnail_url": (
                f"/api/projects/{project_id}/photos/{row['id']}/thumbnail"
                if row["thumb_path"]
                else None
            ),
        }
        for row in rows
    }


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
def _validate_upload_filename(file_storage) -> str | None:
    """בדיקה שיש שם קובץ כלל - לפני שקוראים/שומרים כל תוכן."""
    filename = file_storage.filename or ""
    if not filename:
        return "לא התקבל שם קובץ"
    return None


def _validate_upload_type(file_storage) -> str | None:
    """בדיקת סוג קובץ (סיומת/content-type) - לא דורשת קריאת תוכן הקובץ.
    מחזיר הודעת שגיאה או None אם תקין."""
    filename = file_storage.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    content_type = (file_storage.content_type or "").lower()

    if ext not in _ALLOWED_EXTENSIONS:
        return f"סוג קובץ לא נתמך ({ext or 'לא ידוע'}) - רק JPEG מותר"

    if content_type and content_type not in _ALLOWED_CONTENT_TYPES:
        return f"Content-Type לא נתמך ({content_type}) - רק JPEG מותר"

    return None


# ---------------------------------------------------------------------------
# F10 (issue #11): "retry cache" - שומר את הקובץ הגולמי של כל העלאה שנכשלה
# (ולידציה בצד שרת או כשל ב-image_pipeline), כדי ש-POST /retry יוכל לקרוא
# שוב ל-image_pipeline.process_upload על אותו קובץ. ראה INTERFACES.md סעיף
# 5.2: "process_upload לא 'זוכר' קבצים שנכשלו לפני שנכתבו ל-originals/" -
# זו אחריות frontend-rtl לשמור את הקובץ בעצמו עד שהעיבוד מצליח.
#
# נשמר *תחת* storage.project_dir(project_id) (לא תיקייה/טבלה חדשה בחוזה
# הרשמי) - כך שהוא מתנקה אוטומטית ב-db.delete_project (F10, 5.3) מבלי
# לגעת ב-storage.py/db.py.
# ---------------------------------------------------------------------------
_RETRY_CACHE_DIRNAME = "_retry_cache"


def _retry_cache_dir(project_id: str):
    path = storage.project_dir(project_id) / _RETRY_CACHE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _retry_cache_key(original_filename: str) -> str:
    # hash ולא secure_filename - original_filename יכול להיות בעברית, ו-
    # secure_filename מוריד תווים לא-ASCII (עלול לקצר לשם ריק).
    return hashlib.sha256(original_filename.encode("utf-8")).hexdigest()


def _retry_cache_manifest_path(project_id: str):
    return _retry_cache_dir(project_id) / "manifest.json"


def _load_retry_manifest(project_id: str) -> dict:
    path = _retry_cache_manifest_path(project_id)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_retry_manifest(project_id: str, manifest: dict) -> None:
    _retry_cache_manifest_path(project_id).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


def _cache_failed_upload(project_id: str, original_filename: str, tmp_path: str) -> None:
    """שומר עותק של הקובץ הגולמי שנכשל, לשימוש ע"י POST /retry."""
    key = _retry_cache_key(original_filename)
    data_path = _retry_cache_dir(project_id) / f"{key}.bin"
    shutil.copyfile(tmp_path, data_path)
    manifest = _load_retry_manifest(project_id)
    manifest[key] = original_filename
    _save_retry_manifest(project_id, manifest)


def _clear_cached_failed_upload(project_id: str, original_filename: str) -> None:
    """מסירה קובץ מה-retry cache (נקרא כשהעיבוד הצליח - אין צורך יותר)."""
    key = _retry_cache_key(original_filename)
    data_path = _retry_cache_dir(project_id) / f"{key}.bin"
    try:
        data_path.unlink()
    except OSError:
        pass
    manifest = _load_retry_manifest(project_id)
    if key in manifest:
        del manifest[key]
        _save_retry_manifest(project_id, manifest)


def _list_cached_failed_uploads(project_id: str) -> list[tuple[str, str]]:
    """רשימת (original_filename, data_path) לכל קובץ שממתין ל-retry."""
    manifest = _load_retry_manifest(project_id)
    cache_dir = _retry_cache_dir(project_id)
    result = []
    for key, filename in manifest.items():
        data_path = cache_dir / f"{key}.bin"
        if data_path.is_file():
            result.append((filename, str(data_path)))
    return result


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

    filename_error = _validate_upload_filename(file_storage)
    if filename_error is not None:
        return (
            jsonify(
                {
                    "photo_id": None,
                    "status": "failed",
                    "error_reason": filename_error,
                    "original_filename": file_storage.filename,
                }
            ),
            200,
        )

    filename = file_storage.filename

    try:
        tmp_path, _size = _save_upload_to_tempfile(file_storage, _MAX_FILE_SIZE_BYTES)
    except ValueError:
        return (
            jsonify(
                {
                    "photo_id": None,
                    "status": "failed",
                    "error_reason": "הקובץ גדול מהמותר (מקסימום 20MB לקובץ)",
                    "original_filename": filename,
                }
            ),
            200,
        )

    # F10 (issue #11): סוג קובץ לא נתמך - נדחה בלי לקרוא ל-pipeline, אבל
    # עדיין נשמר ב-retry cache כדי ש-POST /retry יוכל לנסות אותו מול
    # image_pipeline.process_upload בעצמו (ראה INTERFACES.md סעיף 5.2).
    type_error = _validate_upload_type(file_storage)
    if type_error is not None:
        _cache_failed_upload(project_id, filename, tmp_path)
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return (
            jsonify(
                {
                    "photo_id": None,
                    "status": "failed",
                    "error_reason": type_error,
                    "original_filename": filename,
                }
            ),
            200,
        )

    try:
        result = image_pipeline.process_upload(
            project_id=project_id,
            saved_file_path=tmp_path,
            original_filename=filename,
            db=conn,
        )
        if result.status == "failed":
            # F10: שומר את הקובץ הגולמי כדי שניסיון חוזר (POST /retry) יוכל
            # לקרוא שוב ל-process_upload עליו.
            _cache_failed_upload(project_id, filename, tmp_path)
        else:
            _clear_cached_failed_upload(project_id, filename)
    finally:
        # הקובץ הזמני תמיד נמחק - עותק (אם צריך) כבר הועתק ל-retry cache,
        # ו-process_upload כותב עותק עצמאי ל-originals/ בהצלחה.
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
                "original_filename": filename,
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

    # F03 (issue #5): סינון אופציונלי לפי flag, למשל לרשימת "תמונות חובה"
    # נפרדת ב-UI. בלי הפרמטר - מתנהג כמקודם (כל התמונות, F01).
    flag_filter = request.args.get("flag")
    if flag_filter is not None and flag_filter not in selection_engine.VALID_FLAGS:
        return _json_error(
            f"ערך flag לא חוקי לסינון: {flag_filter!r} - מותר רק 'none', 'must' או 'exclude'",
            400,
            error_reason="invalid_flag",
        )

    query = """
        SELECT id, original_filename, file_size_bytes, uploaded_at, thumb_path,
               width, height, flag, error_reason
        FROM photos
        WHERE project_id = ?
        {flag_clause}
        ORDER BY uploaded_at ASC
        """
    params: tuple = (project_id,)
    if flag_filter is not None:
        query = query.format(flag_clause="AND flag = ?")
        params = (project_id, flag_filter)
    else:
        query = query.format(flag_clause="")

    rows = conn.execute(query, params).fetchall()

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
# F03 (issue #5): עדכון flag (none/must/exclude) לתמונה בודדה.
# עטיפת HTTP דקה 1:1 מעל selection_engine.api.set_flag/check_must_limit -
# אין לוגיקת בחירה כאן, ראה INTERFACES.md סעיף 4.2.
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/photos/<photo_id>", methods=["PATCH"])
def update_photo_flag(project_id: str, photo_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    photo = _get_photo_or_404(conn, project_id, photo_id)
    if photo is None:
        return _json_error(f"תמונה לא נמצאה: {photo_id}", 404, error_reason="photo_not_found")

    payload = request.get_json(silent=True) or {}
    if "flag" not in payload:
        return _json_error("שדה 'flag' הוא שדה חובה בבקשה", 400)

    flag = payload.get("flag")
    try:
        selection_engine.set_flag(photo_id, flag, conn)
    except selection_engine.InvalidFlagError as exc:
        return _json_error(str(exc), 400, error_reason="invalid_flag")

    must_check = selection_engine.check_must_limit(project_id, conn)
    updated = _get_photo_or_404(conn, project_id, photo_id)

    return jsonify(
        {
            "photo_id": updated["id"],
            "flag": updated["flag"],
            "must_count": must_check.must_count,
            "must_count_exceeds_limit": not must_check.ok,
        }
    )


# ---------------------------------------------------------------------------
# F03 (issue #5): ספירת 'must' + אינדיקציה לחציית הגבול (60), בלי לשנות
# כלום - נוח ל-UI כדי להציג אזהרה בלי לחכות לעדכון flag הבא (למשל בטעינת
# דף הבחירה). עטיפת HTTP דקה מעל selection_engine.api.check_must_limit.
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/must-count", methods=["GET"])
def get_must_count(project_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    must_check = selection_engine.check_must_limit(project_id, conn)
    return jsonify(
        {
            "must_count": must_check.must_count,
            "must_count_exceeds_limit": not must_check.ok,
        }
    )


# ---------------------------------------------------------------------------
# F05 (issue #6): תיוג שלבי אירוע. עטיפת HTTP דקה 1:1 מעל
# selection_engine.api.tag_event_stages - אין לוגיקת קיבוץ/זמן כאן, ראה
# INTERFACES.md סעיף 4.2. מריץ על *כל* תמונות הפרויקט (לא per-photo) -
# לכן אין body/פרמטרים, רק project_id בנתיב.
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/tag-stages", methods=["POST"])
def tag_event_stages_route(project_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    selection_engine.tag_event_stages(project_id, conn)

    rows = conn.execute(
        """
        SELECT id, event_stage, event_stage_confidence, needs_stage_review
        FROM photos WHERE project_id = ?
        ORDER BY uploaded_at ASC
        """,
        (project_id,),
    ).fetchall()

    needs_review_count = sum(1 for row in rows if row["needs_stage_review"])

    return jsonify(
        {
            "project_id": project_id,
            "photos": [
                {
                    "photo_id": row["id"],
                    "event_stage": row["event_stage"],
                    "event_stage_confidence": row["event_stage_confidence"],
                    "needs_stage_review": bool(row["needs_stage_review"]),
                }
                for row in rows
            ],
            "needs_review_count": needs_review_count,
        }
    )


# ---------------------------------------------------------------------------
# F06 (issue #7): אלגוריתם בחירה - עד 60 תמונות. עטיפת HTTP דקה 1:1 מעל
# selection_engine.api.run_selection - אין לוגיקת בחירה/דירוג כאן, ראה
# INTERFACES.md סעיף 4.2. 200 בכל המקרים (גם status="too_many_must"/
# "too_few_photos") - אלה לא שגיאות HTTP, זו תוצאה תקינה של הרצת האלגוריתם
# שה-UI מציג כהודעה למשתמשת (בדיוק כמו upload_photo עם status="failed").
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/run-selection", methods=["POST"])
def run_selection_route(project_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    result = selection_engine.run_selection(project_id, conn)

    return jsonify(
        {
            "project_id": project_id,
            "status": result.status,
            "selected_photo_ids": result.selected_photo_ids,
            "selected_count": len(result.selected_photo_ids),
            "message": result.message,
        }
    )


# ---------------------------------------------------------------------------
# F07 (issue #8): יצירת 15 כפולות. עטיפת HTTP דקה 1:1 מעל
# layout_engine.api.generate_layout - אין לוגיקת חלוקה/תבניות כאן, ראה
# INTERFACES.md סעיף 4.3. השליפה של selected_photo_ids (ה-query היחיד
# המחייב לפי החוזה בין selection-engine ל-layout-engine, סעיף 4.2) נעשית
# כאן ברמת ה-route - לא לוגיקת-בחירה, רק העברת הקלט התקני לפונקציה.
# 200 בכל המקרים (גם status="error") - בדיוק כמו run-selection.
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/generate-layout", methods=["POST"])
def generate_layout_route(project_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    selected_rows = conn.execute(
        """SELECT id FROM photos WHERE project_id = ? AND selected_for_album = 1
           ORDER BY selection_order""",
        (project_id,),
    ).fetchall()
    selected_photo_ids = [row["id"] for row in selected_rows]

    result = layout_engine.generate_layout(project_id, selected_photo_ids, conn)

    return jsonify(
        {
            "project_id": project_id,
            "status": result.status,
            "spread_ids": result.spread_ids,
            "spread_count": len(result.spread_ids),
            "message": result.message,
        }
    )


# ---------------------------------------------------------------------------
# F08 (issue #9): GET /spreads - מצב הכפולות הנוכחי. עטיפת HTTP דקה מעל
# layout_engine.api.get_spreads + בניית thumbnail_url/width/height per photo
# (אותה קונבנציה כמו list_photos, F01) - אין לוגיקת grid/תבנית כאן, ראה
# INTERFACES.md סעיף 4.3. זה גם ה-endpoint ש"שחזור" (reload) נשען עליו -
# קורא תמיד את המצב המעודכן מ-SQLite, בלי cache בזיכרון.
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/spreads", methods=["GET"])
def get_spreads_route(project_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    spreads = layout_engine.get_spreads(project_id, conn)

    all_photo_ids = [p["photo_id"] for spread in spreads for p in spread["photos"]]
    photo_info = _thumbnail_urls_for_photos(conn, project_id, all_photo_ids)

    for spread in spreads:
        for p in spread["photos"]:
            info = photo_info.get(p["photo_id"], {})
            p["thumbnail_url"] = info.get("thumbnail_url")
            p["width"] = info.get("width")
            p["height"] = info.get("height")

    return jsonify({"project_id": project_id, "spreads": spreads, "spread_count": len(spreads)})


# ---------------------------------------------------------------------------
# F08 (issue #9): POST /spreads/<spread_id>/replace-photo - עטיפת HTTP דקה
# 1:1 מעל layout_engine.api.replace_photo_in_slot. 404 אם הפרויקט לא קיים;
# שגיאות "צפויות" (spread/photo לא נמצא, exclude, כפילות) מוחזרות
# כ-status="error" עם 200 (אין קלט שגוי מצד ה-UI, רק תוצאת-אלגוריתם
# לא-תקינה) - עקבי עם run-selection/generate-layout; 400 רק אם שדות החובה
# בגוף הבקשה חסרים (קלט פגום מבחינת HTTP, לא מבחינת האלגוריתם).
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/spreads/<spread_id>/replace-photo", methods=["POST"])
def replace_photo_in_slot_route(project_id: str, spread_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    payload = request.get_json(silent=True) or {}
    old_photo_id = payload.get("old_photo_id")
    new_photo_id = payload.get("new_photo_id")
    if not old_photo_id or not new_photo_id:
        return _json_error("שדות 'old_photo_id' ו-'new_photo_id' הם שדות חובה בבקשה", 400)

    allow_duplicate = bool(payload.get("allow_duplicate", False))

    result = layout_engine.replace_photo_in_slot(
        project_id, spread_id, old_photo_id, new_photo_id, conn, allow_duplicate=allow_duplicate
    )

    return jsonify(
        {
            "project_id": project_id,
            "spread_id": result.spread_id,
            "status": result.status,
            "error_reason": result.error_reason,
            "message": result.message,
        }
    )


# ---------------------------------------------------------------------------
# F08 (issue #9): POST /spreads/<spread_id>/change-template - עטיפת HTTP
# דקה 1:1 מעל layout_engine.api.change_template. ראה הערות ל-replace-photo
# מעל לגבי status="error" מול 400/404.
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/spreads/<spread_id>/change-template", methods=["POST"])
def change_template_route(project_id: str, spread_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    payload = request.get_json(silent=True) or {}
    template_id = payload.get("template_id")
    if not template_id:
        return _json_error("שדה 'template_id' הוא שדה חובה בבקשה", 400)

    result = layout_engine.change_template(project_id, spread_id, template_id, conn)

    return jsonify(
        {
            "project_id": project_id,
            "spread_id": result.spread_id,
            "status": result.status,
            "error_reason": result.error_reason,
            "message": result.message,
            "template_id": result.template_id,
        }
    )


# ---------------------------------------------------------------------------
# F08 (issue #9): GET /api/templates - קטלוג 6 התבניות הקבוע (templates.json),
# נוחות ל-UI (בורר תבניות) - בלי לוגיקה, רק עטיפה דקה מעל layout_engine.TEMPLATES
# (שכבר נטען פעם אחת מה-JSON ב-import, ראה layout_engine/api.py).
# ---------------------------------------------------------------------------
@app.route("/api/templates", methods=["GET"])
def list_templates_route():
    return jsonify({"templates": list(layout_engine.TEMPLATES.values())})


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
# F10 (issue #11): כל שלבי processing_jobs - ראה INTERFACES.md סעיף 2 ו-5.1.
# שלבים שאין להם שורה עדיין (מודול שלא קיים/לא רץ) מוצגים כ-"not_started" -
# מצב נגזר בצד ה-API, לא ערך DB חדש.
# ---------------------------------------------------------------------------
_PROCESSING_STAGES = (
    "upload",
    "exif",
    "thumbnail",
    "grouping",
    "tagging",
    "selection",
    "layout",
    "export",
)


# ---------------------------------------------------------------------------
# F10: GET /api/projects/<id>/status - סטטוס גס (projects.status) + מפורט
# per-stage (processing_jobs). ראה INTERFACES.md סעיף 5.1 לחוזה המלא.
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/status", methods=["GET"])
def project_status(project_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    job_rows = conn.execute(
        "SELECT stage, status, error_reason, updated_at FROM processing_jobs WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    jobs_by_stage = {row["stage"]: row for row in job_rows}

    stages = {}
    for stage in _PROCESSING_STAGES:
        row = jobs_by_stage.get(stage)
        if row is None:
            stages[stage] = {"status": "not_started", "error_reason": None, "updated_at": None}
        else:
            stages[stage] = {
                "status": row["status"],
                "error_reason": row["error_reason"],
                "updated_at": row["updated_at"],
            }

    photos_failed_count = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE project_id = ? AND error_reason IS NOT NULL",
        (project_id,),
    ).fetchone()[0]
    photos_total_count = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE project_id = ?",
        (project_id,),
    ).fetchone()[0]

    return jsonify(
        {
            "project_id": project_id,
            "status": project["status"],
            "stages": stages,
            "photos_failed_count": photos_failed_count,
            "photos_total_count": photos_total_count,
        }
    )


# ---------------------------------------------------------------------------
# F10: POST /api/projects/<id>/retry - ניסיון חוזר. קורא שוב ל-
# image_pipeline.process_upload *רק* על קבצים עם error_reason (שורת photos
# קיימת שנכשלה) או קבצים שהועלו אך לא קיבלו שורת photos בהצלחה כלל (נדחו
# בולידציה/נכשלו ב-pipeline לפני כתיבת שורה) - בהתאם ל-INTERFACES.md סעיף
# 5.2. *לא* קורא process_upload על תמונות עם status="done" - אלה מסוננות
# מראש (לא רק dedup בתוך process_upload עצמו).
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/retry", methods=["POST"])
def retry_processing(project_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    cached = dict(_list_cached_failed_uploads(project_id))  # filename -> tmp copy path

    retried = []
    seen_filenames = set()

    # (1) שורות photos שכבר נכשלו בפועל (error_reason IS NOT NULL) - רק אם
    # יש לנו עדיין את הקובץ הגולמי שלהן ב-retry cache.
    failed_photo_rows = conn.execute(
        "SELECT id, original_filename FROM photos WHERE project_id = ? AND error_reason IS NOT NULL",
        (project_id,),
    ).fetchall()
    for row in failed_photo_rows:
        filename = row["original_filename"]
        seen_filenames.add(filename)
        data_path = cached.get(filename)
        if data_path is None:
            continue  # אין קובץ גולמי שמור - אין על מה להריץ retry

        result = image_pipeline.process_upload(
            project_id=project_id, saved_file_path=data_path, original_filename=filename, db=conn
        )
        if result.status == "done":
            _clear_cached_failed_upload(project_id, filename)
        retried.append(
            {
                "original_filename": filename,
                "photo_id": result.photo_id,
                "status": result.status,
                "error_reason": result.error_reason,
            }
        )

    # (2) קבצים שהועלו אך לא קיבלו שורת photos בהצלחה כלל (נדחו בולידציה
    # או נכשלו ב-pipeline לפני כתיבת שורה) - כל מה שנשאר ב-retry cache.
    for filename, data_path in cached.items():
        if filename in seen_filenames:
            continue
        result = image_pipeline.process_upload(
            project_id=project_id, saved_file_path=data_path, original_filename=filename, db=conn
        )
        if result.status == "done":
            _clear_cached_failed_upload(project_id, filename)
        retried.append(
            {
                "original_filename": filename,
                "photo_id": result.photo_id,
                "status": result.status,
                "error_reason": result.error_reason,
            }
        )

    return jsonify({"project_id": project_id, "retried": retried})


# ---------------------------------------------------------------------------
# F10: DELETE /api/projects/<id> - מחיקת פרויקט. קריאה יחידה ל-
# db.delete_project (מוחקת גם DB וגם קבצים, ראה INTERFACES.md סעיף 5.3) -
# אין לוגיקת מחיקה נוספת כאן. אישור מפורש הוא אחריות ה-UI, לא ה-backend.
# ---------------------------------------------------------------------------
@app.route("/api/projects/<project_id>", methods=["DELETE"])
def delete_project_route(project_id: str):
    conn = get_db()
    project = _get_project_or_404(conn, project_id)
    if project is None:
        return _json_error(f"פרויקט לא נמצא: {project_id}", 404, error_reason="project_not_found")

    db.delete_project(project_id)

    return jsonify({"project_id": project_id, "deleted": True}), 200


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
