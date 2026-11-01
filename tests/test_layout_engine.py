"""
בדיקות יחידה ל-layout_engine F07 (issue #8): יצירת 15 כפולות והתאמת תבנית
(`generate_layout`).

משתמש ב-DB זמני, בדיוק כמו tests/test_selection_engine_selection.py - מכניס
שורות `photos` ישירות (עם width/height/sharpness_score/exposure_score
קבועים), כדי לבדוק במדויק חלוקה/דומיננטיות/בחירת-תבנית בלי להעלות קבצי
JPEG אמיתיים.

מכסה (לפי בקשת ה-issue):
  (א) בדיוק 15 שורות spreads, position 1..15, לכל קלט תקין (15..60 תמונות).
  (ב) כל תמונה מופיעה פעם אחת בכל האלבום כברירת מחדל (לא מוכפלת).
  (ג) תמונה דומיננטית = האיכות הגבוהה ביותר (sharpness/exposure) בכפולה.
  (ד) בחירת תבנית מגיבה לתערובת הכיוונים (landscape/portrait) בכפולה.
  (ה) הרצה חוזרת מחליפה את הכפולות הקודמות (לא מצרפת/לא מכפילה).
  (ו) פחות מ-15 תמונות נבחרות -> status="error", לא נכתב כלום.
  (ז) יותר מ-60 תמונות נבחרות (או >60 must) -> status="error".
  (ח) explicit_duplicates מאפשר לתמונה בודדת להופיע בשתי כפולות שונות.
"""

from __future__ import annotations

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
    ):
        sys.modules.pop(mod_name, None)

    import db as db_mod

    db_mod.init_db()

    import layout_engine.api as le_mod

    conn = db_mod.get_connection()
    yield le_mod, conn
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
        ids.append(
            _insert_photo(conn, project_id, selection_order=i + 1, **kwargs)
        )
    return ids


def _spreads(conn, project_id):
    return conn.execute(
        "SELECT id, position, template_id, version FROM spreads WHERE project_id = ? ORDER BY position",
        (project_id,),
    ).fetchall()


def _spread_photos(conn, spread_id):
    return conn.execute(
        "SELECT photo_id, slot_index, is_dominant FROM spread_photos WHERE spread_id = ? ORDER BY slot_index",
        (spread_id,),
    ).fetchall()


def _job_status(conn, project_id, stage="layout"):
    row = conn.execute(
        "SELECT status FROM processing_jobs WHERE project_id = ? AND stage = ?",
        (project_id, stage),
    ).fetchone()
    return row["status"] if row else None


# ---------------------------------------------------------------------------
# (א) בדיוק 15 כפולות, position 1..15.
# ---------------------------------------------------------------------------
def test_exactly_15_spreads_created_with_minimum_15_photos(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15)

    result = le_mod.generate_layout(project_id, photo_ids, conn)

    assert result.status == "ok", result.message
    assert len(result.spread_ids) == 15

    rows = _spreads(conn, project_id)
    assert len(rows) == 15
    assert [row["position"] for row in rows] == list(range(1, 16))
    assert _job_status(conn, project_id) == "done"


def test_60_photos_distributes_4_per_spread(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 60)

    result = le_mod.generate_layout(project_id, photo_ids, conn)

    assert result.status == "ok"
    rows = _spreads(conn, project_id)
    assert len(rows) == 15
    for row in rows:
        photos = _spread_photos(conn, row["id"])
        assert len(photos) == 4
        assert row["template_id"] == "grid-mixed"


def test_uneven_count_distributes_remainder_to_first_spreads(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    # 17 תמונות / 15 כפולות -> base=1, remainder=2: שתי הכפולות הראשונות
    # מקבלות 2 תמונות, שאר ה-13 מקבלות תמונה אחת.
    photo_ids = _make_selected_photos(conn, project_id, 17)

    result = le_mod.generate_layout(project_id, photo_ids, conn)

    assert result.status == "ok"
    rows = _spreads(conn, project_id)
    sizes = [len(_spread_photos(conn, row["id"])) for row in rows]
    assert sizes == [2, 2] + [1] * 13
    assert sum(sizes) == 17


# ---------------------------------------------------------------------------
# (ב) כל תמונה מופיעה פעם אחת בכל האלבום כברירת מחדל.
# ---------------------------------------------------------------------------
def test_each_photo_appears_exactly_once_by_default(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 30)

    result = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result.status == "ok"

    all_placed = []
    for row in _spreads(conn, project_id):
        for sp in _spread_photos(conn, row["id"]):
            all_placed.append(sp["photo_id"])

    assert sorted(all_placed) == sorted(photo_ids)
    assert len(all_placed) == len(set(all_placed))  # אין כפילויות


# ---------------------------------------------------------------------------
# (ג) תמונה דומיננטית = האיכות הגבוהה ביותר בכפולה.
# ---------------------------------------------------------------------------
def test_dominant_photo_is_highest_quality_in_its_spread(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)

    # 60 תמונות (4 בדיוק לכל כפולה, ראה test_60_photos_distributes_4_per_spread) -
    # כפולה ראשונה = 4 התמונות הראשונות לפי selection_order; אחת מהן באיכות
    # גבוהה בבירור.
    best = _insert_photo(
        conn, project_id, selection_order=1, sharpness_score=999.0, exposure_score=0.9
    )
    others = [
        _insert_photo(conn, project_id, selection_order=i, sharpness_score=1.0, exposure_score=0.1)
        for i in range(2, 5)
    ]
    fillers = _make_selected_photos(conn, project_id, 56, sharpness_score=5.0, exposure_score=0.5)
    for i, pid in enumerate(fillers):
        conn.execute("UPDATE photos SET selection_order = ? WHERE id = ?", (5 + i, pid))
    conn.commit()

    photo_ids = [best] + others + fillers

    result = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result.status == "ok"

    first_spread = _spreads(conn, project_id)[0]
    sp_rows = _spread_photos(conn, first_spread["id"])
    dominant_rows = [r for r in sp_rows if r["is_dominant"]]
    assert len(dominant_rows) == 1
    assert dominant_rows[0]["photo_id"] == best


def test_dominant_tie_break_is_chronologically_first(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)

    # 30 תמונות (2 בדיוק לכל כפולה) - כפולה ראשונה = שתי התמונות הראשונות.
    first = _insert_photo(conn, project_id, selection_order=1, sharpness_score=5.0, exposure_score=0.5)
    second = _insert_photo(conn, project_id, selection_order=2, sharpness_score=5.0, exposure_score=0.5)
    fillers = _make_selected_photos(conn, project_id, 28, sharpness_score=1.0, exposure_score=0.1)
    for i, pid in enumerate(fillers):
        conn.execute("UPDATE photos SET selection_order = ? WHERE id = ?", (3 + i, pid))
    conn.commit()

    photo_ids = [first, second] + fillers

    result = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result.status == "ok"

    first_spread = _spreads(conn, project_id)[0]
    sp_rows = _spread_photos(conn, first_spread["id"])
    dominant_rows = [r for r in sp_rows if r["is_dominant"]]
    assert dominant_rows[0]["photo_id"] == first


# ---------------------------------------------------------------------------
# (ד) בחירת תבנית מגיבה לתערובת כיוונים.
# ---------------------------------------------------------------------------
def test_single_landscape_photo_gets_landscape_template(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15, width=100, height=60)

    result = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result.status == "ok"

    for row in _spreads(conn, project_id):
        assert row["template_id"] == "single-feature-landscape"


def test_single_portrait_photo_gets_portrait_template(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 15, width=60, height=100)

    result = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result.status == "ok"

    for row in _spreads(conn, project_id):
        assert row["template_id"] == "single-feature-portrait"


def test_duo_both_portrait_gets_duo_portrait_template(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 30, width=60, height=100)

    result = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result.status == "ok"
    for row in _spreads(conn, project_id):
        assert row["template_id"] == "duo-portrait"


def test_duo_both_landscape_gets_duo_landscape_template(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 30, width=100, height=60)

    result = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result.status == "ok"
    for row in _spreads(conn, project_id):
        assert row["template_id"] == "duo-landscape"


def test_three_photos_gets_trio_mixed_template(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 45)

    result = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result.status == "ok"
    for row in _spreads(conn, project_id):
        assert row["template_id"] == "trio-mixed"


def test_four_photos_gets_grid_mixed_template(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 60)

    result = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result.status == "ok"
    for row in _spreads(conn, project_id):
        assert row["template_id"] == "grid-mixed"


# ---------------------------------------------------------------------------
# (ה) הרצה חוזרת מחליפה את הכפולות הקודמות.
# ---------------------------------------------------------------------------
def test_rerunning_generate_layout_replaces_previous_spreads(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    first_batch = _make_selected_photos(conn, project_id, 20)

    result1 = le_mod.generate_layout(project_id, first_batch, conn)
    assert result1.status == "ok"
    first_spread_ids = set(result1.spread_ids)

    second_batch = _make_selected_photos(conn, project_id, 25)
    result2 = le_mod.generate_layout(project_id, first_batch + second_batch, conn)
    assert result2.status == "ok"

    rows = _spreads(conn, project_id)
    assert len(rows) == 15  # לא 30 - לא הצטרפו כפולות ישנות לחדשות
    assert set(row["id"] for row in rows).isdisjoint(first_spread_ids)

    all_placed = []
    for row in rows:
        for sp in _spread_photos(conn, row["id"]):
            all_placed.append(sp["photo_id"])
    assert sorted(all_placed) == sorted(first_batch + second_batch)


# ---------------------------------------------------------------------------
# (ו) פחות מ-15 תמונות נבחרות -> status="error", לא נכתב כלום.
# ---------------------------------------------------------------------------
def test_fewer_than_15_selected_photos_returns_error_without_writing(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 10)

    result = le_mod.generate_layout(project_id, photo_ids, conn)

    assert result.status == "error"
    assert result.spread_ids == []
    assert result.message is not None
    assert _spreads(conn, project_id) == []
    assert _job_status(conn, project_id) == "failed"


def test_empty_selection_returns_error(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)

    result = le_mod.generate_layout(project_id, [], conn)

    assert result.status == "error"
    assert result.spread_ids == []


# ---------------------------------------------------------------------------
# (ז) יותר מ-60 תמונות נבחרות / יותר מ-60 must -> status="error".
# ---------------------------------------------------------------------------
def test_more_than_60_selected_photos_returns_error(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 61)

    result = le_mod.generate_layout(project_id, photo_ids, conn)

    assert result.status == "error"
    assert result.spread_ids == []
    assert _spreads(conn, project_id) == []


def test_more_than_60_must_blocks_layout_without_writing(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _make_selected_photos(conn, project_id, 20, flag="must")
    for pid in photo_ids:
        conn.execute("UPDATE photos SET flag = 'must' WHERE id = ?", (pid,))
    conn.commit()
    # מוסיפים עוד 45 must כדי לעבור את 60 בסה"כ (20 + 45 = 65).
    extra_must = _make_selected_photos(conn, project_id, 45, flag="must")
    for pid in extra_must:
        conn.execute("UPDATE photos SET flag = 'must' WHERE id = ?", (pid,))
    conn.commit()

    result = le_mod.generate_layout(project_id, photo_ids + extra_must, conn)

    assert result.status == "error"
    assert result.spread_ids == []
    assert _spreads(conn, project_id) == []
    assert _job_status(conn, project_id) == "failed"


# ---------------------------------------------------------------------------
# (ח) explicit_duplicates - תמונה מופיעה בשתי כפולות שונות רק במפורש.
# ---------------------------------------------------------------------------
def test_explicit_duplicate_photo_can_appear_in_two_spreads(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)

    dup_photo = _insert_photo(conn, project_id, selection_order=1)
    fillers = _make_selected_photos(conn, project_id, 29)
    for i, pid in enumerate(fillers):
        conn.execute("UPDATE photos SET selection_order = ? WHERE id = ?", (2 + i, pid))
    conn.commit()

    # רשימת קלט עם dup_photo פעמיים - אחת בתחילת האלבום, אחת בסופו - כדי
    # שהן ייפלו לשתי כפולות *שונות* אחרי החלוקה.
    photo_ids = [dup_photo] + fillers + [dup_photo]

    result = le_mod.generate_layout(
        project_id, photo_ids, conn, explicit_duplicates={dup_photo}
    )

    assert result.status == "ok"

    appearances = 0
    spreads_with_dup = set()
    for row in _spreads(conn, project_id):
        for sp in _spread_photos(conn, row["id"]):
            if sp["photo_id"] == dup_photo:
                appearances += 1
                spreads_with_dup.add(row["id"])

    assert appearances == 2
    assert len(spreads_with_dup) == 2


def test_duplicate_photo_without_explicit_flag_is_deduplicated(engine):
    le_mod, conn = engine
    project_id = _make_project(conn)

    dup_photo = _insert_photo(conn, project_id, selection_order=1)
    fillers = _make_selected_photos(conn, project_id, 29)
    for i, pid in enumerate(fillers):
        conn.execute("UPDATE photos SET selection_order = ? WHERE id = ?", (2 + i, pid))
    conn.commit()

    photo_ids = [dup_photo] + fillers + [dup_photo]  # בלי explicit_duplicates

    result = le_mod.generate_layout(project_id, photo_ids, conn)

    assert result.status == "ok"

    appearances = 0
    for row in _spreads(conn, project_id):
        for sp in _spread_photos(conn, row["id"]):
            if sp["photo_id"] == dup_photo:
                appearances += 1

    assert appearances == 1
