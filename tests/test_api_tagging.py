"""
בדיקות API ל-F05 (issue #6): endpoint תיוג שלבי אירוע.

משתמש ב-Flask `app.test_client()` (לא httpx!) - ראה INTERFACES.md סעיף 1.
בודק רק את שכבת ה-HTTP (עטיפה דקה 1:1 מעל selection_engine.tag_event_stages) -
האלגוריתם עצמו מכוסה ב-tests/test_selection_engine_tagging.py.

מכסה:
  (א) POST /tag-stages על פרויקט לא קיים -> 404 מסודר, לא 500.
  (ב) POST /tag-stages על פרויקט עם תמונות בלי taken_at (המצב ברירת-המחדל
      בפועל היום) -> 200, needs_review_count == מספר התמונות, לא קורס.
  (ג) התשובה כוללת per-photo event_stage/confidence/needs_stage_review,
      ומשתקפת גם ב-GET /photos (לא "תצוגה נפרדת" לא-עקבית)... בגרסת
      הבסיס השדות האלה לא מוחזרים ב-GET /photos (F01/F03 בלבד) - בודקים
      רק דרך ה-DB ישירות שהעדכון בפועל קרה.
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


def _make_jpeg_bytes(size=(40, 20), color="blue") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    db_path = storage_root / "smartalbum.db"
    monkeypatch.setenv("SMARTALBUM_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("SMARTALBUM_DB_PATH", str(db_path))

    for mod_name in (
        "storage",
        "db",
        "image_pipeline",
        "image_pipeline.api",
        "selection_engine",
        "selection_engine.api",
        "main",
    ):
        sys.modules.pop(mod_name, None)

    import main as main_mod

    main_mod.app.config["TESTING"] = True
    with main_mod.app.test_client() as test_client:
        yield test_client, main_mod


def _create_project(client, name="חתונה של דנה ויוסי") -> str:
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["project_id"]


def _upload_photo(client, project_id: str, filename="wedding1.jpg") -> str:
    jpeg_bytes = _make_jpeg_bytes()
    resp = client.post(
        f"/api/projects/{project_id}/photos",
        data={"file": (io.BytesIO(jpeg_bytes), filename, "image/jpeg")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "done"
    return body["photo_id"]


# ---------------------------------------------------------------------------
# (א) פרויקט לא קיים -> 404 מסודר.
# ---------------------------------------------------------------------------
def test_tag_stages_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.post("/api/projects/does-not-exist/tag-stages")
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


# ---------------------------------------------------------------------------
# (ב) + (ג) תמונות שהועלו בפועל (taken_at לא ממומש עדיין ב-image-pipeline,
# ראה TODO ב-INTERFACES.md) -> 200, כולן מסומנות לבדיקה, לא קורס.
# ---------------------------------------------------------------------------
def test_tag_stages_on_real_uploads_without_taken_at(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    photo_a = _upload_photo(test_client, project_id, "a.jpg")
    photo_b = _upload_photo(test_client, project_id, "b.jpg")

    resp = test_client.post(f"/api/projects/{project_id}/tag-stages")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()

    assert body["project_id"] == project_id
    assert body["needs_review_count"] == 2
    photos_by_id = {p["photo_id"]: p for p in body["photos"]}
    assert set(photos_by_id.keys()) == {photo_a, photo_b}
    for photo in photos_by_id.values():
        assert photo["event_stage"] is None
        assert photo["needs_stage_review"] is True
        assert photo["event_stage_confidence"] == 0.0

    # משתקף בפועל ב-DB (לא רק בתשובת ה-HTTP).
    import db as db_mod

    conn = db_mod.get_connection()
    try:
        row = conn.execute(
            "SELECT needs_stage_review FROM photos WHERE id = ?", (photo_a,)
        ).fetchone()
        assert row["needs_stage_review"] == 1
    finally:
        conn.close()


def test_tag_stages_empty_project_returns_200_with_no_photos(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)

    resp = test_client.post(f"/api/projects/{project_id}/tag-stages")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["photos"] == []
    assert body["needs_review_count"] == 0
