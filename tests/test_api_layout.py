"""
בדיקות API ל-F07 (issue #8): endpoint יצירת הכפולות, ו-F08 (issue #9):
endpoints להחלפת תמונה/שינוי תבנית/קריאת מצב הכפולות.

משתמש ב-Flask `app.test_client()` (לא httpx) - ראה INTERFACES.md סעיף 1.
בודק רק את שכבת ה-HTTP (עטיפה דקה 1:1 מעל layout_engine.api.generate_layout/
replace_photo_in_slot/change_template/get_spreads) - האלגוריתם עצמו מכוסה
ב-tests/test_layout_engine.py ו-tests/test_layout_engine_swap.py.

מכסה:
  (א) POST /generate-layout על פרויקט לא קיים -> 404 מסודר.
  (ב) יצירה תקינה (אחרי run-selection אמיתי) -> 200, status="ok",
      spread_ids/spread_count משתקפים גם ב-DB.
  (ג) פחות מ-15 תמונות נבחרות -> 200 (לא שגיאת HTTP), status="error".
  (ד) הרצה חוזרת מחליפה את הכפולות הקודמות.
  (ה) F08: GET /spreads - מצב ראשוני, ואחרי swap/שינוי תבנית ("שחזור").
  (ו) F08: POST /replace-photo - הצלחה, דחייה (exclude), דחייה (לא בפרויקט),
      404 על פרויקט לא קיים, 400 על body חסר.
  (ז) F08: POST /change-template - הצלחה, דחייה (slot_count_mismatch),
      404 על פרויקט לא קיים, 400 על body חסר.
  (ח) GET /api/templates - קטלוג שלם.
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


# ===========================================================================
# F08 (issue #9): replace-photo / change-template / GET spreads / templates.
# ===========================================================================
def _generate_full_layout(test_client, main_mod, project_id: str, count: int = 60) -> dict:
    """בונה פרויקט עם `count` תמונות נבחרות (60 כברירת מחדל -> grid-mixed,
    4 תמונות בדיוק לכל כפולה - נוח ל-change-template) וקורא ל-generate-layout
    האמיתי (דרך ה-API), מחזיר את ה-body של generate-layout."""
    _make_selected_photos(main_mod, project_id, count)
    resp = test_client.post(f"/api/projects/{project_id}/generate-layout")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok", body
    return body


def _get_spreads(test_client, project_id: str) -> list[dict]:
    resp = test_client.get(f"/api/projects/{project_id}/spreads")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["spreads"]


# ---------------------------------------------------------------------------
# (ה) GET /spreads - מצב ראשוני ושחזור אחרי swap/שינוי תבנית.
# ---------------------------------------------------------------------------
def test_get_spreads_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.get("/api/projects/does-not-exist/spreads")
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


def test_get_spreads_returns_current_state_with_thumbnail_fields(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _generate_full_layout(test_client, main_mod, project_id)

    spreads = _get_spreads(test_client, project_id)
    assert len(spreads) == 15
    assert [s["position"] for s in spreads] == list(range(1, 16))
    first = spreads[0]
    assert first["template_id"] == "grid-mixed"
    assert len(first["photos"]) == 4
    for p in first["photos"]:
        assert "thumbnail_url" in p  # None כי אין thumb_path אמיתי בטסט הזה
        assert "width" in p and "height" in p


# ---------------------------------------------------------------------------
# (ו) POST /replace-photo.
# ---------------------------------------------------------------------------
def test_replace_photo_route_success_and_reflected_on_reload(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _generate_full_layout(test_client, main_mod, project_id)

    spreads_before = _get_spreads(test_client, project_id)
    first_spread = spreads_before[0]
    old_photo_id = first_spread["photos"][0]["photo_id"]
    new_photo_id = _insert_raw_photo(main_mod, project_id)

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{first_spread['spread_id']}/replace-photo",
        json={"old_photo_id": old_photo_id, "new_photo_id": new_photo_id},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok", body
    assert body["error_reason"] is None
    assert body["spread_id"] == first_spread["spread_id"]

    # "שחזור" - קריאה חדשה ל-GET /spreads משקפת את ההחלפה.
    spreads_after = _get_spreads(test_client, project_id)
    first_after = [s for s in spreads_after if s["spread_id"] == first_spread["spread_id"]][0]
    placed_ids = {p["photo_id"] for p in first_after["photos"]}
    assert new_photo_id in placed_ids
    assert old_photo_id not in placed_ids
    assert first_after["version"] == first_spread["version"] + 1


def test_replace_photo_route_rejected_for_excluded_photo(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _generate_full_layout(test_client, main_mod, project_id)

    spreads = _get_spreads(test_client, project_id)
    first_spread = spreads[0]
    old_photo_id = first_spread["photos"][0]["photo_id"]
    excluded_photo_id = _insert_raw_photo(main_mod, project_id, flag="exclude")

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{first_spread['spread_id']}/replace-photo",
        json={"old_photo_id": old_photo_id, "new_photo_id": excluded_photo_id},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["error_reason"] == "new_photo_excluded"


def test_replace_photo_route_rejected_for_photo_not_in_project(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _generate_full_layout(test_client, main_mod, project_id)

    other_project_id = _create_project(test_client, name="פרויקט אחר")
    foreign_photo_id = _insert_raw_photo(main_mod, other_project_id)

    spreads = _get_spreads(test_client, project_id)
    first_spread = spreads[0]
    old_photo_id = first_spread["photos"][0]["photo_id"]

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{first_spread['spread_id']}/replace-photo",
        json={"old_photo_id": old_photo_id, "new_photo_id": foreign_photo_id},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["error_reason"] == "new_photo_not_found"


def test_replace_photo_route_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.post(
        "/api/projects/does-not-exist/spreads/some-spread/replace-photo",
        json={"old_photo_id": "a", "new_photo_id": "b"},
    )
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


def test_replace_photo_route_missing_body_fields_returns_400(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _generate_full_layout(test_client, main_mod, project_id)
    spreads = _get_spreads(test_client, project_id)

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{spreads[0]['spread_id']}/replace-photo",
        json={"old_photo_id": "only-old"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# (ז) POST /change-template.
# ---------------------------------------------------------------------------
def test_change_template_route_success_and_reflected_on_reload(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _generate_full_layout(test_client, main_mod, project_id)  # grid-mixed, 4 תמונות

    spreads = _get_spreads(test_client, project_id)
    first_spread = spreads[0]
    assert first_spread["template_id"] == "grid-mixed"

    # מצמצם ל-2 תמונות דרך DB ישירות כדי שיתאים ל-duo-landscape.
    import db as db_mod

    conn = db_mod.get_connection()
    try:
        conn.execute(
            "DELETE FROM spread_photos WHERE spread_id = ? AND slot_index IN (2, 3)",
            (first_spread["spread_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{first_spread['spread_id']}/change-template",
        json={"template_id": "duo-landscape"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok", body
    assert body["template_id"] == "duo-landscape"

    spreads_after = _get_spreads(test_client, project_id)
    first_after = [s for s in spreads_after if s["spread_id"] == first_spread["spread_id"]][0]
    assert first_after["template_id"] == "duo-landscape"
    assert len(first_after["photos"]) == 2
    assert first_after["version"] == first_spread["version"] + 1


def test_change_template_route_rejected_for_slot_count_mismatch(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _generate_full_layout(test_client, main_mod, project_id)  # grid-mixed, 4 תמונות

    spreads = _get_spreads(test_client, project_id)
    first_spread = spreads[0]

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{first_spread['spread_id']}/change-template",
        json={"template_id": "trio-mixed"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["error_reason"] == "slot_count_mismatch"


def test_change_template_route_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.post(
        "/api/projects/does-not-exist/spreads/some-spread/change-template",
        json={"template_id": "trio-mixed"},
    )
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


def test_change_template_route_missing_body_returns_400(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _generate_full_layout(test_client, main_mod, project_id)
    spreads = _get_spreads(test_client, project_id)

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{spreads[0]['spread_id']}/change-template",
        json={},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# (ח) GET /api/templates.
# ---------------------------------------------------------------------------
def test_list_templates_route_returns_full_catalog(client):
    test_client, _main_mod = client
    resp = test_client.get("/api/templates")
    assert resp.status_code == 200
    body = resp.get_json()
    template_ids = {tpl["id"] for tpl in body["templates"]}
    assert template_ids == {
        "single-feature-landscape",
        "single-feature-portrait",
        "duo-landscape",
        "duo-portrait",
        "trio-mixed",
        "grid-mixed",
    }
