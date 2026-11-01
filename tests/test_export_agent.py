"""
בדיקות יחידה ל-export_agent.export_album (F09, issue #10).

משתמש באותו דפוס fixture בדיוק כמו tests/test_layout_engine_f08.py (DB
זמני + SMARTALBUM_STORAGE_ROOT זמני, הכנסת שורות photos/spreads/
spread_photos ישירות). בשונה מ-test_layout_engine_f08.py, כאן `photos.
original_path` מצביע על קובצי JPEG **אמיתיים** שנכתבים ל-tmp_path (לא
`/tmp/<uuid>.jpg` פיקטיבי) - כי export_album בפועל פותחת/מצלמת (Pillow)
את קבצי המקור כדי לרנדר את ה-PDF.

מכסה (לפי בקשת המשימה):
  (א) מסלול הצלחה: spreads/spread_photos תקינים -> status="ok", PDF
      תקין (מתחיל ב-%PDF, נפתח ע"י pypdf עם מספר עמודים = מספר הכפולות),
      album_structure.json נכתב ומשקף את מצב ה-DB (סדר/template_id/
      photo_id/slot_index/is_dominant).
  (ב) כלל-הגנה: תמונת must שלא נכללה בפועל ב-spread_photos -> status=
      "error", לא נכתב PDF, processing_jobs(stage='export')='failed'.
  (ג) כלל-הגנה: סך תמונות ייחודיות ב-spread_photos > 60 -> status=
      "error" (אותה התנהגות).
  (ד) processing_jobs(stage='export') עובר pending/לא-קיים -> running ->
      done בהצלחה.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image

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

    import export_agent.api as ea_mod
    import layout_engine.api as le_mod
    import storage as storage_mod

    conn = db_mod.get_connection()
    yield ea_mod, le_mod, storage_mod, conn, tmp_path
    conn.close()


def _make_project(conn, name="חתונה") -> str:
    project_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO projects (id, name, created_at, status) VALUES (?, ?, ?, 'draft')",
        (project_id, name, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return project_id


def _make_original_image(tmp_path, photo_id: str, *, size=(400, 300), color="red") -> str:
    """כותב קובץ JPEG אמיתי לדיסק (לא נתיב פיקטיבי) - export_album פותחת
    אותו בפועל עם Pillow כדי לחתוך/לצייר ל-PDF."""
    originals_dir = tmp_path / "originals"
    originals_dir.mkdir(parents=True, exist_ok=True)
    path = originals_dir / f"{photo_id}.jpg"
    img = Image.new("RGB", size, color=color)
    img.save(path, "JPEG", quality=90)
    return str(path)


def _insert_photo(
    conn,
    project_id: str,
    tmp_path,
    *,
    flag: str = "none",
    width: int = 400,
    height: int = 300,
    selected_for_album: int = 1,
    selection_order: int | None = None,
) -> str:
    photo_id = str(uuid.uuid4())
    original_path = _make_original_image(tmp_path, photo_id, size=(width, height))
    conn.execute(
        """
        INSERT INTO photos (id, project_id, original_filename, original_path, uploaded_at,
                             flag, width, height, selected_for_album, selection_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            photo_id,
            project_id,
            f"{photo_id}.jpg",
            original_path,
            datetime.now(timezone.utc).isoformat(),
            flag,
            width,
            height,
            selected_for_album,
            selection_order,
        ),
    )
    conn.commit()
    return photo_id


def _make_spread(conn, project_id, position, template_id, photo_slots, version=1):
    """photo_slots: list[(slot_index, photo_id, is_dominant)]."""
    spread_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO spreads (id, project_id, position, template_id, version, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (spread_id, project_id, position, template_id, version, now),
    )
    for slot_index, photo_id, is_dominant in photo_slots:
        conn.execute(
            """INSERT INTO spread_photos (spread_id, photo_id, slot_index, is_dominant)
               VALUES (?, ?, ?, ?)""",
            (spread_id, photo_id, slot_index, 1 if is_dominant else 0),
        )
    conn.commit()
    return spread_id


def _job_status(conn, project_id, stage):
    row = conn.execute(
        "SELECT status, error_reason FROM processing_jobs WHERE project_id = ? AND stage = ?",
        (project_id, stage),
    ).fetchone()
    return row


# ---------------------------------------------------------------------------
# (א) מסלול הצלחה
# ---------------------------------------------------------------------------
def test_export_album_happy_path_writes_valid_pdf_and_structure(engine):
    ea_mod, le_mod, storage_mod, conn, tmp_path = engine
    project_id = _make_project(conn)

    p1 = _insert_photo(conn, project_id, tmp_path, width=600, height=400)
    p2 = _insert_photo(conn, project_id, tmp_path, width=300, height=500)
    p3 = _insert_photo(conn, project_id, tmp_path, width=300, height=500)

    _make_spread(
        conn, project_id, 1, "single-feature-landscape", [(0, p1, True)]
    )
    _make_spread(
        conn, project_id, 2, "duo-portrait", [(0, p2, True), (1, p3, False)]
    )

    result = ea_mod.export_album(project_id, conn)

    assert result.status == "ok", result.message
    assert result.pdf_path is not None
    assert result.structure_path is not None

    pdf_path = storage_mod.export_pdf_path(project_id)
    structure_path = storage_mod.export_structure_path(project_id)
    assert str(pdf_path) == result.pdf_path
    assert str(structure_path) == result.structure_path

    assert pdf_path.exists()
    pdf_bytes = pdf_path.read_bytes()
    assert pdf_bytes.startswith(b"%PDF")

    import pypdf

    reader = pypdf.PdfReader(str(pdf_path))
    assert len(reader.pages) == 2  # 2 spreads -> 2 pages, אחד לכל כפולה

    assert structure_path.exists()
    structure = json.loads(structure_path.read_text(encoding="utf-8"))
    assert structure["project_id"] == project_id
    assert len(structure["spreads"]) == 2

    spread1, spread2 = structure["spreads"]
    assert spread1["position"] == 1
    assert spread1["template_id"] == "single-feature-landscape"
    assert spread1["photos"] == [
        {"slot_index": 0, "photo_id": p1, "is_dominant": True}
    ]

    assert spread2["position"] == 2
    assert spread2["template_id"] == "duo-portrait"
    assert spread2["photos"] == [
        {"slot_index": 0, "photo_id": p2, "is_dominant": True},
        {"slot_index": 1, "photo_id": p3, "is_dominant": False},
    ]

    job = _job_status(conn, project_id, "export")
    assert job["status"] == "done"
    assert job["error_reason"] is None


def test_export_album_empty_spreads_still_ok(engine):
    """פרויקט בלי כפולות כלל - לא כשל, PDF עם 0 עמודים + JSON עם spreads
    ריק (עקבי עם ההתנהגות המתועדת של snapshot_structure למצב-קצה דומה)."""
    ea_mod, le_mod, storage_mod, conn, tmp_path = engine
    project_id = _make_project(conn)

    result = ea_mod.export_album(project_id, conn)

    assert result.status == "ok", result.message
    structure = json.loads(
        storage_mod.export_structure_path(project_id).read_text(encoding="utf-8")
    )
    assert structure["spreads"] == []

    import pypdf

    reader = pypdf.PdfReader(result.pdf_path)
    assert len(reader.pages) == 0


# ---------------------------------------------------------------------------
# (ב) כלל-הגנה: תמונת must שלא נכללה
# ---------------------------------------------------------------------------
def test_export_album_rejects_missing_must_photo(engine):
    ea_mod, le_mod, storage_mod, conn, tmp_path = engine
    project_id = _make_project(conn)

    included = _insert_photo(conn, project_id, tmp_path)
    missing_must = _insert_photo(conn, project_id, tmp_path, flag="must")

    _make_spread(
        conn, project_id, 1, "single-feature-landscape", [(0, included, True)]
    )
    # missing_must לא משובצת לשום spread_photos - סימן לסטטוס-בחירה לא תקין.

    result = ea_mod.export_album(project_id, conn)

    assert result.status == "error"
    assert result.pdf_path is None
    assert result.structure_path is None
    assert result.message

    assert not storage_mod.export_pdf_path(project_id).exists()
    assert not storage_mod.export_structure_path(project_id).exists()

    job = _job_status(conn, project_id, "export")
    assert job["status"] == "failed"
    assert job["error_reason"]


# ---------------------------------------------------------------------------
# (ג) כלל-הגנה: סך תמונות ייחודיות > 60
# ---------------------------------------------------------------------------
def test_export_album_rejects_too_many_photos(engine):
    ea_mod, le_mod, storage_mod, conn, tmp_path = engine
    project_id = _make_project(conn)

    photo_ids = [_insert_photo(conn, project_id, tmp_path) for _ in range(61)]

    # משבצים את כל 61 התמונות בפועל ל-spread_photos (לא משנה אם זה חורג
    # מ-MAX_PHOTOS_PER_SPREAD של layout-engine - הבדיקה כאן היא על ספירת
    # photo_id ייחודיים ב-spread_photos, לא על תקינות template/slot_count).
    spread_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO spreads (id, project_id, position, template_id, version, updated_at)
           VALUES (?, ?, 1, 'grid-mixed', 1, ?)""",
        (spread_id, project_id, now),
    )
    for i, pid in enumerate(photo_ids):
        conn.execute(
            """INSERT INTO spread_photos (spread_id, photo_id, slot_index, is_dominant)
               VALUES (?, ?, ?, 0)""",
            (spread_id, pid, i),
        )
    conn.commit()

    result = ea_mod.export_album(project_id, conn)

    assert result.status == "error"
    assert result.pdf_path is None
    assert result.structure_path is None
    assert "60" in result.message

    assert not storage_mod.export_pdf_path(project_id).exists()

    job = _job_status(conn, project_id, "export")
    assert job["status"] == "failed"


# ---------------------------------------------------------------------------
# (ד) processing_jobs(stage='export') מעבר סטטוסים
# ---------------------------------------------------------------------------
def test_export_album_job_status_transitions_to_done_on_success(engine):
    ea_mod, le_mod, storage_mod, conn, tmp_path = engine
    project_id = _make_project(conn)

    assert _job_status(conn, project_id, "export") is None

    p1 = _insert_photo(conn, project_id, tmp_path)
    _make_spread(conn, project_id, 1, "single-feature-landscape", [(0, p1, True)])

    result = ea_mod.export_album(project_id, conn)
    assert result.status == "ok", result.message

    job = _job_status(conn, project_id, "export")
    assert job["status"] == "done"
    assert job["error_reason"] is None


def test_export_album_job_status_failed_then_succeeds_after_fix(engine):
    """ניסיון חוזר (F10): אחרי כשל (must חסר), תיקון הבחירה וקריאה חוזרת
    -> processing_jobs(stage='export') מתעדכן ל-done (לא נשאר 'failed')."""
    ea_mod, le_mod, storage_mod, conn, tmp_path = engine
    project_id = _make_project(conn)

    p1 = _insert_photo(conn, project_id, tmp_path, flag="must")
    _make_spread(conn, project_id, 1, "single-feature-landscape", [])
    # עדיין לא משבצים את p1 (ה-must) ל-spread_photos -> כשל ראשון.

    first = ea_mod.export_album(project_id, conn)
    assert first.status == "error"
    assert _job_status(conn, project_id, "export")["status"] == "failed"

    # "תיקון": משבצים את p1 בפועל לכפולה.
    spread_row = conn.execute(
        "SELECT id FROM spreads WHERE project_id = ?", (project_id,)
    ).fetchone()
    conn.execute(
        """INSERT INTO spread_photos (spread_id, photo_id, slot_index, is_dominant)
           VALUES (?, ?, 0, 1)""",
        (spread_row["id"], p1),
    )
    conn.commit()

    second = ea_mod.export_album(project_id, conn)
    assert second.status == "ok", second.message
    assert _job_status(conn, project_id, "export")["status"] == "done"
