"""
בדיקות API ל-F07 (issue #8): endpoint יצירת הכפולות.

משתמש ב-Flask `app.test_client()` (לא httpx) - ראה INTERFACES.md סעיף 1.
בודק רק את שכבת ה-HTTP (עטיפה דקה 1:1 מעל layout_engine.api.generate_layout,
כולל שליפת selected_photo_ids לפי selected_for_album/selection_order) -
האלגוריתם עצמו מכוסה ב-tests/test_layout_engine.py.

מכסה:
  (א) POST /generate-layout על פרויקט לא קיים -> 404 מסודר.
  (ב) יצירה תקינה (אחרי run-selection אמיתי) -> 200, status="ok",
      spread_ids/spread_count משתקפים גם ב-DB.
  (ג) פחות מ-15 תמונות נבחרות -> 200 (לא שגיאת HTTP), status="error".
  (ד) הרצה חוזרת מחליפה את הכפולות הקודמות.
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


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


def _create_project(client, name="חתונה של דנה ויוסי") -> str:
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["project_id"]


def _insert_raw_photo(
    main_mod,
    project_id: str,
    *,
    flag: str = "none",
    selected_for_album: int = 0,
    selection_order: int | None = None,
) -> str:
    """ראה תבנית זהה ב-tests/test_api_selection.py - מכניסה ישירה ל-DB."""
    import db as db_mod

    photo_id = str(uuid.uuid4())
    conn = db_mod.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO photos (id, project_id, original_filename, original_path,
                                 uploaded_at, flag, selected_for_album, selection_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                photo_id,
                project_id,
                f"{photo_id}.jpg",
                f"/tmp/{photo_id}.jpg",
                "2024-01-01T00:00:00+00:00",
                flag,
                selected_for_album,
                selection_order,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return photo_id


def _make_selected_photos(main_mod, project_id, count) -> list[str]:
    return [
        _insert_raw_photo(main_mod, project_id, selected_for_album=1, selection_order=i + 1)
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# (א) פרויקט לא קיים -> 404 מסודר.
# ---------------------------------------------------------------------------
def test_generate_layout_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.post("/api/projects/does-not-exist/generate-layout")
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


# ---------------------------------------------------------------------------
# (ב) יצירה תקינה - 200, status="ok", משתקף ב-DB. משתמש בזרימה האמיתית
# (run-selection ואז generate-layout) - לא רק הכנסת שורות גולמיות.
# ---------------------------------------------------------------------------
def test_generate_layout_ok_after_real_run_selection(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    for _ in range(20):
        _insert_raw_photo(main_mod, project_id)

    sel_resp = test_client.post(f"/api/projects/{project_id}/run-selection")
    assert sel_resp.status_code == 200
    assert sel_resp.get_json()["status"] == "ok"

    resp = test_client.post(f"/api/projects/{project_id}/generate-layout")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()

    assert body["project_id"] == project_id
    assert body["status"] == "ok"
    assert body["spread_count"] == 15
    assert len(body["spread_ids"]) == 15
    assert body["message"] is None

    import db as db_mod

    conn = db_mod.get_connection()
    try:
        spread_rows = conn.execute(
            "SELECT id, position, template_id FROM spreads WHERE project_id = ? ORDER BY position",
            (project_id,),
        ).fetchall()
        assert len(spread_rows) == 15
        assert [row["position"] for row in spread_rows] == list(range(1, 16))

        placed_photo_ids = conn.execute(
            """SELECT sp.photo_id FROM spread_photos sp
               JOIN spreads s ON sp.spread_id = s.id WHERE s.project_id = ?""",
            (project_id,),
        ).fetchall()
        assert len(placed_photo_ids) == 20
        assert len({row["photo_id"] for row in placed_photo_ids}) == 20
    finally:
        conn.close()


def test_generate_layout_directly_with_preselected_photos(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    photo_ids = _make_selected_photos(main_mod, project_id, 30)

    resp = test_client.post(f"/api/projects/{project_id}/generate-layout")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["status"] == "ok"
    assert body["spread_count"] == 15

    import db as db_mod

    conn = db_mod.get_connection()
    try:
        placed = conn.execute(
            """SELECT sp.photo_id FROM spread_photos sp
               JOIN spreads s ON sp.spread_id = s.id WHERE s.project_id = ?""",
            (project_id,),
        ).fetchall()
        assert sorted(row["photo_id"] for row in placed) == sorted(photo_ids)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (ג) פחות מ-15 תמונות נבחרות -> 200, status="error".
# ---------------------------------------------------------------------------
def test_generate_layout_too_few_selected_photos(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    _make_selected_photos(main_mod, project_id, 10)

    resp = test_client.post(f"/api/projects/{project_id}/generate-layout")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["status"] == "error"
    assert body["spread_ids"] == []
    assert body["spread_count"] == 0
    assert body["message"]


def test_generate_layout_no_selection_at_all_returns_error(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)

    resp = test_client.post(f"/api/projects/{project_id}/generate-layout")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"


# ---------------------------------------------------------------------------
# (ד) הרצה חוזרת מחליפה את הכפולות הקודמות - לא מצרפת/מכפילה.
# ---------------------------------------------------------------------------
def test_generate_layout_rerun_replaces_previous_spreads(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    _make_selected_photos(main_mod, project_id, 20)

    resp1 = test_client.post(f"/api/projects/{project_id}/generate-layout")
    assert resp1.status_code == 200
    first_spread_ids = set(resp1.get_json()["spread_ids"])

    resp2 = test_client.post(f"/api/projects/{project_id}/generate-layout")
    assert resp2.status_code == 200
    body2 = resp2.get_json()
    assert body2["status"] == "ok"
    assert body2["spread_count"] == 15

    import db as db_mod

    conn = db_mod.get_connection()
    try:
        rows = conn.execute(
            "SELECT id FROM spreads WHERE project_id = ?", (project_id,)
        ).fetchall()
        assert len(rows) == 15  # לא 30
        assert set(row["id"] for row in rows).isdisjoint(first_spread_ids)
    finally:
        conn.close()
