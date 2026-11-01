"""
בדיקות יחידה ל-layout_engine F08 (issue #9): regenerate_layout,
replace_photo_in_slot, change_template - וכן export_agent.snapshot_structure
(תלות-קדימה מכוונת של F08, ראה export_agent/api.py).

משתמש באותו דפוס fixture בדיוק כמו tests/test_layout_engine.py (DB זמני,
הכנסת שורות photos ישירות).

מכסה (לפי בקשת ה-issue):
  (א) regenerate_layout: מקפיץ version, מחליף (לא מצרף), שולף
      selected_photo_ids בעצמו מה-DB.
  (ב) replace_photo_in_slot: החלפה תקינה, דחיית exclude, דחיית תמונה
      מפרויקט אחר, slot_index לא חוקי, spread_id לא קיים.
  (ג) change_template: שינוי תקין, טיפול ב-slot-count mismatch (פחות/יותר
      סלוטים), דומיננטיות נשמרת, template_id לא קיים.
  (ד) snapshot_structure: JSON תקין משקף את ה-DB בפועל, נתיב נכון.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    db_path = storage_root / "smartalbum.db"
    monkeypatch.setenv("SMARTALBUM_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("SMARTALBUM_DB_PATH", str(db_path))

    for mod_name in (
        "storage",
        "db",
        "selection_engine",
        "selection_engine.api",
        "layout_engine",
        "layout_engine.api",
        "export_agent",
        "export_agent.api",
    ):
        sys.modules.pop(mod_name, None)

    import db as db_mod

    db_mod.init_db()

    import layout_engine.api as le_mod
    import export_agent.api as ea_mod
    import storage as storage_mod

    conn = db_mod.get_connection()
    yield le_mod, ea_mod, storage_mod, conn
    conn.close()


def _make_project(conn, name="חתונה") -> str:
    project_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO projects (id, name, created_at, status) VALUES (?, ?, ?, 'draft')",
        (project_id, name, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return project_id


def _insert_photo(
    conn,
    project_id: str,
    *,
    flag: str = "none",
    width: int | None = 100,
    height: int | None = 60,
    sharpness_score: float | None = None,
    exposure_score: float | None = None,
    selected_for_album: int = 1,
    selection_order: int | None = None,
) -> str:
    photo_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO photos (id, project_id, original_filename, original_path, uploaded_at,
                             flag, width, height, sharpness_score, exposure_score,
                             selected_for_album, selection_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            photo_id,
            project_id,
            f"{photo_id}.jpg",
            f"/tmp/{photo_id}.jpg",
            datetime.now(timezone.utc).isoformat(),
            flag,
            width,
            height,
            sharpness_score,
            exposure_score,
            selected_for_album,
            selection_order,
        ),
    )
    conn.commit()
    return photo_id


def _make_selected_photos(conn, project_id, count, **kwargs) -> list[str]:
    ids = []
    for i in range(count):
        ids.append(_insert_photo(conn, project_id, selection_order=i + 1, **kwargs))
    return ids


def _spreads(conn, project_id):
    return conn.execute(
        "SELECT id, position, template_id, version, updated_at FROM spreads "
        "WHERE project_id = ? ORDER BY position",
        (project_id,),
    ).fetchall()


def _spread_photos(conn, spread_id):
    return conn.execute(
        "SELECT photo_id, slot_index, is_dominant FROM spread_photos WHERE spread_id = ? "
        "ORDER BY slot_index",
        (spread_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# (א) regenerate_layout
# ---------------------------------------------------------------------------
def test_regenerate_layout_bumps_version_from_1_to_2(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 20)

    result1 = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result1.status == "ok"
    rows1 = _spreads(conn, project_id)
    assert all(row["version"] == 1 for row in rows1)

    result2 = le_mod.regenerate_layout(project_id, conn)
    assert result2.status == "ok", result2.message
    rows2 = _spreads(conn, project_id)
    assert len(rows2) == 15
    assert all(row["version"] == 2 for row in rows2)


def test_regenerate_layout_bumps_version_across_multiple_calls(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 20)

    le_mod.generate_layout(project_id, photo_ids, conn)
    le_mod.regenerate_layout(project_id, conn)
    le_mod.regenerate_layout(project_id, conn)
    result = le_mod.regenerate_layout(project_id, conn)

    assert result.status == "ok"
    rows = _spreads(conn, project_id)
    assert all(row["version"] == 4 for row in rows)


def test_regenerate_layout_replaces_not_appends(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 20)

    result1 = le_mod.generate_layout(project_id, photo_ids, conn)
    first_spread_ids = set(result1.spread_ids)

    result2 = le_mod.regenerate_layout(project_id, conn)

    rows = _spreads(conn, project_id)
    assert len(rows) == 15  # לא 30
    assert set(row["id"] for row in rows).isdisjoint(first_spread_ids)
    assert set(result2.spread_ids) == set(row["id"] for row in rows)


def test_regenerate_layout_rederives_selection_from_db(engine):
    """regenerate_layout לא מקבל selected_photo_ids כפרמטר - שולף מה-DB.
    משנים את ה-selected_for_album בין generate ל-regenerate ומוודאים
    שהתוצאה החדשה משקפת את מצב ה-DB העדכני, לא את מה שהועבר בהרצה
    הראשונה."""
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15)

    le_mod.generate_layout(project_id, photo_ids, conn)

    # מוסיפים עוד 15 תמונות ומסמנים אותן כנבחרות - "משתמשת הריצה run_selection
    # מחדש" (לא מדמים את run_selection עצמו, רק את התוצאה שלו ב-DB).
    extra = _make_selected_photos(conn, project_id, 15)
    for i, pid in enumerate(extra):
        conn.execute(
            "UPDATE photos SET selection_order = ? WHERE id = ?", (16 + i, pid)
        )
    conn.commit()

    result = le_mod.regenerate_layout(project_id, conn)
    assert result.status == "ok"

    all_placed = []
    for row in _spreads(conn, project_id):
        for sp in _spread_photos(conn, row["id"]):
            all_placed.append(sp["photo_id"])
    assert sorted(all_placed) == sorted(photo_ids + extra)


def test_regenerate_layout_without_prior_generate_starts_at_version_1(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15)

    result = le_mod.regenerate_layout(project_id, conn)

    assert result.status == "ok"
    rows = _spreads(conn, project_id)
    assert all(row["version"] == 1 for row in rows)


def test_regenerate_layout_too_few_selected_returns_error(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    _make_selected_photos(conn, project_id, 5)

    result = le_mod.regenerate_layout(project_id, conn)

    assert result.status == "error"
    assert _spreads(conn, project_id) == []


def test_regenerate_layout_accepts_explicit_duplicates_kwarg(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)

    dup_photo = _insert_photo(conn, project_id, selection_order=1)
    fillers = _make_selected_photos(conn, project_id, 29)
    for i, pid in enumerate(fillers):
        conn.execute("UPDATE photos SET selection_order = ? WHERE id = ?", (2 + i, pid))
    conn.commit()
    conn.execute(
        "UPDATE photos SET selection_order = 32 WHERE id = ?", (dup_photo,)
    )
    # לא ניתן לשים selection_order כפול באמת כדי לדמות הופעה כפולה כי זו
    # עמודה ב-photos (תמונה אחת = שורה אחת) - regenerate_layout שולף רשימה
    # בלי כפילויות מה-DB; ה-explicit_duplicates kwarg נבדק כאן רק מבחינת
    # "לא קורס/מועבר כראוי" - לא ניתן לבדוק כפילות אמיתית בלי selected_photo_ids
    # ידני (זה מכוסה ב-test_layout_engine.py על generate_layout עצמו).
    conn.commit()

    result = le_mod.regenerate_layout(
        project_id, conn, explicit_duplicates={dup_photo}
    )
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# (ב) replace_photo_in_slot
# ---------------------------------------------------------------------------
def test_replace_photo_in_slot_valid_replace(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15)
    le_mod.generate_layout(project_id, photo_ids, conn)

    spread = _spreads(conn, project_id)[0]
    old_photos = _spread_photos(conn, spread["id"])
    assert len(old_photos) == 1  # single-feature template
    slot_index = old_photos[0]["slot_index"]

    new_photo = _insert_photo(conn, project_id, selected_for_album=0)

    ok = le_mod.replace_photo_in_slot(spread["id"], slot_index, new_photo, conn)
    assert ok is True

    rows = _spread_photos(conn, spread["id"])
    assert len(rows) == 1
    assert rows[0]["photo_id"] == new_photo
    assert rows[0]["slot_index"] == slot_index
    # single-feature template -> הסלוט היחיד הוא דומיננטי
    assert rows[0]["is_dominant"] == 1

    updated_spread = conn.execute(
        "SELECT updated_at FROM spreads WHERE id = ?", (spread["id"],)
    ).fetchone()
    assert updated_spread["updated_at"] is not None


def test_replace_photo_in_slot_preserves_non_dominant_flag(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 30)  # duo template
    le_mod.generate_layout(project_id, photo_ids, conn)

    spread = _spreads(conn, project_id)[0]
    rows = _spread_photos(conn, spread["id"])
    non_dominant_slot = next(r["slot_index"] for r in rows if not r["is_dominant"])

    new_photo = _insert_photo(conn, project_id, selected_for_album=0)
    ok = le_mod.replace_photo_in_slot(spread["id"], non_dominant_slot, new_photo, conn)
    assert ok is True

    rows_after = _spread_photos(conn, spread["id"])
    replaced = next(r for r in rows_after if r["slot_index"] == non_dominant_slot)
    assert replaced["photo_id"] == new_photo
    assert replaced["is_dominant"] == 0
    # הדומיננטי המקורי לא נגע בו
    dominant_rows = [r for r in rows_after if r["is_dominant"]]
    assert len(dominant_rows) == 1


def test_replace_photo_in_slot_rejects_excluded_photo(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15)
    le_mod.generate_layout(project_id, photo_ids, conn)

    spread = _spreads(conn, project_id)[0]
    slot_index = _spread_photos(conn, spread["id"])[0]["slot_index"]
    excluded_photo = _insert_photo(conn, project_id, flag="exclude", selected_for_album=0)

    ok = le_mod.replace_photo_in_slot(spread["id"], slot_index, excluded_photo, conn)
    assert ok is False

    # לא השתנה כלום
    rows = _spread_photos(conn, spread["id"])
    assert rows[0]["photo_id"] != excluded_photo


def test_replace_photo_in_slot_rejects_photo_from_other_project(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    other_project_id = _make_project(conn, name="פרויקט אחר")
    photo_ids = _make_selected_photos(conn, project_id, 15)
    le_mod.generate_layout(project_id, photo_ids, conn)

    spread = _spreads(conn, project_id)[0]
    slot_index = _spread_photos(conn, spread["id"])[0]["slot_index"]
    foreign_photo = _insert_photo(conn, other_project_id, selected_for_album=0)

    ok = le_mod.replace_photo_in_slot(spread["id"], slot_index, foreign_photo, conn)
    assert ok is False


def test_replace_photo_in_slot_rejects_invalid_slot_index(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15)  # single-feature: slot 0 only
    le_mod.generate_layout(project_id, photo_ids, conn)

    spread = _spreads(conn, project_id)[0]
    new_photo = _insert_photo(conn, project_id, selected_for_album=0)

    ok = le_mod.replace_photo_in_slot(spread["id"], 5, new_photo, conn)
    assert ok is False


def test_replace_photo_in_slot_rejects_unknown_spread_id(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    new_photo = _insert_photo(conn, project_id, selected_for_album=0)

    ok = le_mod.replace_photo_in_slot(str(uuid.uuid4()), 0, new_photo, conn)
    assert ok is False


def test_replace_photo_in_slot_rejects_unknown_photo_id(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15)
    le_mod.generate_layout(project_id, photo_ids, conn)
    spread = _spreads(conn, project_id)[0]

    ok = le_mod.replace_photo_in_slot(spread["id"], 0, str(uuid.uuid4()), conn)
    assert ok is False


def test_replace_photo_in_slot_rejects_photo_already_in_another_slot_same_spread(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 30)  # duo: 2 slots
    le_mod.generate_layout(project_id, photo_ids, conn)

    spread = _spreads(conn, project_id)[0]
    rows = _spread_photos(conn, spread["id"])
    slot_a, slot_b = rows[0]["slot_index"], rows[1]["slot_index"]
    photo_in_slot_b = rows[1]["photo_id"]

    ok = le_mod.replace_photo_in_slot(spread["id"], slot_a, photo_in_slot_b, conn)
    assert ok is False


# ---------------------------------------------------------------------------
# (ג) change_template
# ---------------------------------------------------------------------------
def test_change_template_valid_change_same_slot_count(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 30, width=100, height=60)
    le_mod.generate_layout(project_id, photo_ids, conn)

    spread = _spreads(conn, project_id)[0]
    assert spread["template_id"] == "duo-landscape"
    old_rows = _spread_photos(conn, spread["id"])
    old_dominant = next(r["photo_id"] for r in old_rows if r["is_dominant"])

    ok = le_mod.change_template(spread["id"], "duo-portrait", conn)
    assert ok is True

    updated = conn.execute(
        "SELECT template_id FROM spreads WHERE id = ?", (spread["id"],)
    ).fetchone()
    assert updated["template_id"] == "duo-portrait"

    new_rows = _spread_photos(conn, spread["id"])
    assert len(new_rows) == 2
    assert {r["photo_id"] for r in new_rows} == {r["photo_id"] for r in old_rows}
    new_dominant = next(r["photo_id"] for r in new_rows if r["is_dominant"])
    assert new_dominant == old_dominant  # דומיננטי נשאר דומיננטי


def test_change_template_fewer_slots_drops_excess_photos(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 60)  # grid-mixed, 4 slots
    le_mod.generate_layout(project_id, photo_ids, conn)

    spread = _spreads(conn, project_id)[0]
    old_rows = _spread_photos(conn, spread["id"])
    assert len(old_rows) == 4
    old_dominant = next(r["photo_id"] for r in old_rows if r["is_dominant"])

    ok = le_mod.change_template(spread["id"], "single-feature-landscape", conn)
    assert ok is True

    new_rows = _spread_photos(conn, spread["id"])
    assert len(new_rows) == 1
    # הדומיננטי הקודם נשאר (יחיד, ממילא דומיננטי בתבנית החדשה)
    assert new_rows[0]["photo_id"] == old_dominant
    assert new_rows[0]["is_dominant"] == 1


def test_change_template_more_slots_leaves_extra_slots_empty(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15)  # single-feature, 1 slot
    le_mod.generate_layout(project_id, photo_ids, conn)

    spread = _spreads(conn, project_id)[0]
    old_rows = _spread_photos(conn, spread["id"])
    old_photo = old_rows[0]["photo_id"]

    ok = le_mod.change_template(spread["id"], "grid-mixed", conn)
    assert ok is True

    new_rows = _spread_photos(conn, spread["id"])
    assert len(new_rows) == 1  # רק הדומיננטי הקיים משובץ, 3 סלוטים ריקים
    assert new_rows[0]["photo_id"] == old_photo
    assert new_rows[0]["is_dominant"] == 1


def test_change_template_does_not_bump_spreads_version(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 30)
    le_mod.generate_layout(project_id, photo_ids, conn)
    spread = _spreads(conn, project_id)[0]
    assert spread["version"] == 1

    le_mod.change_template(spread["id"], "duo-portrait", conn)

    updated = conn.execute(
        "SELECT version FROM spreads WHERE id = ?", (spread["id"],)
    ).fetchone()
    assert updated["version"] == 1


def test_change_template_rejects_unknown_template_id(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15)
    le_mod.generate_layout(project_id, photo_ids, conn)
    spread = _spreads(conn, project_id)[0]

    ok = le_mod.change_template(spread["id"], "does-not-exist", conn)
    assert ok is False
    # לא השתנה כלום
    unchanged = conn.execute(
        "SELECT template_id FROM spreads WHERE id = ?", (spread["id"],)
    ).fetchone()
    assert unchanged["template_id"] == "single-feature-landscape"


def test_change_template_rejects_unknown_spread_id(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    ok = le_mod.change_template(str(uuid.uuid4()), "grid-mixed", conn)
    assert ok is False


# ---------------------------------------------------------------------------
# (ד) snapshot_structure
# ---------------------------------------------------------------------------
def test_snapshot_structure_writes_valid_json_reflecting_db(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15)
    le_mod.generate_layout(project_id, photo_ids, conn)

    path = ea_mod.snapshot_structure(project_id, conn)

    expected_path = storage_mod.export_structure_prev_path(project_id)
    assert path == str(expected_path)
    assert os.path.isfile(path)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    assert data["project_id"] == project_id
    assert "snapshotted_at" in data
    assert len(data["spreads"]) == 15

    db_rows = _spreads(conn, project_id)
    assert [s["id"] for s in data["spreads"]] == [row["id"] for row in db_rows]

    first_spread_json = data["spreads"][0]
    first_spread_db_photos = _spread_photos(conn, db_rows[0]["id"])
    assert len(first_spread_json["photos"]) == len(first_spread_db_photos)
    assert first_spread_json["photos"][0]["photo_id"] == first_spread_db_photos[0]["photo_id"]
    assert first_spread_json["photos"][0]["is_dominant"] is True


def test_snapshot_structure_empty_project_writes_empty_spreads_list(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)

    path = ea_mod.snapshot_structure(project_id, conn)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["spreads"] == []


def test_snapshot_structure_before_regenerate_captures_previous_version(engine):
    """זרימת F08 מלאה (סעיף 6 ב-INTERFACES.md): snapshot *לפני* regenerate -
    הקובץ חייב לשקף את המצב *הקודם* (version 1), לא את זה שנוצר אחרי."""
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 20)
    le_mod.generate_layout(project_id, photo_ids, conn)

    path = ea_mod.snapshot_structure(project_id, conn)
    with open(path, encoding="utf-8") as f:
        data_before = json.load(f)
    assert all(s["version"] == 1 for s in data_before["spreads"])

    le_mod.regenerate_layout(project_id, conn)

    # ה-DB עבר ל-version 2, אבל קובץ ה-snapshot (שלא נכתב מחדש) נשאר עם 1.
    with open(path, encoding="utf-8") as f:
        data_after_regenerate = json.load(f)
    assert all(s["version"] == 1 for s in data_after_regenerate["spreads"])

    rows_now = _spreads(conn, project_id)
    assert all(row["version"] == 2 for row in rows_now)
