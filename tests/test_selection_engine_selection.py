"""
בדיקות יחידה ל-selection_engine F06 (issue #7): אלגוריתם בחירה - עד 60
תמונות לאלבום (`run_selection`).

משתמש ב-DB זמני (SMARTALBUM_STORAGE_ROOT/SMARTALBUM_DB_PATH) ומכניס שורות
`photos` ישירות ל-DB (בלי לעבור את image_pipeline), בדיוק כמו
`tests/test_selection_engine_tagging.py` ו-`tests/test_api_flags.py`
(`_insert_raw_photo`) - כך שאפשר לקבוע flag/similarity_group_id/
sharpness_score/exposure_score/taken_at במדויק, ולבדוק מהר מאוד תרחישים
עם עשרות תמונות (60/61+) בלי להעלות קבצי JPEG אמיתיים אחד-אחד.

מכסה (לפי בקשת ה-issue):
  (א) בחירה רגילה מתחת ל-60 - כל התמונות התקינות נבחרות.
  (ב) must מכליל תמיד, גם תמונת must באיכות נמוכה, גם כש-זה "דוחק" מועמדת
      אחרת (none) באיכות גבוהה יותר מחוץ למכסה.
  (ג) exclude לעולם לא נבחרת (ולא נספרת כ"תקינה" לצורך כלל ה-15).
  (ד) צמצום כפילויות: מתוך קבוצת similarity_group_id, רק הציון הגבוה
      ביותר נבחר אוטומטית; קבוצה שכבר מיוצגת ע"י must - מדולגת כליל
      (לא מתווספת עוד "כפילות" אוטומטית); שתי תמונות must מאותה קבוצה -
      שתיהן נכללות (המשתמשת "הוסיפה במפורש").
  (ה) פחות מ-15 תמונות תקינות -> status="too_few_photos", לא נכתב כלום.
  (ו) יותר מ-60 must -> status="too_many_must" (נאכף גם ב-run_selection
      עצמה, לא רק ב-check_must_limit), לא נכתב כלום.
  (ז) יותר מ-60 מועמדות כוללות (none, בלי must) -> המכסה (60) נבחרת לפי
      דירוג איכות (sharpness/exposure).
  (ח) הרצה חוזרת מחליפה את הבחירה הקודמת (לא מצרפת).
  (ט) processing_jobs.stage='selection' מתעדכן בהתאם (running/done/failed).
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """DB זמני + מודולים נטענים מחדש מולו - ראה test_selection_engine_tagging.py."""
    storage_root = tmp_path / "storage"
    db_path = storage_root / "smartalbum.db"
    monkeypatch.setenv("SMARTALBUM_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("SMARTALBUM_DB_PATH", str(db_path))

    for mod_name in ("storage", "db", "selection_engine", "selection_engine.api"):
        sys.modules.pop(mod_name, None)

    import db as db_mod

    db_mod.init_db()

    import selection_engine.api as se_mod

    conn = db_mod.get_connection()
    yield se_mod, conn
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
    similarity_group_id: str | None = None,
    sharpness_score: float | None = None,
    exposure_score: float | None = None,
    taken_at: str | None = None,
) -> str:
    photo_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO photos (id, project_id, original_filename, original_path, uploaded_at,
                             flag, similarity_group_id, sharpness_score, exposure_score, taken_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            photo_id,
            project_id,
            f"{photo_id}.jpg",
            f"/tmp/{photo_id}.jpg",
            datetime.now(timezone.utc).isoformat(),
            flag,
            similarity_group_id,
            sharpness_score,
            exposure_score,
            taken_at,
        ),
    )
    conn.commit()
    return photo_id


def _get_photo(conn, photo_id: str):
    return conn.execute(
        "SELECT flag, selected_for_album, selection_order FROM photos WHERE id = ?",
        (photo_id,),
    ).fetchone()


def _job_status(conn, project_id: str, stage: str):
    row = conn.execute(
        "SELECT status FROM processing_jobs WHERE project_id = ? AND stage = ?",
        (project_id, stage),
    ).fetchone()
    return row["status"] if row else None


# ---------------------------------------------------------------------------
# (א) בחירה רגילה מתחת ל-60 - כל התקינות נבחרות.
# ---------------------------------------------------------------------------
def test_normal_selection_under_60_selects_all_valid_photos(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    photo_ids = [
        _insert_photo(conn, project_id, sharpness_score=float(i), exposure_score=0.5)
        for i in range(20)
    ]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "ok"
    assert set(result.selected_photo_ids) == set(photo_ids)
    assert len(result.selected_photo_ids) == 20

    for pid in photo_ids:
        row = _get_photo(conn, pid)
        assert row["selected_for_album"] == 1
        assert row["selection_order"] is not None

    assert _job_status(conn, project_id, "selection") == "done"


# ---------------------------------------------------------------------------
# (ב) must מכליל תמיד - גם איכות נמוכה, גם כשדוחק מועמדת אחרת מחוץ למכסה.
# ---------------------------------------------------------------------------
def test_must_always_included_even_with_low_quality_and_pushes_out_lower_ranked_candidates(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    # 14 תמונות "מלאות" סתם כדי לעבור את סף ה-15 תקינות, בלי שום קשר לבדיקה.
    filler_ids = [
        _insert_photo(conn, project_id, sharpness_score=10.0, exposure_score=0.5)
        for _ in range(14)
    ]

    must_low_quality = _insert_photo(
        conn, project_id, flag="must", sharpness_score=0.1, exposure_score=0.1
    )

    # ממלאים את שאר המכסה (60) עם מועמדות none באיכות גבוהה בהרבה, פרט
    # לאחת (the_pushed_out) שתהיה בדיוק המועמדת ה-61 מבחינת דירוג ואיכות -
    # כדי לוודא שה-must (איכות נמוכה) נכנס במקום *משהו* שהיה נבחר בלעדיו.
    high_quality_ids = []
    for i in range(45):
        high_quality_ids.append(
            _insert_photo(conn, project_id, sharpness_score=100.0 + i, exposure_score=0.9)
        )

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "ok"
    assert must_low_quality in result.selected_photo_ids
    # סה"כ מועמדות תקינות = 14 + 1 (must) + 45 = 60, בדיוק המכסה - כולן נכנסות.
    assert len(result.selected_photo_ids) == 60
    assert set(result.selected_photo_ids) == set(filler_ids) | {must_low_quality} | set(high_quality_ids)


def test_must_included_even_when_valid_pool_exceeds_60_and_crowds_out_best_none_candidate(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    must_low_quality = _insert_photo(
        conn, project_id, flag="must", sharpness_score=-5.0, exposure_score=0.0
    )
    # 65 מועמדות none באיכות גבוהה (יותר מהמכסה הנותרת, 59) - רק 59 הטובות
    # ביותר מהן תיבחרנה; ה-must תמיד נכנסת למרות הציון הנמוך ביותר מכולן.
    none_ids_by_quality_desc = [
        _insert_photo(conn, project_id, sharpness_score=float(1000 - i), exposure_score=0.8)
        for i in range(65)
    ]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "ok"
    assert must_low_quality in result.selected_photo_ids
    assert len(result.selected_photo_ids) == 60

    selected_none = set(result.selected_photo_ids) - {must_low_quality}
    # 59 הטובות ביותר (הראשונות ברשימה, שנוצרו עם הציון הגבוה ביותר) נבחרו.
    expected_best_59 = set(none_ids_by_quality_desc[:59])
    assert selected_none == expected_best_59
    # המועמדת הגרועה ביותר (האחרונה שנוצרה, ציון הכי נמוך מבין ה-none) "נדחקה" חוצה.
    assert none_ids_by_quality_desc[-1] not in result.selected_photo_ids


# ---------------------------------------------------------------------------
# (ג) exclude לעולם לא נבחרת, ולא נספרת לכלל ה-15 "תקינות".
# ---------------------------------------------------------------------------
def test_excluded_photos_never_selected(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    valid_ids = [_insert_photo(conn, project_id) for _ in range(16)]
    excluded_ids = [_insert_photo(conn, project_id, flag="exclude") for _ in range(5)]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "ok"
    assert set(result.selected_photo_ids) == set(valid_ids)
    for pid in excluded_ids:
        assert pid not in result.selected_photo_ids
        row = _get_photo(conn, pid)
        assert row["selected_for_album"] == 0
        assert row["selection_order"] is None


def test_excluded_photos_do_not_count_toward_minimum_valid(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    # 10 תקינות (מתחת ל-15) + 10 exclude - ה-exclude לא "מצילות" את הספירה.
    for _ in range(10):
        _insert_photo(conn, project_id)
    for _ in range(10):
        _insert_photo(conn, project_id, flag="exclude")

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "too_few_photos"
    assert result.selected_photo_ids == []


# ---------------------------------------------------------------------------
# (ד) צמצום כפילויות לפי similarity_group_id.
# ---------------------------------------------------------------------------
def test_similarity_group_keeps_only_highest_scoring_member(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    group_id = "group-1"
    best = _insert_photo(
        conn, project_id, similarity_group_id=group_id, sharpness_score=50.0, exposure_score=0.9
    )
    worse_a = _insert_photo(
        conn, project_id, similarity_group_id=group_id, sharpness_score=10.0, exposure_score=0.5
    )
    worse_b = _insert_photo(
        conn, project_id, similarity_group_id=group_id, sharpness_score=5.0, exposure_score=0.5
    )
    # עוד 14 תמונות "מלאות" כדי לעבור את סף 15 התקינות.
    fillers = [_insert_photo(conn, project_id) for _ in range(14)]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "ok"
    assert best in result.selected_photo_ids
    assert worse_a not in result.selected_photo_ids
    assert worse_b not in result.selected_photo_ids
    assert set(result.selected_photo_ids) == {best} | set(fillers)


def test_similarity_group_already_covered_by_must_is_skipped_entirely(engine):
    """קבוצה שיש לה כבר נציג must - אף תמונה none אחרת מאותה קבוצה לא
    נבחרת אוטומטית, גם אם הציון שלה גבוה."""
    se_mod, conn = engine
    project_id = _make_project(conn)

    group_id = "group-covered-by-must"
    must_photo = _insert_photo(
        conn, project_id, flag="must", similarity_group_id=group_id,
        sharpness_score=1.0, exposure_score=0.1,
    )
    high_quality_sibling = _insert_photo(
        conn, project_id, similarity_group_id=group_id, sharpness_score=999.0, exposure_score=0.9
    )
    fillers = [_insert_photo(conn, project_id) for _ in range(14)]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "ok"
    assert must_photo in result.selected_photo_ids
    assert high_quality_sibling not in result.selected_photo_ids
    assert set(result.selected_photo_ids) == {must_photo} | set(fillers)


def test_two_must_photos_from_same_similarity_group_both_included(engine):
    """המשתמשת סימנה must לשתי תמונות מאותה קבוצת-דמיון - "הוספה מפורשת",
    שתיהן נכללות (הדגל היחיד שמייצג "הוספה מפורשת" בחוזה הקיים)."""
    se_mod, conn = engine
    project_id = _make_project(conn)

    group_id = "group-double-must"
    must_a = _insert_photo(conn, project_id, flag="must", similarity_group_id=group_id)
    must_b = _insert_photo(conn, project_id, flag="must", similarity_group_id=group_id)
    fillers = [_insert_photo(conn, project_id) for _ in range(14)]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "ok"
    assert must_a in result.selected_photo_ids
    assert must_b in result.selected_photo_ids
    assert set(result.selected_photo_ids) == {must_a, must_b} | set(fillers)


def test_photos_without_similarity_group_are_never_deduplicated_against_each_other(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    standalone_ids = [_insert_photo(conn, project_id, similarity_group_id=None) for _ in range(16)]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "ok"
    assert set(result.selected_photo_ids) == set(standalone_ids)


# ---------------------------------------------------------------------------
# (ה) פחות מ-15 תמונות תקינות -> too_few_photos, לא נכתב כלום.
# ---------------------------------------------------------------------------
def test_fewer_than_15_valid_photos_returns_too_few_photos_without_writing(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    photo_ids = [_insert_photo(conn, project_id) for _ in range(14)]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "too_few_photos"
    assert result.selected_photo_ids == []
    assert result.message is not None

    for pid in photo_ids:
        row = _get_photo(conn, pid)
        assert row["selected_for_album"] == 0
        assert row["selection_order"] is None

    assert _job_status(conn, project_id, "selection") == "failed"


def test_exactly_15_valid_photos_is_sufficient(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    photo_ids = [_insert_photo(conn, project_id) for _ in range(15)]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "ok"
    assert set(result.selected_photo_ids) == set(photo_ids)


# ---------------------------------------------------------------------------
# (ו) יותר מ-60 must -> too_many_must, לא נכתב כלום (נאכף גם כאן, לא רק
# ב-check_must_limit).
# ---------------------------------------------------------------------------
def test_more_than_60_must_blocks_selection_without_writing(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    must_ids = [_insert_photo(conn, project_id, flag="must") for _ in range(61)]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "too_many_must"
    assert result.selected_photo_ids == []
    assert result.message is not None

    for pid in must_ids:
        row = _get_photo(conn, pid)
        assert row["selected_for_album"] == 0
        assert row["selection_order"] is None

    assert _job_status(conn, project_id, "selection") == "failed"


def test_exactly_60_must_is_within_limit_and_all_included(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    must_ids = [_insert_photo(conn, project_id, flag="must") for _ in range(60)]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "ok"
    assert set(result.selected_photo_ids) == set(must_ids)
    assert len(result.selected_photo_ids) == 60


# ---------------------------------------------------------------------------
# (ז) יותר מ-60 מועמדות none (בלי must) -> המכסה נבחרת לפי דירוג איכות.
# ---------------------------------------------------------------------------
def test_more_than_60_none_candidates_selects_top_60_by_quality(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    all_ids = [
        _insert_photo(conn, project_id, sharpness_score=float(i), exposure_score=0.5)
        for i in range(70)
    ]

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "ok"
    assert len(result.selected_photo_ids) == 60
    # ה-60 עם ה-sharpness הגבוה ביותר (אינדקסים 10..69, שנוצרו עם ציון i).
    expected = set(all_ids[10:])
    assert set(result.selected_photo_ids) == expected


# ---------------------------------------------------------------------------
# (ח) הרצה חוזרת מחליפה את הבחירה הקודמת, לא מצרפת.
# ---------------------------------------------------------------------------
def test_rerunning_selection_replaces_previous_selection(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    first_batch = [_insert_photo(conn, project_id) for _ in range(16)]
    result1 = se_mod.run_selection(project_id, conn)
    assert result1.status == "ok"
    assert set(result1.selected_photo_ids) == set(first_batch)

    # מחריגים חלק מהקבוצה הראשונה ומוסיפים תמונות חדשות - הרצה שנייה.
    conn.execute("UPDATE photos SET flag = 'exclude' WHERE id = ?", (first_batch[0],))
    conn.commit()
    second_batch = [_insert_photo(conn, project_id) for _ in range(5)]

    result2 = se_mod.run_selection(project_id, conn)
    assert result2.status == "ok"
    expected_second = set(first_batch[1:]) | set(second_batch)
    assert set(result2.selected_photo_ids) == expected_second

    # התמונה שהוחרגה - לא נבחרת יותר, ולא "נשארת" מהבחירה הקודמת.
    row = _get_photo(conn, first_batch[0])
    assert row["selected_for_album"] == 0
    assert row["selection_order"] is None


# ---------------------------------------------------------------------------
# (ט) processing_jobs
# ---------------------------------------------------------------------------
def test_empty_project_is_too_few_photos_and_marks_job_failed(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    result = se_mod.run_selection(project_id, conn)

    assert result.status == "too_few_photos"
    assert _job_status(conn, project_id, "selection") == "failed"
