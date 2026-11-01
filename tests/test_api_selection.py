"""
בדיקות API ל-F06 (issue #7): endpoint אלגוריתם הבחירה.

משתמש ב-Flask `app.test_client()` (לא httpx!) - ראה INTERFACES.md סעיף 1.
בודק רק את שכבת ה-HTTP (עטיפה דקה 1:1 מעל selection_engine.api.run_selection) -
האלגוריתם עצמו מכוסה ב-tests/test_selection_engine_selection.py.

מכסה:
  (א) POST /run-selection על פרויקט לא קיים -> 404 מסודר, לא 500.
  (ב) בחירה תקינה -> 200, status="ok", selected_photo_ids/selected_count
      משתקפים גם ב-DB (selected_for_album/selection_order).
  (ג) פחות מ-15 תמונות תקינות -> 200 (לא שגיאת HTTP), status="too_few_photos",
      selected_photo_ids=[], message לא ריק.
  (ד) יותר מ-60 must -> 200, status="too_many_must".
"""

from __future__ import annotations

import io
import os
import sys
import uuid

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


def _insert_raw_photo(main_mod, project_id: str, flag: str = "none") -> str:
    """ראה תבנית זהה ב-tests/test_api_flags.py - מכניסה ישירה ל-DB, מהיר
    מאוד לבדיקות עם הרבה תמונות (60/61+) בלי להעלות קבצי JPEG אמיתיים."""
    import db as db_mod

    photo_id = str(uuid.uuid4())
    conn = db_mod.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO photos (id, project_id, original_filename, original_path,
                                 uploaded_at, flag)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (photo_id, project_id, f"{photo_id}.jpg", f"/tmp/{photo_id}.jpg", "2024-01-01T00:00:00+00:00", flag),
        )
        conn.commit()
    finally:
        conn.close()
    return photo_id


# ---------------------------------------------------------------------------
# (א) פרויקט לא קיים -> 404 מסודר.
# ---------------------------------------------------------------------------
def test_run_selection_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.post("/api/projects/does-not-exist/run-selection")
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


# ---------------------------------------------------------------------------
# (ב) בחירה תקינה - 200, status="ok", משתקף ב-DB.
# ---------------------------------------------------------------------------
def test_run_selection_ok_reflected_in_response_and_db(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    photo_ids = [_insert_raw_photo(main_mod, project_id) for _ in range(16)]

    resp = test_client.post(f"/api/projects/{project_id}/run-selection")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()

    assert body["project_id"] == project_id
    assert body["status"] == "ok"
    assert set(body["selected_photo_ids"]) == set(photo_ids)
    assert body["selected_count"] == 16
    assert body["message"] is None

    list_resp = test_client.get(f"/api/projects/{project_id}/photos")
    assert list_resp.status_code == 200

    import db as db_mod

    conn = db_mod.get_connection()
    try:
        rows = conn.execute(
            "SELECT selected_for_album FROM photos WHERE project_id = ?", (project_id,)
        ).fetchall()
        assert all(row["selected_for_album"] == 1 for row in rows)
    finally:
        conn.close()


def test_run_selection_excludes_excluded_photos(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    kept_ids = [_insert_raw_photo(main_mod, project_id, flag="none") for _ in range(15)]
    excluded_id = _insert_raw_photo(main_mod, project_id, flag="exclude")

    resp = test_client.post(f"/api/projects/{project_id}/run-selection")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["status"] == "ok"
    assert excluded_id not in body["selected_photo_ids"]
    assert set(body["selected_photo_ids"]) == set(kept_ids)


def test_run_selection_must_photos_always_included(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    must_id = _insert_raw_photo(main_mod, project_id, flag="must")
    other_ids = [_insert_raw_photo(main_mod, project_id, flag="none") for _ in range(14)]

    resp = test_client.post(f"/api/projects/{project_id}/run-selection")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["status"] == "ok"
    assert must_id in body["selected_photo_ids"]
    assert set(body["selected_photo_ids"]) == {must_id} | set(other_ids)


# ---------------------------------------------------------------------------
# (ג) פחות מ-15 תמונות תקינות -> 200, status="too_few_photos".
# ---------------------------------------------------------------------------
def test_run_selection_too_few_photos(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    for _ in range(10):
        _insert_raw_photo(main_mod, project_id)

    resp = test_client.post(f"/api/projects/{project_id}/run-selection")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["status"] == "too_few_photos"
    assert body["selected_photo_ids"] == []
    assert body["selected_count"] == 0
    assert body["message"]


def test_run_selection_empty_project_is_too_few_photos(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)

    resp = test_client.post(f"/api/projects/{project_id}/run-selection")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "too_few_photos"


# ---------------------------------------------------------------------------
# (ד) יותר מ-60 must -> 200, status="too_many_must".
# ---------------------------------------------------------------------------
def test_run_selection_too_many_must(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    for _ in range(61):
        _insert_raw_photo(main_mod, project_id, flag="must")

    resp = test_client.post(f"/api/projects/{project_id}/run-selection")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["status"] == "too_many_must"
    assert body["selected_photo_ids"] == []
    assert body["message"]


def test_run_selection_with_real_uploads_end_to_end(client):
    """סניטי בסיסי מול תמונות שעברו בפועל את image_pipeline.process_upload
    (לא רק הכנסת שורות גולמיות) - מוודא שהעטיפה לא מתבססת על עמודות
    שה-pipeline האמיתי לא ממלא."""
    test_client, _main_mod = client
    project_id = _create_project(test_client)

    photo_ids = [_upload_photo(test_client, project_id, f"photo_{i}.jpg") for i in range(15)]

    resp = test_client.post(f"/api/projects/{project_id}/run-selection")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "ok"
    assert set(body["selected_photo_ids"]) == set(photo_ids)
