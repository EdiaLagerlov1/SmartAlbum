"""
בדיקות יחידה ל-layout_engine F08 (issue #9): החלפת תמונה בכפולה
(`replace_photo_in_slot`), שינוי תבנית (`change_template`) וקריאת מצב
(`get_spreads`) - חלק ה-backend של [F08] החלפת תמונות ושינוי תבנית.

משתמש ב-DB זמני, בדיוק כמו tests/test_layout_engine.py - מכניס שורות
`photos`/`spreads`/`spread_photos` ישירות דרך `generate_layout` (כדי לקבל
מצב התחלתי תקין) ואז קורא ל-`replace_photo_in_slot`/`change_template`.

מכסה (לפי בקשת ה-issue):
  (א) swap מוצלח - מחליף את התמונה בסלוט, משאיר slot_index/is_dominant,
      מקדם version.
  (ב) swap נדחה - תמונה חדשה מסומנת exclude.
  (ג) swap נדחה - תמונה חדשה לא שייכת לפרויקט.
  (ד) swap נדחה כברירת מחדל - תמונה חדשה כבר באלבום (כפולה אחרת); מצליח
      עם allow_duplicate=True.
  (ה) swap נדחה - spread_id/old_photo_id לא תואמים.
  (ו) שינוי תבנית מוצלח - משנה template_id, משבץ מחדש סלוטים, משאיר דומיננטי.
  (ז) שינוי תבנית נדחה - מספר סלוטים לא תואם למספר התמונות.
  (ח) שינוי תבנית נדחה - template_id לא קיים בקטלוג.
  (ט) "שחזור" - get_spreads אחרי swap/שינוי תבנית, ואחרי "פתיחה מחדש"
      (חיבור DB חדש) משקף את המצב השמור.
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
    yield le_mod, conn, db_mod
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
        "SELECT id, position, template_id, version FROM spreads WHERE project_id = ? ORDER BY position",
        (project_id,),
    ).fetchall()


def _spread_photos(conn, spread_id):
    return conn.execute(
        "SELECT photo_id, slot_index, is_dominant FROM spread_photos WHERE spread_id = ? ORDER BY slot_index",
        (spread_id,),
    ).fetchall()


def _setup_layout(le_mod, conn, project_id, count=60, **kwargs):
    """מריץ generate_layout על `count` תמונות (ברירת מחדל 60 - 4 תמונות
    בדיוק לכל כפולה, grid-mixed - נוח לבדוק swap/change-template)."""
    photo_ids = _make_selected_photos(conn, project_id, count, **kwargs)
    result = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result.status == "ok", result.message
    return photo_ids, _spreads(conn, project_id)


# ---------------------------------------------------------------------------
# (א) swap מוצלח.
# ---------------------------------------------------------------------------
def test_replace_photo_in_slot_success_preserves_slot_and_bumps_version(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    photo_ids, spreads = _setup_layout(le_mod, conn, project_id)

    first_spread = spreads[0]
    sp_rows = _spread_photos(conn, first_spread["id"])
    old_photo = sp_rows[0]["photo_id"]
    old_slot_index = sp_rows[0]["slot_index"]
    old_is_dominant = sp_rows[0]["is_dominant"]

    new_photo = _insert_photo(conn, project_id, selected_for_album=0, selection_order=None)

    result = le_mod.replace_photo_in_slot(project_id, first_spread["id"], old_photo, new_photo, conn)

    assert result.status == "ok", result.message
    assert result.error_reason is None

    updated_rows = _spread_photos(conn, first_spread["id"])
    by_slot = {r["slot_index"]: r for r in updated_rows}
    assert by_slot[old_slot_index]["photo_id"] == new_photo
    assert by_slot[old_slot_index]["is_dominant"] == old_is_dominant
    assert all(r["photo_id"] != old_photo for r in updated_rows)

    new_version = conn.execute(
        "SELECT version FROM spreads WHERE id = ?", (first_spread["id"],)
    ).fetchone()["version"]
    assert new_version == first_spread["version"] + 1


def test_replace_photo_in_slot_noop_when_same_photo(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id)

    first_spread = spreads[0]
    sp_rows = _spread_photos(conn, first_spread["id"])
    old_photo = sp_rows[0]["photo_id"]

    result = le_mod.replace_photo_in_slot(project_id, first_spread["id"], old_photo, old_photo, conn)

    assert result.status == "ok"
    version_after = conn.execute(
        "SELECT version FROM spreads WHERE id = ?", (first_spread["id"],)
    ).fetchone()["version"]
    assert version_after == first_spread["version"]  # לא קודם - no-op אמיתי


# ---------------------------------------------------------------------------
# (ב) swap נדחה - תמונה חדשה מסומנת exclude.
# ---------------------------------------------------------------------------
def test_replace_photo_in_slot_rejected_for_excluded_photo(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id)

    first_spread = spreads[0]
    old_photo = _spread_photos(conn, first_spread["id"])[0]["photo_id"]
    excluded_photo = _insert_photo(
        conn, project_id, flag="exclude", selected_for_album=0, selection_order=None
    )

    result = le_mod.replace_photo_in_slot(
        project_id, first_spread["id"], old_photo, excluded_photo, conn
    )

    assert result.status == "error"
    assert result.error_reason == "new_photo_excluded"
    # לא נכתב - התמונה הישנה נשארת בסלוט.
    updated_rows = _spread_photos(conn, first_spread["id"])
    assert old_photo in [r["photo_id"] for r in updated_rows]


# ---------------------------------------------------------------------------
# (ג) swap נדחה - תמונה חדשה לא שייכת לפרויקט (לא קיימת כלל / פרויקט אחר).
# ---------------------------------------------------------------------------
def test_replace_photo_in_slot_rejected_for_photo_not_in_project(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id)

    other_project_id = _make_project(conn, name="פרויקט אחר")
    foreign_photo = _insert_photo(
        conn, other_project_id, selected_for_album=0, selection_order=None
    )

    first_spread = spreads[0]
    old_photo = _spread_photos(conn, first_spread["id"])[0]["photo_id"]

    result = le_mod.replace_photo_in_slot(
        project_id, first_spread["id"], old_photo, foreign_photo, conn
    )

    assert result.status == "error"
    assert result.error_reason == "new_photo_not_found"

    result2 = le_mod.replace_photo_in_slot(
        project_id, first_spread["id"], old_photo, "does-not-exist", conn
    )
    assert result2.status == "error"
    assert result2.error_reason == "new_photo_not_found"


# ---------------------------------------------------------------------------
# (ד) דה-דופ: swap נדחה כברירת מחדל אם התמונה כבר באלבום; מצליח עם
# allow_duplicate=True.
# ---------------------------------------------------------------------------
def test_replace_photo_in_slot_rejected_when_new_photo_already_in_album(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id)

    first_spread, second_spread = spreads[0], spreads[1]
    old_photo = _spread_photos(conn, first_spread["id"])[0]["photo_id"]
    # תמונה שמשובצת כבר בכפולה השנייה.
    photo_in_second_spread = _spread_photos(conn, second_spread["id"])[0]["photo_id"]

    result = le_mod.replace_photo_in_slot(
        project_id, first_spread["id"], old_photo, photo_in_second_spread, conn
    )
    assert result.status == "error"
    assert result.error_reason == "new_photo_already_in_album"

    result_allowed = le_mod.replace_photo_in_slot(
        project_id,
        first_spread["id"],
        old_photo,
        photo_in_second_spread,
        conn,
        allow_duplicate=True,
    )
    assert result_allowed.status == "ok", result_allowed.message

    appearances = sum(
        1
        for row in _spreads(conn, project_id)
        for sp in _spread_photos(conn, row["id"])
        if sp["photo_id"] == photo_in_second_spread
    )
    assert appearances == 2


def test_replace_photo_in_slot_rejected_when_new_photo_already_in_same_spread(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id)

    first_spread = spreads[0]
    sp_rows = _spread_photos(conn, first_spread["id"])
    old_photo = sp_rows[0]["photo_id"]
    other_photo_in_same_spread = sp_rows[1]["photo_id"]

    result = le_mod.replace_photo_in_slot(
        project_id, first_spread["id"], old_photo, other_photo_in_same_spread, conn
    )
    assert result.status == "error"
    assert result.error_reason == "new_photo_already_in_spread"


# ---------------------------------------------------------------------------
# (ה) swap נדחה - spread/old_photo לא תואמים.
# ---------------------------------------------------------------------------
def test_replace_photo_in_slot_rejected_for_unknown_spread(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id)
    old_photo = _spread_photos(conn, spreads[0]["id"])[0]["photo_id"]
    new_photo = _insert_photo(conn, project_id, selected_for_album=0, selection_order=None)

    result = le_mod.replace_photo_in_slot(project_id, "does-not-exist", old_photo, new_photo, conn)
    assert result.status == "error"
    assert result.error_reason == "spread_not_found"


def test_replace_photo_in_slot_rejected_when_old_photo_not_in_spread(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id)
    first_spread, second_spread = spreads[0], spreads[1]
    photo_in_second_spread = _spread_photos(conn, second_spread["id"])[0]["photo_id"]
    new_photo = _insert_photo(conn, project_id, selected_for_album=0, selection_order=None)

    # photo_in_second_spread אינה בכלל בכפולה הראשונה.
    result = le_mod.replace_photo_in_slot(
        project_id, first_spread["id"], photo_in_second_spread, new_photo, conn
    )
    assert result.status == "error"
    assert result.error_reason == "old_photo_not_in_spread"


# ---------------------------------------------------------------------------
# (ו) שינוי תבנית מוצלח.
# ---------------------------------------------------------------------------
def test_change_template_success_reassigns_slots_and_keeps_dominant(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    # 60 תמונות -> 4 בדיוק לכל כפולה, grid-mixed (4 סלוטים).
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id, count=60)

    first_spread = spreads[0]
    assert first_spread["template_id"] == "grid-mixed"
    before_rows = _spread_photos(conn, first_spread["id"])
    dominant_before = [r["photo_id"] for r in before_rows if r["is_dominant"]][0]

    # trio-mixed דורש 3 סלוטים - לא תואם 4 תמונות, לכן נשתמש בתבנית אחרת
    # עם 4 סלוטים (אין עוד כזו בקטלוג) - נבדוק דווקא שינוי שדורש הסרת
    # תמונה קודם. לשם הבדיקה הזו, נבנה קבוצה של 3 במקום.
    conn.execute(
        "DELETE FROM spread_photos WHERE spread_id = ? AND slot_index = 3", (first_spread["id"],)
    )
    conn.commit()

    result = le_mod.change_template(project_id, first_spread["id"], "trio-mixed", conn)

    assert result.status == "ok", result.message
    assert result.template_id == "trio-mixed"

    updated_spread = conn.execute(
        "SELECT template_id, version FROM spreads WHERE id = ?", (first_spread["id"],)
    ).fetchone()
    assert updated_spread["template_id"] == "trio-mixed"
    assert updated_spread["version"] == first_spread["version"] + 1

    after_rows = _spread_photos(conn, first_spread["id"])
    assert len(after_rows) == 3
    dominant_after = [r["photo_id"] for r in after_rows if r["is_dominant"]]
    assert dominant_after == [dominant_before]


# ---------------------------------------------------------------------------
# (ז) שינוי תבנית נדחה - מספר סלוטים לא תואם.
# ---------------------------------------------------------------------------
def test_change_template_rejected_for_slot_count_mismatch(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    # 60 תמונות -> 4 בדיוק לכל כפולה (grid-mixed).
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id, count=60)
    first_spread = spreads[0]

    result = le_mod.change_template(project_id, first_spread["id"], "trio-mixed", conn)

    assert result.status == "error"
    assert result.error_reason == "slot_count_mismatch"

    # לא השתנה כלום.
    unchanged = conn.execute(
        "SELECT template_id, version FROM spreads WHERE id = ?", (first_spread["id"],)
    ).fetchone()
    assert unchanged["template_id"] == "grid-mixed"
    assert unchanged["version"] == first_spread["version"]
    assert len(_spread_photos(conn, first_spread["id"])) == 4


# ---------------------------------------------------------------------------
# (ח) שינוי תבנית נדחה - template_id לא קיים.
# ---------------------------------------------------------------------------
def test_change_template_rejected_for_unknown_template(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id, count=60)
    first_spread = spreads[0]

    result = le_mod.change_template(project_id, first_spread["id"], "does-not-exist", conn)

    assert result.status == "error"
    assert result.error_reason == "template_not_found"


def test_change_template_rejected_for_unknown_spread(engine):
    le_mod, conn, _db_mod = engine
    project_id = _make_project(conn)
    _setup_layout(le_mod, conn, project_id, count=60)

    result = le_mod.change_template(project_id, "does-not-exist", "trio-mixed", conn)
    assert result.status == "error"
    assert result.error_reason == "spread_not_found"


# ---------------------------------------------------------------------------
# (ט) "שחזור" - get_spreads אחרי swap/שינוי תבנית, ואחרי חיבור DB חדש
# (מדמה "פתיחה מחדש" של הפרויקט).
# ---------------------------------------------------------------------------
def test_get_spreads_reflects_persisted_swap_after_reopening_connection(engine):
    le_mod, conn, db_mod = engine
    project_id = _make_project(conn)
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id, count=60)

    first_spread = spreads[0]
    old_photo = _spread_photos(conn, first_spread["id"])[0]["photo_id"]
    new_photo = _insert_photo(conn, project_id, selected_for_album=0, selection_order=None)

    result = le_mod.replace_photo_in_slot(project_id, first_spread["id"], old_photo, new_photo, conn)
    assert result.status == "ok"

    # "פתיחה מחדש" - חיבור DB חדש ועצמאי (לא אותו conn) מול אותו קובץ.
    fresh_conn = db_mod.get_connection()
    try:
        state = le_mod.get_spreads(project_id, fresh_conn)
        first_state = [s for s in state if s["spread_id"] == first_spread["id"]][0]
        placed_ids = {p["photo_id"] for p in first_state["photos"]}
        assert new_photo in placed_ids
        assert old_photo not in placed_ids
        assert first_state["version"] == first_spread["version"] + 1
    finally:
        fresh_conn.close()


def test_get_spreads_reflects_persisted_template_change_after_reopening_connection(engine):
    le_mod, conn, db_mod = engine
    project_id = _make_project(conn)
    _photo_ids, spreads = _setup_layout(le_mod, conn, project_id, count=60)
    first_spread = spreads[0]

    conn.execute(
        "DELETE FROM spread_photos WHERE spread_id = ? AND slot_index = 3", (first_spread["id"],)
    )
    conn.commit()

    result = le_mod.change_template(project_id, first_spread["id"], "trio-mixed", conn)
    assert result.status == "ok"

    fresh_conn = db_mod.get_connection()
    try:
        state = le_mod.get_spreads(project_id, fresh_conn)
        first_state = [s for s in state if s["spread_id"] == first_spread["id"]][0]
        assert first_state["template_id"] == "trio-mixed"
        assert len(first_state["photos"]) == 3
    finally:
        fresh_conn.close()
