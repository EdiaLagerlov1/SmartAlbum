"""
בדיקות יחידה ל-selection_engine F05 (issue #6): תיוג שלבי אירוע וקיבוץ על
בסיס זמן (`tag_event_stages`).

משתמש ב-DB זמני (SMARTALBUM_STORAGE_ROOT/SMARTALBUM_DB_PATH, כמו שאר
הטסטים בריפו) ומכניס שורות `photos` ישירות ל-DB (בלי לעבור את
image_pipeline) כדי לקבוע `taken_at` במדויק - ראה גם `tests/test_api_flags.py`
(`_insert_raw_photo`) לתבנית דומה.

מכסה (לפי בקשת ה-issue):
  (א) תמונות עם taken_at הפרוש על פני כמה שעות, עם פערי זמן גדולים בין
      קבוצות - מתקבצות לשלבים (event_stage) נפרדים.
  (ב) תמונות קרובות בזמן (דקות בודדות) - מתקבצות לאותו שלב.
  (ג) taken_at=NULL (לחלק מהתמונות, וגם לכולן) - גורם ל-needs_stage_review=1
      ול-event_stage=NULL, ולא לקריסה (לא exception).
  (ד) ביטחון נמוך (קבוצת-זמן בודדת בלי אף פער גדול, או קבוצה "עודפת" בלי
      שם פנוי) - גורם ל-needs_stage_review=1 גם כש-taken_at תקין.
  (ה) סדר כרונולוגי של השלבים (getting_ready לפני ceremony לפני party...).
  (ו) לא קורס ולא "ממציא" שלב כשיש יותר קבוצות-זמן משמות ידועים.
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
    """DB זמני + מודולים נטענים מחדש מולו (בדיוק כמו ב-test_api_flags.py),
    אבל בלי Flask - הטסטים האלה הם unit-level ישירות על selection_engine.api."""
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


def _insert_photo(conn, project_id: str, taken_at: str | None) -> str:
    photo_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO photos (id, project_id, original_filename, original_path, uploaded_at, taken_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            photo_id,
            project_id,
            f"{photo_id}.jpg",
            f"/tmp/{photo_id}.jpg",
            datetime.now(timezone.utc).isoformat(),
            taken_at,
        ),
    )
    conn.commit()
    return photo_id


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _get_photo(conn, photo_id: str):
    return conn.execute(
        "SELECT event_stage, event_stage_confidence, needs_stage_review FROM photos WHERE id = ?",
        (photo_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# (א) + (ה): פערי זמן גדולים (שעות) -> קבוצות/שלבים נפרדים, בסדר כרונולוגי.
# ---------------------------------------------------------------------------
def test_large_time_gaps_split_into_separate_stages_in_order(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    base = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)

    # קבוצה 0: 09:00-09:10 (הכנות) - 3 תמונות, פערים של דקות.
    group0 = [
        _insert_photo(conn, project_id, _iso(base + timedelta(minutes=0))),
        _insert_photo(conn, project_id, _iso(base + timedelta(minutes=5))),
        _insert_photo(conn, project_id, _iso(base + timedelta(minutes=10))),
    ]
    # קבוצה 1: 13:00-13:10 (פער של 4 שעות מהקבוצה הקודמת - גבול שלב, טקס).
    base1 = base + timedelta(hours=4)
    group1 = [
        _insert_photo(conn, project_id, _iso(base1 + timedelta(minutes=0))),
        _insert_photo(conn, project_id, _iso(base1 + timedelta(minutes=5))),
    ]
    # קבוצה 2: 20:00-20:10 (עוד פער גדול - ריקודים).
    base2 = base1 + timedelta(hours=7)
    group2 = [
        _insert_photo(conn, project_id, _iso(base2 + timedelta(minutes=0))),
        _insert_photo(conn, project_id, _iso(base2 + timedelta(minutes=5))),
    ]

    se_mod.tag_event_stages(project_id, conn)

    stages0 = {se_mod.EVENT_STAGE_SEQUENCE[0]}
    stages1 = {se_mod.EVENT_STAGE_SEQUENCE[1]}
    stages2 = {se_mod.EVENT_STAGE_SEQUENCE[2]}

    for pid in group0:
        row = _get_photo(conn, pid)
        assert row["event_stage"] in stages0
        assert row["needs_stage_review"] == 0

    for pid in group1:
        row = _get_photo(conn, pid)
        assert row["event_stage"] in stages1
        assert row["needs_stage_review"] == 0

    for pid in group2:
        row = _get_photo(conn, pid)
        assert row["event_stage"] in stages2
        assert row["needs_stage_review"] == 0

    # סדר כרונולוגי אמיתי - getting_ready לפני ceremony לפני party.
    assert se_mod.EVENT_STAGE_SEQUENCE[0] == "getting_ready"
    assert se_mod.EVENT_STAGE_SEQUENCE[1] == "ceremony"
    assert se_mod.EVENT_STAGE_SEQUENCE[2] == "reception"


# ---------------------------------------------------------------------------
# (ב): תמונות קרובות בזמן (דקות) מתקבצות לאותו שלב (לא מפוצלות).
# ---------------------------------------------------------------------------
def test_close_in_time_photos_grouped_into_same_stage(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    base = datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc)
    photo_ids = [
        _insert_photo(conn, project_id, _iso(base + timedelta(minutes=offset)))
        for offset in (0, 2, 4, 6, 8)
    ]
    # עוגן שני, כדי שתהיה יותר מקבוצת-זמן אחת (כדי שלא ניפול ל-"single cluster"
    # low-confidence) - לא בודק אותו, רק נותן בסיס-השוואה לקבוצה הראשונה.
    _insert_photo(conn, project_id, _iso(base + timedelta(hours=5)))

    se_mod.tag_event_stages(project_id, conn)

    stages = {_get_photo(conn, pid)["event_stage"] for pid in photo_ids}
    assert len(stages) == 1  # כולן באותו שלב
    assert None not in stages
    for pid in photo_ids:
        assert _get_photo(conn, pid)["needs_stage_review"] == 0


# ---------------------------------------------------------------------------
# (ג): taken_at=NULL (חלקי וגם גורף) -> needs_stage_review=1, בלי קריסה.
# ---------------------------------------------------------------------------
def test_missing_taken_at_marks_needs_review_without_crashing(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    with_time = _insert_photo(conn, project_id, "2026-06-01T09:00:00+00:00")
    without_time_a = _insert_photo(conn, project_id, None)
    without_time_b = _insert_photo(conn, project_id, "not-a-valid-timestamp")

    # לא קורס (לא מעלה exception):
    se_mod.tag_event_stages(project_id, conn)

    for pid in (without_time_a, without_time_b):
        row = _get_photo(conn, pid)
        assert row["event_stage"] is None
        assert row["needs_stage_review"] == 1
        assert row["event_stage_confidence"] == 0.0


def test_all_photos_missing_taken_at_does_not_crash(engine):
    """כל התמונות בפרויקט בלי taken_at (המצב הנוכחי בפועל - image-pipeline
    לא ממלא את השדה, ראה TODO ב-INTERFACES.md) - הפונקציה חייבת להתמודד
    עם זה בחוכמה (fallback, לא ניחוש), לא לקרוס."""
    se_mod, conn = engine
    project_id = _make_project(conn)

    photo_ids = [_insert_photo(conn, project_id, None) for _ in range(5)]

    se_mod.tag_event_stages(project_id, conn)  # לא קורס

    for pid in photo_ids:
        row = _get_photo(conn, pid)
        assert row["event_stage"] is None
        assert row["needs_stage_review"] == 1

    job = conn.execute(
        "SELECT status FROM processing_jobs WHERE project_id = ? AND stage = 'tagging'",
        (project_id,),
    ).fetchone()
    assert job["status"] == "done"


def test_empty_project_does_not_crash(engine):
    """פרויקט בלי תמונות כלל - קצה-גבול שלא קשור ל-taken_at, אבל חייב
    להתמודד בלי exception."""
    se_mod, conn = engine
    project_id = _make_project(conn)

    se_mod.tag_event_stages(project_id, conn)

    job = conn.execute(
        "SELECT status FROM processing_jobs WHERE project_id = ? AND stage = 'tagging'",
        (project_id,),
    ).fetchone()
    assert job["status"] == "done"


# ---------------------------------------------------------------------------
# (ד): ביטחון נמוך -> needs_stage_review=1, גם עם taken_at תקין.
# ---------------------------------------------------------------------------
def test_single_time_cluster_has_low_confidence_and_needs_review(engine):
    """כל התמונות (עם timestamp תקין) קרובות זו לזו - אין אף פער גדול,
    אז אין בסיס לחלק לשלבים: event_stage=NULL, ביטחון מתחת לסף, לבדיקה."""
    se_mod, conn = engine
    project_id = _make_project(conn)

    base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    photo_ids = [
        _insert_photo(conn, project_id, _iso(base + timedelta(minutes=offset)))
        for offset in (0, 3, 6, 9)
    ]

    se_mod.tag_event_stages(project_id, conn)

    for pid in photo_ids:
        row = _get_photo(conn, pid)
        assert row["event_stage"] is None
        assert row["event_stage_confidence"] < se_mod.EVENT_STAGE_CONFIDENCE_THRESHOLD
        assert row["needs_stage_review"] == 1


def test_overflow_clusters_beyond_known_stage_names_need_review(engine):
    """(ו): יותר קבוצות-זמן (פערים גדולים) מאשר שמות ב-EVENT_STAGE_SEQUENCE -
    הקבוצות העודפות לא מקבלות שם מנוחש, ומסומנות לבדיקה."""
    se_mod, conn = engine
    project_id = _make_project(conn)

    base = datetime(2026, 6, 1, 6, 0, 0, tzinfo=timezone.utc)
    num_clusters = len(se_mod.EVENT_STAGE_SEQUENCE) + 2
    overflow_photo_ids = []
    for i in range(num_clusters):
        cluster_base = base + timedelta(hours=i * 4)
        pid_a = _insert_photo(conn, project_id, _iso(cluster_base))
        pid_b = _insert_photo(conn, project_id, _iso(cluster_base + timedelta(minutes=5)))
        if i >= len(se_mod.EVENT_STAGE_SEQUENCE):
            overflow_photo_ids.extend([pid_a, pid_b])

    se_mod.tag_event_stages(project_id, conn)

    assert overflow_photo_ids  # sanity - יש בכלל קבוצות עודפות בתרחיש הזה
    for pid in overflow_photo_ids:
        row = _get_photo(conn, pid)
        assert row["event_stage"] is None
        assert row["needs_stage_review"] == 1


def test_running_twice_overwrites_previous_tagging(engine):
    se_mod, conn = engine
    project_id = _make_project(conn)

    base = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    photo_id = _insert_photo(conn, project_id, _iso(base))
    _insert_photo(conn, project_id, _iso(base + timedelta(hours=5)))

    se_mod.tag_event_stages(project_id, conn)
    first = _get_photo(conn, photo_id)["event_stage"]

    se_mod.tag_event_stages(project_id, conn)
    second = _get_photo(conn, photo_id)["event_stage"]

    assert first == second == se_mod.EVENT_STAGE_SEQUENCE[0]
