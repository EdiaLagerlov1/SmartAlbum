"""
בדיקות API ל-F10 (issue #11): סטטוס עיבוד/כשל, ניסיון חוזר, מחיקת פרויקט.

משתמש ב-Flask `app.test_client()` - בדיוק כמו tests/test_api_upload.py (ראה
INTERFACES.md סעיף 1, הערת architect: Flask לא FastAPI/httpx בסביבה הזו).

מכסה את חוזה INTERFACES.md סעיף 5:
  (א) GET /status - צורת ה-JSON (stages עם "not_started" לשלבים בלי שורה,
      "done"/"failed" בפועל, photos_failed_count/photos_total_count), 404
      לפרויקט לא קיים.
  (ב) POST /retry - מנסה שוב רק קבצים שנכשלו/לא הושלמו (כולל קובץ שנדחה
      בולידציה ולא קיבל שורת photos כלל), לא נוגע בתמונות status="done".
  (ג) DELETE /<id> - מוחקת את הפרויקט (DB + קבצים בדיסק), בקשות הבאות
      מחזירות 404.
"""

from __future__ import annotations

import io
import os
import sys

import pytest
from PIL import Image

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _make_jpeg_bytes(size=(40, 20), color="green") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _make_png_bytes(size=(10, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color="blue").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """מגדיר storage root + DB זמניים *לפני* ייבוא main (כדי ש-db.init_db()
    שרץ באתחול המודול יפעל מול DB זמני, לא storage/smartalbum.db האמיתי)."""
    storage_root = tmp_path / "storage"
    db_path = storage_root / "smartalbum.db"
    monkeypatch.setenv("SMARTALBUM_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("SMARTALBUM_DB_PATH", str(db_path))

    for mod_name in ("storage", "db", "image_pipeline", "image_pipeline.api", "main"):
        sys.modules.pop(mod_name, None)

    import main as main_mod

    main_mod.app.config["TESTING"] = True
    with main_mod.app.test_client() as test_client:
        yield test_client


def _create_project(client, name="חתונה של דנה ויוסי") -> str:
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["project_id"]
    return body["project_id"]


def _upload(client, project_id, filename, raw_bytes, content_type="image/jpeg"):
    return client.post(
        f"/api/projects/{project_id}/photos",
        data={"file": (io.BytesIO(raw_bytes), filename, content_type)},
        content_type="multipart/form-data",
    )


# ---------------------------------------------------------------------------
# (א) GET /api/projects/<id>/status
# ---------------------------------------------------------------------------

_ALL_STAGES = (
    "upload",
    "exif",
    "thumbnail",
    "grouping",
    "tagging",
    "selection",
    "layout",
    "export",
)


def test_status_for_fresh_project_reports_not_started_stages(client):
    project_id = _create_project(client)

    resp = client.get(f"/api/projects/{project_id}/status")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["project_id"] == project_id
    assert body["status"] == "draft"
    assert body["photos_failed_count"] == 0
    assert body["photos_total_count"] == 0
    assert set(body["stages"].keys()) == set(_ALL_STAGES)
    for stage in _ALL_STAGES:
        assert body["stages"][stage]["status"] == "not_started"
        assert body["stages"][stage]["error_reason"] is None
        assert body["stages"][stage]["updated_at"] is None


def test_status_after_successful_upload_reports_done_stages(client):
    project_id = _create_project(client)
    resp = _upload(client, project_id, "wedding1.jpg", _make_jpeg_bytes())
    assert resp.get_json()["status"] == "done"

    status = client.get(f"/api/projects/{project_id}/status").get_json()
    assert status["stages"]["exif"]["status"] == "done"
    assert status["stages"]["thumbnail"]["status"] == "done"
    assert status["stages"]["exif"]["updated_at"] is not None
    # שלבים שעדיין לא ממומשים (F04 ואילך) נשארים not_started.
    assert status["stages"]["grouping"]["status"] == "not_started"
    assert status["photos_total_count"] == 1
    assert status["photos_failed_count"] == 0


def test_status_after_pipeline_failure_reports_failed_stage(client):
    project_id = _create_project(client)
    # סיומת תקינה (.jpg) אבל תוכן פגום - process_upload נכשל בפועל (exif stage).
    resp = _upload(client, project_id, "corrupt.jpg", b"garbage not a jpeg")
    assert resp.get_json()["status"] == "failed"

    status = client.get(f"/api/projects/{project_id}/status").get_json()
    assert status["stages"]["exif"]["status"] == "failed"
    assert status["stages"]["exif"]["error_reason"]


def test_status_for_nonexistent_project_returns_404(client):
    resp = client.get("/api/projects/does-not-exist/status")
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


# ---------------------------------------------------------------------------
# (ב) POST /api/projects/<id>/retry
# ---------------------------------------------------------------------------


def test_retry_does_not_reprocess_done_photos(client):
    project_id = _create_project(client)
    resp = _upload(client, project_id, "good.jpg", _make_jpeg_bytes())
    photo_id_before = resp.get_json()["photo_id"]

    retry_resp = client.post(f"/api/projects/{project_id}/retry")
    assert retry_resp.status_code == 200
    body = retry_resp.get_json()
    assert body["retried"] == []  # אין כלום לנסות מחדש - התמונה כבר done

    # התמונה עדיין קיימת ולא שונה (photo_id זהה).
    photos = client.get(f"/api/projects/{project_id}/photos").get_json()
    assert len(photos) == 1
    assert photos[0]["photo_id"] == photo_id_before
    assert photos[0]["status"] == "done"


def test_retry_reprocesses_upload_rejected_by_extension_check(client):
    """קובץ שנדחה בולידציה בצד-שרת (סיומת לא נתמכת) - לא קיבל שורת photos
    כלל, אבל frontend-rtl שמר את הבייטים ב-retry cache. retry קורא
    ל-process_upload *בעצמו* (לא בודק סיומת) - PNG תקין נפתח ע"י Pillow
    ומצליח, מה שמדגים "ניסיון חוזר ממשיך משלבים שהושלמו" גם לקובץ שמעולם
    לא עבר את ה-pipeline בהצלחה."""
    project_id = _create_project(client)

    resp = _upload(client, project_id, "photo.png", _make_png_bytes(), content_type="image/png")
    body = resp.get_json()
    assert body["status"] == "failed"
    assert body["photo_id"] is None

    # עדיין לא קיימת שורת photos (נדחה לפני ה-pipeline).
    photos_before = client.get(f"/api/projects/{project_id}/photos").get_json()
    assert photos_before == []

    retry_resp = client.post(f"/api/projects/{project_id}/retry")
    assert retry_resp.status_code == 200
    retried = retry_resp.get_json()["retried"]
    assert len(retried) == 1
    assert retried[0]["original_filename"] == "photo.png"
    assert retried[0]["status"] == "done"
    assert retried[0]["photo_id"]

    photos_after = client.get(f"/api/projects/{project_id}/photos").get_json()
    assert len(photos_after) == 1
    assert photos_after[0]["status"] == "done"

    # ניסיון חוזר נוסף - אין יותר כלום לנסות (ה-cache נוקה בהצלחה).
    second_retry = client.post(f"/api/projects/{project_id}/retry").get_json()
    assert second_retry["retried"] == []


def test_retry_keeps_retrying_still_failing_photo(client):
    """קובץ פגום שנכשל ב-pipeline (exif) - retry מנסה שוב, עדיין נכשל (אין
    תוכן תקין יותר), אבל לא "נעלם"/קורס, ולא משפיע על תמונה done אחרת."""
    project_id = _create_project(client)
    good_resp = _upload(client, project_id, "good.jpg", _make_jpeg_bytes())
    good_photo_id = good_resp.get_json()["photo_id"]

    bad_resp = _upload(client, project_id, "corrupt.jpg", b"garbage not a jpeg")
    assert bad_resp.get_json()["status"] == "failed"

    retry_resp = client.post(f"/api/projects/{project_id}/retry")
    retried = retry_resp.get_json()["retried"]
    assert len(retried) == 1
    assert retried[0]["original_filename"] == "corrupt.jpg"
    assert retried[0]["status"] == "failed"
    assert retried[0]["error_reason"]

    # התמונה התקינה לא עובדה מחדש / לא השתנה.
    photos = client.get(f"/api/projects/{project_id}/photos").get_json()
    good_rows = [p for p in photos if p["original_filename"] == "good.jpg"]
    assert len(good_rows) == 1
    assert good_rows[0]["photo_id"] == good_photo_id
    assert good_rows[0]["status"] == "done"


def test_retry_for_nonexistent_project_returns_404(client):
    resp = client.post("/api/projects/does-not-exist/retry")
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


# ---------------------------------------------------------------------------
# (ג) DELETE /api/projects/<id>
# ---------------------------------------------------------------------------


def test_delete_project_removes_db_rows_and_files(client):
    import storage  # אותו מודול שה-fixture טען עם SMARTALBUM_STORAGE_ROOT זמני

    project_id = _create_project(client)
    _upload(client, project_id, "wedding1.jpg", _make_jpeg_bytes())

    assert storage.project_dir(project_id).exists()

    delete_resp = client.delete(f"/api/projects/{project_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.get_json()["deleted"] is True

    # הקבצים בדיסק נמחקו (originals/thumbnails) - לא רק שורות ה-DB.
    assert not storage.project_dir(project_id).exists()

    # כל endpoint שתלוי בפרויקט מחזיר 404 אחרי המחיקה.
    assert client.get(f"/api/projects/{project_id}/photos").status_code == 404
    assert client.get(f"/api/projects/{project_id}/status").status_code == 404
    assert client.post(f"/api/projects/{project_id}/retry").status_code == 404
    assert client.delete(f"/api/projects/{project_id}").status_code == 404


def test_delete_nonexistent_project_returns_404(client):
    resp = client.delete("/api/projects/does-not-exist")
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True
