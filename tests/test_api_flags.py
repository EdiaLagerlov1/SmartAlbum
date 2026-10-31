"""
בדיקות API ל-F03 (issue #5): סימון תמונות חובה (must) / החרגה (exclude).

משתמש ב-Flask `app.test_client()` (לא httpx!) - ראה INTERFACES.md סעיף 1.

מכסה:
  (א) סימון 'must' מצליח ומוחזר בתשובה (עם must_count).
  (ב) סימון 'exclude' מצליח ומוחזר בתשובה.
  (ג) סימון ערך flag לא חוקי נדחה עם 400, בלי קריסה (500), ולא משנה את
      הערך הקיים ב-DB.
  (ד) מעבר must -> exclude -> none על אותה תמונה: העדכון האחרון מנצח
      (flag הוא עמודה יחידה, לא "שני flags" שנשארים).
  (ה) must_count מחושב נכון, כולל מקרה גבולי: בדיוק 60 זה בסדר
      (must_count_exceeds_limit=False), 61 זה over-limit (=True).
  (ו) PATCH לתמונה/פרויקט לא קיימים מחזיר 404 מסודר (JSON), בלי 500.
  (ז) GET רשימת תמונות עם ?flag= מסנן נכון, ובלי הפרמטר מתנהג כמקודם (F01).
  (ח) GET /must-count עצמאי (בלי לשנות flag) מחזיר ספירה נכונה.
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


def _make_jpeg_bytes(size=(40, 20), color="green") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """מגדיר storage root + DB זמניים *לפני* ייבוא main (כדי ש-db.init_db()
    שרץ באתחול המודול יפעל מול DB זמני, לא storage/smartalbum.db האמיתי)."""
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
    """מכניסה ישירה ל-DB (בלי לעבור את image_pipeline) - מהיר מאוד,
    מספיק כדי לבדוק ספירת must_count על הרבה תמונות (60/61) בלי להעלות
    קבצי JPEG אמיתיים אחד-אחד."""
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
# (א) סימון 'must' מצליח
# ---------------------------------------------------------------------------
def test_mark_must_succeeds(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)
    photo_id = _upload_photo(test_client, project_id)

    resp = test_client.patch(f"/api/projects/{project_id}/photos/{photo_id}", json={"flag": "must"})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["photo_id"] == photo_id
    assert body["flag"] == "must"
    assert body["must_count"] == 1
    assert body["must_count_exceeds_limit"] is False

    # משתקף גם ברשימת התמונות (F01 קיים, לא נשבר).
    list_resp = test_client.get(f"/api/projects/{project_id}/photos")
    photos = list_resp.get_json()
    assert photos[0]["flag"] == "must"


# ---------------------------------------------------------------------------
# (ב) סימון 'exclude' מצליח
# ---------------------------------------------------------------------------
def test_mark_exclude_succeeds(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)
    photo_id = _upload_photo(test_client, project_id)

    resp = test_client.patch(f"/api/projects/{project_id}/photos/{photo_id}", json={"flag": "exclude"})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["flag"] == "exclude"
    assert body["must_count"] == 0


# ---------------------------------------------------------------------------
# (ג) ערך flag לא חוקי נדחה עם 400, בלי קריסה, ובלי לשנות את הערך הקיים
# ---------------------------------------------------------------------------
def test_invalid_flag_value_rejected_with_400(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)
    photo_id = _upload_photo(test_client, project_id)

    # קודם מסמנים must כדי לוודא שהערך הלא-חוקי לא "דורס" אותו.
    ok_resp = test_client.patch(f"/api/projects/{project_id}/photos/{photo_id}", json={"flag": "must"})
    assert ok_resp.status_code == 200

    resp = test_client.patch(
        f"/api/projects/{project_id}/photos/{photo_id}", json={"flag": "both-must-and-exclude"}
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("error") is True
    assert body.get("error_reason") == "invalid_flag"

    # הערך הקודם (must) לא השתנה.
    list_resp = test_client.get(f"/api/projects/{project_id}/photos")
    photos = list_resp.get_json()
    assert photos[0]["flag"] == "must"


def test_missing_flag_field_rejected_with_400(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)
    photo_id = _upload_photo(test_client, project_id)

    resp = test_client.patch(f"/api/projects/{project_id}/photos/{photo_id}", json={})
    assert resp.status_code == 400
    assert resp.get_json().get("error") is True


# ---------------------------------------------------------------------------
# (ד) מעבר must -> exclude -> none: העדכון האחרון מנצח, אין "שני flags"
# ---------------------------------------------------------------------------
def test_flag_transitions_last_update_wins(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)
    photo_id = _upload_photo(test_client, project_id)

    r1 = test_client.patch(f"/api/projects/{project_id}/photos/{photo_id}", json={"flag": "must"})
    assert r1.get_json()["flag"] == "must"
    assert r1.get_json()["must_count"] == 1

    r2 = test_client.patch(f"/api/projects/{project_id}/photos/{photo_id}", json={"flag": "exclude"})
    assert r2.get_json()["flag"] == "exclude"
    assert r2.get_json()["must_count"] == 0  # לא נשאר "גם must" אחרי המעבר

    r3 = test_client.patch(f"/api/projects/{project_id}/photos/{photo_id}", json={"flag": "none"})
    assert r3.get_json()["flag"] == "none"
    assert r3.get_json()["must_count"] == 0

    list_resp = test_client.get(f"/api/projects/{project_id}/photos")
    photos = list_resp.get_json()
    assert photos[0]["flag"] == "none"


# ---------------------------------------------------------------------------
# (ו) 404 מסודר לתמונה/פרויקט לא קיימים
# ---------------------------------------------------------------------------
def test_patch_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.patch(
        "/api/projects/does-not-exist/photos/does-not-exist-either", json={"flag": "must"}
    )
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


def test_patch_nonexistent_photo_returns_404(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)
    resp = test_client.patch(
        f"/api/projects/{project_id}/photos/does-not-exist-either", json={"flag": "must"}
    )
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


# ---------------------------------------------------------------------------
# (ז) סינון רשימת תמונות לפי flag, ובלי הפרמטר - התנהגות קיימת (F01)
# ---------------------------------------------------------------------------
def test_list_photos_filtered_by_flag(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)
    photo_a = _upload_photo(test_client, project_id, "a.jpg")
    photo_b = _upload_photo(test_client, project_id, "b.jpg")

    test_client.patch(f"/api/projects/{project_id}/photos/{photo_a}", json={"flag": "must"})
    test_client.patch(f"/api/projects/{project_id}/photos/{photo_b}", json={"flag": "exclude"})

    all_resp = test_client.get(f"/api/projects/{project_id}/photos")
    assert len(all_resp.get_json()) == 2  # בלי פרמטר - לא נשבר (F01)

    must_resp = test_client.get(f"/api/projects/{project_id}/photos?flag=must")
    must_photos = must_resp.get_json()
    assert len(must_photos) == 1
    assert must_photos[0]["photo_id"] == photo_a

    exclude_resp = test_client.get(f"/api/projects/{project_id}/photos?flag=exclude")
    assert len(exclude_resp.get_json()) == 1
    assert exclude_resp.get_json()[0]["photo_id"] == photo_b


def test_list_photos_invalid_flag_filter_rejected_with_400(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)
    resp = test_client.get(f"/api/projects/{project_id}/photos?flag=not-a-real-flag")
    assert resp.status_code == 400
    assert resp.get_json().get("error") is True


# ---------------------------------------------------------------------------
# (ה) must_count - מקרה גבולי: 60 בסדר, 61 חוצה את הגבול.
# (ח) GET /must-count עצמאי, בלי לשנות flag.
# ---------------------------------------------------------------------------
def test_must_count_exactly_60_is_within_limit(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    for _ in range(60):
        _insert_raw_photo(main_mod, project_id, flag="must")

    resp = test_client.get(f"/api/projects/{project_id}/must-count")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["must_count"] == 60
    assert body["must_count_exceeds_limit"] is False


def test_must_count_61_exceeds_limit(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    for _ in range(61):
        _insert_raw_photo(main_mod, project_id, flag="must")

    resp = test_client.get(f"/api/projects/{project_id}/must-count")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["must_count"] == 61
    assert body["must_count_exceeds_limit"] is True


def test_must_count_reflected_in_patch_response_when_crossing_limit(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    for _ in range(60):
        _insert_raw_photo(main_mod, project_id, flag="must")

    # תמונה ה-61 מסומנת must דרך ה-API עצמו - התשובה חייבת לשקף את החציה.
    photo_id = _insert_raw_photo(main_mod, project_id, flag="none")
    resp = test_client.patch(f"/api/projects/{project_id}/photos/{photo_id}", json={"flag": "must"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["must_count"] == 61
    assert body["must_count_exceeds_limit"] is True


def test_must_count_for_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.get("/api/projects/does-not-exist/must-count")
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True
