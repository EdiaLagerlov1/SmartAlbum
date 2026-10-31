"""
בדיקות ל-db.py (architect-owned) - בעיקר delete_project (F10, issue #11):
מחיקת פרויקט חייבת למחוק גם את כל השורות הקשורות ב-DB (photos, spreads,
spread_photos, processing_jobs, projects) וגם את כל הקבצים בדיסק
(originals/thumbnails/exports תחת storage/projects/<id>/).

משתמש ב-SMARTALBUM_STORAGE_ROOT / SMARTALBUM_DB_PATH (env vars) כדי לרוץ
מול storage/DB זמניים - לא נוגע ב-storage/smartalbum.db האמיתי.
"""

from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture()
def dbmods(tmp_path, monkeypatch):
    """מייבא storage/db מחדש מול storage root + DB זמניים לכל טסט."""
    storage_root = tmp_path / "storage"
    db_path = storage_root / "smartalbum.db"
    monkeypatch.setenv("SMARTALBUM_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("SMARTALBUM_DB_PATH", str(db_path))

    for mod_name in ("storage", "db"):
        sys.modules.pop(mod_name, None)

    import storage as storage_mod
    import db as db_mod

    db_mod.init_db()
    return storage_mod, db_mod


def _seed_project(storage_mod, db_mod, project_id: str = "proj-1") -> None:
    """יוצר פרויקט + שורות קשורות בכל הטבלאות + קבצים בדיסק (originals/
    thumbnails/exports), כדי שהטסט יבדוק מחיקה אמיתית, לא טבלה ריקה."""
    photo_id = f"{project_id}-photo-1"
    spread_id = f"{project_id}-spread-1"
    job_id = f"{project_id}-job-1"

    conn = db_mod.get_connection()
    try:
        conn.execute(
            "INSERT INTO projects (id, name, created_at, status) VALUES (?, ?, ?, 'draft')",
            (project_id, "פרויקט בדיקה", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO photos (id, project_id, original_filename, original_path, uploaded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (photo_id, project_id, "a.jpg", "/tmp/a.jpg", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO spreads (id, project_id, position, template_id, updated_at) VALUES (?, ?, ?, ?, ?)",
            (spread_id, project_id, 1, "4up-grid", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO spread_photos (spread_id, photo_id, slot_index) VALUES (?, ?, ?)",
            (spread_id, photo_id, 0),
        )
        conn.execute(
            "INSERT INTO processing_jobs (id, project_id, stage, status, updated_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, project_id, "upload", "done", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    # קבצים בדיסק - originals/thumbnails/exports, כמו שimage-pipeline/export-agent
    # היו כותבים בפועל (ראה storage.py).
    storage_mod.ensure_project_dirs(project_id)
    (storage_mod.originals_dir(project_id) / f"{photo_id}.jpg").write_bytes(b"fake-jpeg-bytes")
    (storage_mod.thumbnails_dir(project_id) / f"{photo_id}.jpg").write_bytes(b"fake-thumb-bytes")
    (storage_mod.exports_dir(project_id) / "album.pdf").write_bytes(b"fake-pdf-bytes")


def test_delete_project_removes_all_db_rows(dbmods):
    storage_mod, db_mod = dbmods
    project_id = "proj-1"
    _seed_project(storage_mod, db_mod, project_id)

    db_mod.delete_project(project_id)

    conn = db_mod.get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        assert row["n"] == 0, "projects still has a row after delete_project"

        for table in ("photos", "spreads", "processing_jobs"):
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE project_id = ?", (project_id,)
            ).fetchone()
            assert row["n"] == 0, f"table {table} still has rows after delete_project"

        # spread_photos אין לה project_id ישירות - נבדק דרך ה-spread שנמחק.
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM spread_photos WHERE spread_id = ?",
            (f"{project_id}-spread-1",),
        ).fetchone()
        assert row["n"] == 0, "spread_photos still has rows after delete_project"
    finally:
        conn.close()


def test_delete_project_removes_files_on_disk(dbmods):
    storage_mod, db_mod = dbmods
    project_id = "proj-1"
    _seed_project(storage_mod, db_mod, project_id)

    photo_filename = f"{project_id}-photo-1.jpg"
    project_dir = storage_mod.project_dir(project_id)
    assert project_dir.exists()
    assert (storage_mod.originals_dir(project_id) / photo_filename).exists()
    assert (storage_mod.thumbnails_dir(project_id) / photo_filename).exists()
    assert (storage_mod.exports_dir(project_id) / "album.pdf").exists()

    db_mod.delete_project(project_id)

    assert not project_dir.exists()


def test_delete_project_is_safe_when_project_has_no_files(dbmods):
    """מחיקת פרויקט שאין לו תיקיית קבצים (לא הועלו תמונות) לא קורסת."""
    storage_mod, db_mod = dbmods
    project_id = "proj-empty"

    conn = db_mod.get_connection()
    try:
        conn.execute(
            "INSERT INTO projects (id, name, created_at, status) VALUES (?, ?, ?, 'draft')",
            (project_id, "פרויקט ריק", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    # לא קוראים ensure_project_dirs - מדמה פרויקט שנוצר אבל אין לו עדיין תיקייה.
    db_mod.delete_project(project_id)

    conn = db_mod.get_connection()
    try:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        assert row is None
    finally:
        conn.close()


def test_delete_project_does_not_affect_other_projects(dbmods):
    storage_mod, db_mod = dbmods
    _seed_project(storage_mod, db_mod, "proj-1")
    _seed_project(storage_mod, db_mod, "proj-2")

    db_mod.delete_project("proj-1")

    conn = db_mod.get_connection()
    try:
        remaining = conn.execute("SELECT id FROM projects").fetchall()
        assert [r["id"] for r in remaining] == ["proj-2"]
    finally:
        conn.close()

    assert not storage_mod.project_dir("proj-1").exists()
    assert storage_mod.project_dir("proj-2").exists()
    assert (storage_mod.exports_dir("proj-2") / "album.pdf").exists()
