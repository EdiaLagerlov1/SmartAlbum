"""
בדיקות API ל-F01 (issue #2): פתיחת פרויקט + העלאת תמונות עם חיווי התקדמות.

משתמש ב-Flask `app.test_client()` (לא httpx!) - ראה INTERFACES.md סעיף 1,
הערת architect: FastAPI/httpx לא ניתנים להתקנה בסביבה הזו, Flask כבר מותקן.

מכסה:
  (א) יצירת פרויקט מחזירה project_id.
  (ב) העלאת JPEG תקין (נוצר פרוגרמטית עם Pillow) מחזירה status="done".
  (ג) העלאת קובץ לא-JPEG / גדול מהמותר נדחית עם error_reason, בלי 500.
  (ד) העלאה תקינה אחרי קובץ שנדחה עדיין מצליחה (קובץ כושל לא "תוקע" בקשות הבאות).
  (ה) 404 מסודר (JSON) לפרויקט לא קיים.
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


def test_create_project_returns_project_id(client):
    resp = client.post("/api/projects", json={"name": "פרויקט בדיקה"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert "project_id" in body and body["project_id"]
    assert body["name"] == "פרויקט בדיקה"
    assert body["status"] == "draft"


def test_create_project_requires_name(client):
    resp = client.post("/api/projects", json={"name": "   "})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("error") is True


def test_upload_valid_jpeg_returns_done(client):
    project_id = _create_project(client)

    jpeg_bytes = _make_jpeg_bytes()
    resp = client.post(
        f"/api/projects/{project_id}/photos",
        data={"file": (io.BytesIO(jpeg_bytes), "wedding1.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "done"
    assert body["photo_id"]
    assert body["error_reason"] is None

    # רשימת התמונות של הפרויקט משקפת את ההעלאה.
    list_resp = client.get(f"/api/projects/{project_id}/photos")
    assert list_resp.status_code == 200
    photos = list_resp.get_json()
    assert len(photos) == 1
    assert photos[0]["status"] == "done"
    assert photos[0]["original_filename"] == "wedding1.jpg"


def test_upload_non_jpeg_is_rejected_without_500(client):
    project_id = _create_project(client)

    png_bytes = _make_png_bytes()
    resp = client.post(
        f"/api/projects/{project_id}/photos",
        data={"file": (io.BytesIO(png_bytes), "not_a_jpeg.png", "image/png")},
        content_type="multipart/form-data",
    )

    assert resp.status_code != 500
    body = resp.get_json()
    assert body["status"] == "failed"
    assert body["photo_id"] is None
    assert body["error_reason"]


def test_upload_too_large_file_is_rejected_without_500(client):
    project_id = _create_project(client)

    # קובץ "מזוייף" מעל 20MB (לא צריך להיות JPEG תקין - אסור שיגיע ל-pipeline כלל).
    too_large_bytes = b"\xff" * (20 * 1024 * 1024 + 1024)
    resp = client.post(
        f"/api/projects/{project_id}/photos",
        data={"file": (io.BytesIO(too_large_bytes), "huge.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )

    assert resp.status_code != 500
    body = resp.get_json()
    assert body["status"] == "failed"
    assert body["photo_id"] is None
    assert "20" in body["error_reason"] or "גדול" in body["error_reason"]


def test_failed_upload_does_not_block_subsequent_valid_upload(client):
    project_id = _create_project(client)

    # (1) קובץ שגוי - נדחה.
    bad_resp = client.post(
        f"/api/projects/{project_id}/photos",
        data={"file": (io.BytesIO(b"garbage not a jpeg"), "corrupt.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )
    assert bad_resp.status_code != 500
    assert bad_resp.get_json()["status"] == "failed"

    # (2) קובץ תקין - צריך להצליח כרגיל, לא "תקוע" בגלל הקובץ הקודם.
    good_bytes = _make_jpeg_bytes()
    good_resp = client.post(
        f"/api/projects/{project_id}/photos",
        data={"file": (io.BytesIO(good_bytes), "good.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )
    assert good_resp.status_code == 200
    good_body = good_resp.get_json()
    assert good_body["status"] == "done"
    assert good_body["photo_id"]

    # ברשימה: קובץ אחד בלבד (הכושל לא נכתב ל-photos, לפי חוזה image_pipeline).
    list_resp = client.get(f"/api/projects/{project_id}/photos")
    photos = list_resp.get_json()
    assert len(photos) == 1
    assert photos[0]["original_filename"] == "good.jpg"


def test_upload_to_nonexistent_project_returns_clean_404(client):
    resp = client.post(
        f"/api/projects/does-not-exist-123/photos",
        data={"file": (io.BytesIO(_make_jpeg_bytes()), "x.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 404
    body = resp.get_json()
    assert body is not None
    assert body.get("error") is True


def test_list_photos_for_nonexistent_project_returns_clean_404(client):
    resp = client.get("/api/projects/does-not-exist-456/photos")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body.get("error") is True
