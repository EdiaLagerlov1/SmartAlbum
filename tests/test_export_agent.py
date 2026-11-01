"""
בדיקות יחידה ל-export_agent F09 (issue #10): export_album/ExportResult.

משתמש באותו דפוס fixture בדיוק כמו tests/test_layout_engine_f08.py (DB
זמני, הכנסת שורות photos ישירות, generate_layout אמיתי ליצירת spreads).

** הערת-סביבה (ראה גם INTERFACES.md/PR) **: WeasyPrint אינו מותקן בסביבה
הזו (PyPI חסום). כל הטסטים כאן רצים *בלי* WeasyPrint מותקן, ומכוסים:
  (א) structure.json - תוכן נכון, נתיב נכון, שיקוף מדויק של ה-DB.
  (ב) נתיבים (storage.export_pdf_path/export_structure_path).
  (ג) מקרי-שגיאה: פרויקט לא קיים, אין spreads עדיין, יותר מ-60 תמונות
      באלבום, תמונת must חסרה מהאלבום, weasyprint לא מותקן - כולם
      status="error" נקי (לא exception/traceback).
  (ד) עדכוני processing_jobs (running -> done/failed) לשלב 'export'.
  (ה) בניית HTML/CSS (_build_album_html) - לא תלויה ב-weasyprint בכלל.

שני טסטים בלבד, המכוסים ב-@pytest.mark.skipif(weasyprint is None, ...),
בודקים ייצור PDF-bytes אמיתי - **לא רצים בסביבה הזו**, ראה ה-skip reason.
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

# ייבוא ברמת-המודול (לא בתוך fixture) כדי שה-skipif guard מתחת יוכל לבדוק
# את זמינות weasyprint בזמן collection, בלי תלות בסדר-טעינת fixtures.
import export_agent.api as _export_agent_module  # noqa: E402


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

    import export_agent.api as ea_mod
    import layout_engine.api as le_mod
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
    thumb_path: str | None = None,
    selection_order: int | None = None,
    selected_for_album: int = 1,
) -> str:
    photo_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO photos (id, project_id, original_filename, original_path, uploaded_at,
                             flag, width, height, thumb_path, selected_for_album, selection_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            thumb_path,
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


def _generate_full_layout(le_mod, conn, project_id, count=20, **kwargs):
    photo_ids = _make_selected_photos(conn, project_id, count, **kwargs)
    result = le_mod.generate_layout(project_id, photo_ids, conn)
    assert result.status == "ok", result.message
    return photo_ids


# ---------------------------------------------------------------------------
# מקרי שגיאה שלא דורשים weasyprint בכלל
# ---------------------------------------------------------------------------
def test_export_album_project_not_found(engine):
    le_mod, ea_mod, storage_mod, conn = engine

    result = ea_mod.export_album("does-not-exist", conn)

    assert result.status == "error"
    assert result.pdf_path is None
    assert result.structure_path is None
    assert "לא נמצא" in result.message

    # לא נכתבה שורת processing_jobs - FK לא מאפשר project_id לא קיים.
    jobs = conn.execute(
        "SELECT * FROM processing_jobs WHERE project_id = ?", ("does-not-exist",)
    ).fetchall()
    assert jobs == []


def test_export_album_no_spreads_yet(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)

    result = ea_mod.export_album(project_id, conn)

    assert result.status == "error"
    assert result.pdf_path is None
    assert result.structure_path is None
    assert "פריסה" in result.message

    job = conn.execute(
        "SELECT status, error_reason FROM processing_jobs WHERE project_id = ? AND stage = 'export'",
        (project_id,),
    ).fetchone()
    assert job["status"] == "failed"
    assert job["error_reason"] == "layout_not_generated"

    # לא נכתב שום קובץ בפועל.
    assert not storage_mod.export_pdf_path(project_id).exists()
    assert not storage_mod.export_structure_path(project_id).exists()


def test_export_album_must_photo_missing_from_album_is_error(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    _generate_full_layout(le_mod, conn, project_id, count=20)

    # תמונת must שלא נכנסה ללייאאוט (לא ב-selected_photo_ids שהועברו ל-generate_layout).
    _insert_photo(conn, project_id, flag="must", selected_for_album=0, selection_order=None)

    result = ea_mod.export_album(project_id, conn)

    assert result.status == "error"
    assert result.pdf_path is None
    assert "חובה" in result.message

    job = conn.execute(
        "SELECT status, error_reason FROM processing_jobs WHERE project_id = ? AND stage = 'export'",
        (project_id,),
    ).fetchone()
    assert job["status"] == "failed"
    assert job["error_reason"] == "must_photos_missing_from_album"


def test_export_album_too_many_photos_in_album_is_error(engine, monkeypatch):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    _generate_full_layout(le_mod, conn, project_id, count=20)

    # מדמים "מצב בחירה לא תקין" בלי לבנות באמת 61 תמונות/כפולות - מנמיכים
    # את הסף כדי לבדוק את מסלול-השגיאה בלי דרישות-נתונים מיותרות.
    monkeypatch.setattr(ea_mod, "MAX_EXPORT_PHOTOS", 5)

    result = ea_mod.export_album(project_id, conn)

    assert result.status == "error"
    assert result.pdf_path is None
    assert result.structure_path is None

    job = conn.execute(
        "SELECT status, error_reason FROM processing_jobs WHERE project_id = ? AND stage = 'export'",
        (project_id,),
    ).fetchone()
    assert job["status"] == "failed"
    assert job["error_reason"] == "too_many_photos_in_album"


def test_export_album_weasyprint_unavailable_is_clean_error(engine, monkeypatch):
    """ה-flow המלא (פרויקט תקין + spreads + ולידציה תקינה) אבל weasyprint
    לא מותקן - *זו בדיוק המציאות בסביבה הזו* (לא מדמים, זה המצב האמיתי)."""
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    _generate_full_layout(le_mod, conn, project_id, count=20)

    assert ea_mod.weasyprint is None  # מתעד את מגבלת-הסביבה בפועל

    result = ea_mod.export_album(project_id, conn)

    assert result.status == "error"
    assert result.pdf_path is None
    assert result.structure_path is None
    assert "WeasyPrint" in result.message or "PDF" in result.message

    job = conn.execute(
        "SELECT status, error_reason FROM processing_jobs WHERE project_id = ? AND stage = 'export'",
        (project_id,),
    ).fetchone()
    assert job["status"] == "failed"
    assert job["error_reason"] == "weasyprint_unavailable"

    # "distinguish not-generated from renderer-unavailable" - הודעות שונות.
    no_layout_project = _make_project(conn)
    no_layout_result = ea_mod.export_album(no_layout_project, conn)
    assert no_layout_result.message != result.message


def test_export_album_running_status_set_before_failure(engine):
    """processing_jobs עובר ל-'running' לפני שהכשל (כל כשל) נכתב - בודקים
    זאת דרך תוצאת הכשל הסופית (failed) שמחליפה running, לא ישירות, כי אין
    hook לבדוק מצב-ביניים בתוך קריאה סינכרונית יחידה - תואם את אותה
    מגבלת-בדיקה הקיימת למודולים אחרים (run_selection/generate_layout)."""
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)

    assert (
        conn.execute(
            "SELECT 1 FROM processing_jobs WHERE project_id = ? AND stage = 'export'",
            (project_id,),
        ).fetchone()
        is None
    )

    ea_mod.export_album(project_id, conn)

    job = conn.execute(
        "SELECT status FROM processing_jobs WHERE project_id = ? AND stage = 'export'",
        (project_id,),
    ).fetchone()
    assert job is not None
    assert job["status"] == "failed"  # לא "running" שנשאר תקוע


# ---------------------------------------------------------------------------
# structure.json / HTML בנייה - לא תלויים ב-weasyprint בכלל
# ---------------------------------------------------------------------------
def test_fetch_spreads_with_photos_matches_db_state(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    photo_ids = _generate_full_layout(le_mod, conn, project_id, count=20)

    spreads = ea_mod._fetch_spreads_with_photos(project_id, conn)

    assert len(spreads) == 15
    all_photo_ids = [p["photo_id"] for s in spreads for p in s["photos"]]
    assert sorted(all_photo_ids) == sorted(photo_ids)
    # סדר כפולות לפי position (1..15), סדר תמונות בכל כפולה לפי slot_index.
    assert [s["position"] for s in spreads] == list(range(1, 16))
    for spread in spreads:
        slot_indices = [p["slot_index"] for p in spread["photos"]]
        assert slot_indices == sorted(slot_indices)


def test_build_structure_payload_has_full_reconstruction_data(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    _generate_full_layout(le_mod, conn, project_id, count=20)

    spreads = ea_mod._fetch_spreads_with_photos(project_id, conn)
    payload = ea_mod._build_structure_payload(project_id, spreads)

    assert payload["project_id"] == project_id
    assert "exported_at" in payload
    assert len(payload["spreads"]) == 15
    for spread in payload["spreads"]:
        assert set(spread.keys()) >= {"id", "position", "template_id", "version", "photos"}
        for photo in spread["photos"]:
            assert set(photo.keys()) >= {"slot_index", "photo_id", "is_dominant"}


def test_build_album_html_includes_same_photos_order_and_dominant_marker(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    _generate_full_layout(le_mod, conn, project_id, count=20)

    spreads = ea_mod._fetch_spreads_with_photos(project_id, conn)
    html, css = ea_mod._build_album_html(project_id, spreads)

    assert "<html" in html
    assert "spread-card" in css
    # כל photo_id של תמונה דומיננטית מקבל markup "dominant" ב-html.
    for spread in spreads:
        assert f"כפולה {spread['position']}" in html
        assert spread["template_id"] in html
        for photo in spread["photos"]:
            if photo["is_dominant"]:
                assert "דומיננטי" in html


def test_validate_export_readiness_ok_for_valid_album(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    _generate_full_layout(le_mod, conn, project_id, count=20)

    spreads = ea_mod._fetch_spreads_with_photos(project_id, conn)
    assert ea_mod._validate_export_readiness(project_id, conn, spreads) is None


def test_export_pdf_path_and_structure_path_match_storage_helpers(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)

    assert str(storage_mod.export_pdf_path(project_id)).endswith("album.pdf")
    assert str(storage_mod.export_structure_path(project_id)).endswith("album_structure.json")


# ---------------------------------------------------------------------------
# WeasyPrint-dependent: PDF bytes אמיתיים. מדולגים בסביבה הזו (לא מותקן).
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    _export_agent_module.weasyprint is None,
    reason="weasyprint not installed; PyPI unreachable in this sandbox",
)
def test_export_album_writes_real_pdf_bytes(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    project_id = _make_project(conn)
    _generate_full_layout(le_mod, conn, project_id, count=20)

    result = ea_mod.export_album(project_id, conn)

    assert result.status == "ok", result.message
    pdf_path = storage_mod.export_pdf_path(project_id)
    assert pdf_path.is_file()
    assert pdf_path.read_bytes().startswith(b"%PDF")

    structure_path = storage_mod.export_structure_path(project_id)
    assert structure_path.is_file()
    structure = json.loads(structure_path.read_text(encoding="utf-8"))
    assert len(structure["spreads"]) == 15

    job = conn.execute(
        "SELECT status FROM processing_jobs WHERE project_id = ? AND stage = 'export'",
        (project_id,),
    ).fetchone()
    assert job["status"] == "done"


@pytest.mark.skipif(
    _export_agent_module.weasyprint is None,
    reason="weasyprint not installed; PyPI unreachable in this sandbox",
)
def test_render_pdf_bytes_directly(engine):
    le_mod, ea_mod, storage_mod, conn = engine
    pdf_bytes = ea_mod._render_pdf_bytes("<html><body><h1>hi</h1></body></html>", "body{}")
    assert pdf_bytes.startswith(b"%PDF")
