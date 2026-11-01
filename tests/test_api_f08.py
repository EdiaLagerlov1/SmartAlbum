"""
בדיקות API ל-F08 (issue #9): endpoints ב-main.py שעוטפים
layout_engine.api.regenerate_layout / replace_photo_in_slot / change_template
וכן export_agent.api.snapshot_structure, לפי החוזה המלא ב-INTERFACES.md
סעיף 4.3/4.4/6.

משתמש ב-Flask `app.test_client()` (לא httpx) - אותו דפוס בדיוק כמו
tests/test_api_layout.py. בודק רק את שכבת ה-HTTP (עטיפות דקות) - האלגוריתם/
קונבנציות הכשל עצמם מכוסים ב-tests/test_layout_engine_f08.py.

מכסה:
  (א) GET /spreads - פרויקט לא קיים -> 404; רשימה תקינה אחרי generate-layout.
  (ב) POST /replace-photo - הצלחה, 404 כשל פרויקט/כפולה לא קיימים, 400 שדות
      חסרים, status="error" (200) על כשל-ולידציה (exclude/slot_index לא חוקי/
      פרויקט אחר).
  (ג) POST /change-template - הצלחה (כולל reconciliation), status="error"
      (200) על template_id לא קיים, 404 על כפולה לא קיימת.
  (ד) POST /regenerate-layout - מממש את סעיף 6 בפועל: snapshot_structure
      *לפני* regenerate_layout (קובץ album_structure_prev.json קיים ומשקף
      את המבנה הקודם, רק אחר כך spreads.version מוקפץ ב-DB), version מוקפץ,
      status="ok"/"error".
  (ה) GET /structure-prev - 404 לפני snapshot ראשון, 200 עם תוכן תקין אחרי.
  (ו) GET /api/templates - קטלוג 6 התבניות.
"""

from __future__ import annotations

import json
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
        "export_agent",
        "export_agent.api",
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


def _generate_layout(test_client, project_id):
    resp = test_client.post(f"/api/projects/{project_id}/generate-layout")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok", body
    return body


def _get_db_conn():
    import db as db_mod

    return db_mod.get_connection()


# ---------------------------------------------------------------------------
# (א) GET /spreads
# ---------------------------------------------------------------------------
def test_list_spreads_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.get("/api/projects/does-not-exist/spreads")
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


def test_list_spreads_after_generate_layout(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    _generate_layout(test_client, project_id)

    resp = test_client.get(f"/api/projects/{project_id}/spreads")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["project_id"] == project_id
    assert len(body["spreads"]) == 15

    first = body["spreads"][0]
    assert first["position"] == 1
    assert first["template_id"]
    assert first["slot_count"] >= 1
    assert isinstance(first["photos"], list)
    assert len(first["photos"]) >= 1
    photo = first["photos"][0]
    assert set(photo.keys()) == {
        "slot_index",
        "photo_id",
        "is_dominant",
        "original_filename",
        "thumbnail_url",
    }


# ---------------------------------------------------------------------------
# (ב) POST /replace-photo
# ---------------------------------------------------------------------------
def test_replace_photo_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.post(
        "/api/projects/does-not-exist/spreads/spread-x/replace-photo",
        json={"slot_index": 0, "new_photo_id": "photo-x"},
    )
    assert resp.status_code == 404
    assert resp.get_json()["error_reason"] == "project_not_found"


def test_replace_photo_nonexistent_spread_returns_404(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/does-not-exist/replace-photo",
        json={"slot_index": 0, "new_photo_id": "photo-x"},
    )
    assert resp.status_code == 404
    assert resp.get_json()["error_reason"] == "spread_not_found"


def test_replace_photo_missing_fields_returns_400(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    layout_body = _generate_layout(test_client, project_id)
    spread_id = layout_body["spread_ids"][0]

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{spread_id}/replace-photo", json={}
    )
    assert resp.status_code == 400


def test_replace_photo_success(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    photo_ids = _make_selected_photos(main_mod, project_id, 20)
    layout_body = _generate_layout(test_client, project_id)
    spread_id = layout_body["spread_ids"][0]

    # תמונה שלא נבחרה/לא שובצה בכפולה הזו, ושאינה מוחרגת - מועמדת תקינה.
    extra_photo = _insert_raw_photo(main_mod, project_id, selected_for_album=0)

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{spread_id}/replace-photo",
        json={"slot_index": 0, "new_photo_id": extra_photo},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok", body
    assert body["message"] is None

    conn = _get_db_conn()
    try:
        row = conn.execute(
            "SELECT photo_id FROM spread_photos WHERE spread_id = ? AND slot_index = 0",
            (spread_id,),
        ).fetchone()
        assert row["photo_id"] == extra_photo
    finally:
        conn.close()


def test_replace_photo_with_excluded_photo_returns_error_status_200(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    layout_body = _generate_layout(test_client, project_id)
    spread_id = layout_body["spread_ids"][0]

    excluded_photo = _insert_raw_photo(main_mod, project_id, flag="exclude")

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{spread_id}/replace-photo",
        json={"slot_index": 0, "new_photo_id": excluded_photo},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["message"]


def test_replace_photo_from_other_project_returns_error_status_200(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    other_project_id = _create_project(test_client, name="פרויקט אחר")
    _make_selected_photos(main_mod, project_id, 20)
    layout_body = _generate_layout(test_client, project_id)
    spread_id = layout_body["spread_ids"][0]

    foreign_photo = _insert_raw_photo(main_mod, other_project_id)

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{spread_id}/replace-photo",
        json={"slot_index": 0, "new_photo_id": foreign_photo},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"


def test_replace_photo_invalid_slot_index_returns_error_status_200(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    layout_body = _generate_layout(test_client, project_id)
    spread_id = layout_body["spread_ids"][0]
    extra_photo = _insert_raw_photo(main_mod, project_id)

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{spread_id}/replace-photo",
        json={"slot_index": 999, "new_photo_id": extra_photo},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"


# ---------------------------------------------------------------------------
# (ג) POST /change-template
# ---------------------------------------------------------------------------
def test_change_template_nonexistent_spread_returns_404(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/does-not-exist/change-template",
        json={"new_template_id": "trio-mixed"},
    )
    assert resp.status_code == 404
    assert resp.get_json()["error_reason"] == "spread_not_found"


def test_change_template_missing_field_returns_400(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    layout_body = _generate_layout(test_client, project_id)
    spread_id = layout_body["spread_ids"][0]

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{spread_id}/change-template", json={}
    )
    assert resp.status_code == 400


def test_change_template_success(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    layout_body = _generate_layout(test_client, project_id)
    spread_id = layout_body["spread_ids"][0]

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{spread_id}/change-template",
        json={"new_template_id": "grid-mixed"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok", body

    conn = _get_db_conn()
    try:
        row = conn.execute(
            "SELECT template_id FROM spreads WHERE id = ?", (spread_id,)
        ).fetchone()
        assert row["template_id"] == "grid-mixed"
    finally:
        conn.close()


def test_change_template_nonexistent_template_id_returns_error_status_200(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    layout_body = _generate_layout(test_client, project_id)
    spread_id = layout_body["spread_ids"][0]

    resp = test_client.post(
        f"/api/projects/{project_id}/spreads/{spread_id}/change-template",
        json={"new_template_id": "no-such-template"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["message"]


# ---------------------------------------------------------------------------
# (ד) POST /regenerate-layout - חוזה סעיף 6: snapshot *לפני* regenerate.
# ---------------------------------------------------------------------------
def test_regenerate_layout_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.post("/api/projects/does-not-exist/regenerate-layout")
    assert resp.status_code == 404


def test_regenerate_layout_writes_snapshot_before_bumping_version(client):
    test_client, main_mod = client
    import storage as storage_mod

    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    layout_body = _generate_layout(test_client, project_id)
    first_spread_ids = set(layout_body["spread_ids"])

    # לפני regenerate - עדיין אין קובץ snapshot (snapshot_structure לא נקרא
    # עדיין, generate_layout לא קורא לו).
    snapshot_path = storage_mod.export_structure_prev_path(project_id)
    assert not snapshot_path.is_file()

    resp = test_client.post(f"/api/projects/{project_id}/regenerate-layout")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok", body
    assert body["spread_count"] == 15
    assert body["snapshot_path"] == str(snapshot_path)

    # ה-snapshot נכתב, ומשקף את ה-15 כפולות *הקודמות* (version=1, לפני
    # ה-regenerate) - לא את הכפולות החדשות (version=2).
    assert snapshot_path.is_file()
    snapshot_data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot_data["project_id"] == project_id
    assert len(snapshot_data["spreads"]) == 15
    assert all(s["version"] == 1 for s in snapshot_data["spreads"])
    snapshotted_spread_ids = {s["id"] for s in snapshot_data["spreads"]}
    assert snapshotted_spread_ids == first_spread_ids

    # ואילו ה-DB בפועל כבר מראה את הגרסה החדשה (version=2) עם spread_ids
    # חדשים - regenerate_layout רץ *אחרי* ה-snapshot, לא לפניו.
    conn = _get_db_conn()
    try:
        rows = conn.execute(
            "SELECT id, version FROM spreads WHERE project_id = ?", (project_id,)
        ).fetchall()
        assert len(rows) == 15
        assert all(row["version"] == 2 for row in rows)
        assert {row["id"] for row in rows}.isdisjoint(first_spread_ids)
    finally:
        conn.close()

    assert set(body["spread_ids"]) == {row_id for row_id in body["spread_ids"]}
    assert len(body["spread_ids"]) == 15


def test_regenerate_layout_too_few_photos_returns_error_status_200(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    _generate_layout(test_client, project_id)

    # מורידים את מספר התמונות הנבחרות מתחת ל-15 לפני regenerate.
    conn = _get_db_conn()
    try:
        conn.execute(
            "UPDATE photos SET selected_for_album = 0 WHERE project_id = ? "
            "AND selection_order > 5",
            (project_id,),
        )
        conn.commit()
    finally:
        conn.close()

    resp = test_client.post(f"/api/projects/{project_id}/regenerate-layout")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["spread_ids"] == []


# ---------------------------------------------------------------------------
# (ה) GET /structure-prev
# ---------------------------------------------------------------------------
def test_get_structure_prev_before_any_snapshot_returns_404(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    _generate_layout(test_client, project_id)

    resp = test_client.get(f"/api/projects/{project_id}/structure-prev")
    assert resp.status_code == 404
    assert resp.get_json()["error_reason"] == "no_snapshot"


def test_get_structure_prev_after_regenerate_returns_snapshot_content(client):
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    _generate_layout(test_client, project_id)

    regen_resp = test_client.post(f"/api/projects/{project_id}/regenerate-layout")
    assert regen_resp.status_code == 200

    resp = test_client.get(f"/api/projects/{project_id}/structure-prev")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["project_id"] == project_id
    assert len(body["spreads"]) == 15


def test_get_structure_prev_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.get("/api/projects/does-not-exist/structure-prev")
    assert resp.status_code == 404
    assert resp.get_json()["error_reason"] == "project_not_found"


# ---------------------------------------------------------------------------
# (ו) GET /api/templates
# ---------------------------------------------------------------------------
def test_list_templates_returns_catalog(client):
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
    for tpl in body["templates"]:
        assert "slot_count" in tpl
        assert "slots" in tpl
