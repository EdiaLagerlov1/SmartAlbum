"""
בדיקת E2E ל-issue #33: מסע המשתמשת המלא דרך ה-endpoints שהכפתורים החדשים
ב-UI (static/app.js) קוראים להם - upload -> tag-stages -> run-selection ->
generate-layout -> spreads.

לפני התיקון ב-issue #33, ל-UI לא היה שום כפתור שקורא ל-tag-stages/
run-selection/generate-layout (רק ל-regenerate-layout, שמניח שכבר יש
כפולות) - כפי שתועד ב-issue: "qa-agent's existing tests appear to call
these endpoints directly rather than walking the full upload->tag->select
->layout->export journey". הטסט הזה לא בודק JS (אין ריצת דפדפן/Cypress
בסביבה הזו) - הוא מאשר, בשכבת ה-HTTP, שהרצף המלא שה-UI *אחרי התיקון* קורא
לו (ראה app.js: runTagStages -> runSelection -> generateLayout) עובד
end-to-end על פרויקט אמיתי, לא רק כל endpoint בבדיקה מבודדת.
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


def _make_jpeg_bytes(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), color=color).save(buf, format="JPEG")
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
        "layout_engine",
        "layout_engine.api",
        "main",
    ):
        sys.modules.pop(mod_name, None)

    import main as main_mod

    main_mod.app.config["TESTING"] = True
    with main_mod.app.test_client() as test_client:
        yield test_client, main_mod


def test_full_journey_upload_tag_select_generate_layout(client):
    test_client, _main_mod = client

    # 1. פתיחת פרויקט (F01) - אותו endpoint ש-createProject ב-app.js קוראת.
    resp = test_client.post("/api/projects", json={"name": "חתונה - בדיקת E2E"})
    assert resp.status_code == 201, resp.get_json()
    project_id = resp.get_json()["project_id"]

    # 2. העלאת תמונות (F01/F02/F04) - 16 תמונות בצבעים שונים כדי שלא
    # ייפלו כולן לקבוצת-כפילויות אחת (similarity_group_id), ונישאר מעל
    # MIN_VALID_PHOTOS=15 אחרי run-selection.
    photo_ids = []
    for i in range(16):
        color = (i * 15 % 255, (i * 40 + 20) % 255, (i * 70 + 5) % 255)
        resp = test_client.post(
            f"/api/projects/{project_id}/photos",
            data={"file": (io.BytesIO(_make_jpeg_bytes(color)), f"wedding{i}.jpg", "image/jpeg")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["status"] == "done"
        photo_ids.append(body["photo_id"])
    assert len(photo_ids) == 16

    # 3. תיוג שלבי אירוע (F05) - הכפתור "תיוג שלבי אירוע" (tagStagesBtn).
    resp = test_client.post(f"/api/projects/{project_id}/tag-stages")
    assert resp.status_code == 200, resp.get_json()
    tag_body = resp.get_json()
    assert tag_body["project_id"] == project_id
    assert len(tag_body["photos"]) == 16

    # 4. הרצת בחירה (F06) - הכפתור "הרצת בחירת תמונות לאלבום" (runSelectionBtn).
    resp = test_client.post(f"/api/projects/{project_id}/run-selection")
    assert resp.status_code == 200, resp.get_json()
    selection_body = resp.get_json()
    assert selection_body["status"] == "ok", selection_body
    assert selection_body["selected_count"] >= 15
    assert set(selection_body["selected_photo_ids"]).issubset(set(photo_ids))

    # 5. יצירת כפולות לראשונה (F07) - הכפתור "יצירת כפולות" (generateLayoutBtn),
    # *לא* /regenerate-layout (שמניח שכבר יש כפולות קיימות).
    resp = test_client.post(f"/api/projects/{project_id}/generate-layout")
    assert resp.status_code == 200, resp.get_json()
    layout_body = resp.get_json()
    assert layout_body["status"] == "ok", layout_body
    assert layout_body["spread_count"] == 15

    # 6. GET /spreads (F08, קריאה ל-loadSpreads) משקף את מה שנוצר - זה מה
    # ש-UI משתמש בו כדי להחליט אם להציג "spreads-actions-initial" (ריק)
    # או "spreads-actions-existing" (יש כפולות) - ראה
    # updateSpreadsActionsVisibility ב-app.js.
    resp = test_client.get(f"/api/projects/{project_id}/spreads")
    assert resp.status_code == 200
    spreads_body = resp.get_json()
    assert len(spreads_body["spreads"]) == 15

    # 7. מצב עיבוד (F10) - GET /status חייב לשקף גם tagging/selection/layout
    # כ-"done" (לא ספינר כללי אחד) - זה מה ש-loadStatus מציגה אחרי כל
    # שלב ב-app.js.
    resp = test_client.get(f"/api/projects/{project_id}/status")
    assert resp.status_code == 200
    status_body = resp.get_json()
    for stage in ("tagging", "selection", "layout"):
        assert status_body["stages"][stage]["status"] == "done", status_body


def test_run_selection_too_few_photos_blocks_generate_layout(client):
    """
    פחות מ-15 תמונות תקינות -> run-selection מחזיר status="too_few_photos"
    ולא כותב selected_for_album; generate-layout שלאחר מכן (כמו שה-UI
    עדיין יכול לקרוא לו אם המשתמשת מתעלמת מההודעה) מחזיר status="error"
    כי אין תמונות נבחרות - לא קורס, לא יוצר 15 כפולות ריקות.
    """
    test_client, _main_mod = client

    resp = test_client.post("/api/projects", json={"name": "פרויקט קטן"})
    project_id = resp.get_json()["project_id"]

    for i in range(3):
        color = (i * 50, 10, 200)
        resp = test_client.post(
            f"/api/projects/{project_id}/photos",
            data={"file": (io.BytesIO(_make_jpeg_bytes(color)), f"p{i}.jpg", "image/jpeg")},
            content_type="multipart/form-data",
        )
        assert resp.get_json()["status"] == "done"

    resp = test_client.post(f"/api/projects/{project_id}/run-selection")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "too_few_photos"
    assert body["selected_photo_ids"] == []

    resp = test_client.post(f"/api/projects/{project_id}/generate-layout")
    assert resp.status_code == 200
    layout_body = resp.get_json()
    assert layout_body["status"] == "error"
    assert layout_body["spread_count"] == 0
