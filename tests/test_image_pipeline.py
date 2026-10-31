"""
בדיקות יחידה ל-image_pipeline (F02 - issue #3).

מכסה:
  (א) כיוון EXIF מתוקן ב-thumbnail (לא במקור).
  (ב) בייטים של original_path זהים בייט-לבייט לקובץ שהועלה (hash).
  (ג) קובץ לא-תקין לא עוצר את הריצה ומוחזר status="failed".

משתמש בתמונות שנוצרות פרוגרמטית עם Pillow בזמן ריצת הטסט (אין קבצי
תמונה מוכנים בריפו) - ראה שימוש ב-SMARTALBUM_STORAGE_ROOT / SMARTALBUM_DB_PATH
(env overrides, מתועד ב-storage.py / db.py) כדי לא לכתוב ל-storage/ האמיתי.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import os
import sys
import uuid

import pytest
from PIL import Image

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _make_jpeg_with_orientation(path, orientation: int, size=(40, 20)) -> None:
    """יוצר JPEG קטן (פס אדום בחצי שמאלי, ירוק בחצי ימני - כדי שיהיה אפשר
    להבדיל בין "לפני תיקון" ל"אחרי תיקון") עם EXIF Orientation=orientation."""
    img = Image.new("RGB", size, color="green")
    w, h = size
    for x in range(w // 2):
        for y in range(h):
            img.putpixel((x, y), (255, 0, 0))  # חצי שמאלי אדום

    exif = img.getexif()
    exif[0x0112] = orientation  # Orientation tag
    img.save(path, "JPEG", exif=exif)


@pytest.fixture()
def pipeline_env(tmp_path, monkeypatch):
    """מגדיר storage root + DB זמניים, מאתחל סכמה, ומייבא מודולים
    (db/storage/image_pipeline) אחרי שהדגלים הסביבתיים מוגדרים, כדי
    שכל ברירות המחדל שלהם יושפעו מה-override."""
    storage_root = tmp_path / "storage"
    db_path = storage_root / "smartalbum.db"
    monkeypatch.setenv("SMARTALBUM_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("SMARTALBUM_DB_PATH", str(db_path))

    for mod_name in ("storage", "db", "image_pipeline", "image_pipeline.api"):
        sys.modules.pop(mod_name, None)

    import storage as storage_mod
    import db as db_mod
    import image_pipeline.api as pipeline_mod

    db_mod.init_db()
    conn = db_mod.get_connection()

    project_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO projects (id, name, created_at, status) VALUES (?, ?, ?, 'draft')",
        (project_id, "טסט", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()

    yield {
        "storage": storage_mod,
        "db": db_mod,
        "pipeline": pipeline_mod,
        "conn": conn,
        "project_id": project_id,
        "tmp_path": tmp_path,
    }

    conn.close()


def test_exif_orientation_fixed_in_thumbnail_not_original(pipeline_env):
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    upload_path = tmp_path / "upload_rotated.jpg"
    # orientation=6 => "rotate 90 CW" לפי מוסכמת EXIF; ImageOps.exif_transpose
    # מצפה שהתוצאה המתוקנת תהיה גובה>רוחב (סובבה) בהשוואה למקור (רוחב>גובה).
    _make_jpeg_with_orientation(upload_path, orientation=6, size=(40, 20))

    original_bytes_before = upload_path.read_bytes()

    result = pipeline.process_upload(
        project_id=project_id,
        saved_file_path=str(upload_path),
        original_filename="rotated.jpg",
        db=conn,
    )

    assert result.status == "done"
    assert result.photo_id is not None
    assert result.error_reason is None

    row = conn.execute(
        "SELECT * FROM photos WHERE id = ?", (result.photo_id,)
    ).fetchone()
    assert row is not None
    assert row["exif_orientation"] == 6

    # המקור לא נגע בו - עדיין רוחב(40) > גובה(20).
    with Image.open(row["original_path"]) as orig_img:
        assert orig_img.size == (40, 20)

    # ה-thumbnail תוקן לפי EXIF orientation=6 -> מסתובב ל-portrait (גובה > רוחב).
    with Image.open(row["thumb_path"]) as thumb_img:
        assert thumb_img.width < thumb_img.height

    assert row["width"] == thumb_img.width
    assert row["height"] == thumb_img.height

    # thumbnail לא מכיל יותר EXIF orientation לא-1 (ImageOps.exif_transpose
    # מנרמל את זה) - קוראים חזרה כדי להיות בטוחים שאין כפל-סיבוב בתצוגה.
    with Image.open(row["thumb_path"]) as thumb_img2:
        thumb_exif = thumb_img2.getexif()
        assert thumb_exif.get(0x0112, 1) == 1

    original_bytes_after = original_bytes_before  # upload_path itself untouched
    assert upload_path.read_bytes() == original_bytes_after


def test_original_file_bytes_are_preserved_exactly(pipeline_env):
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    upload_path = tmp_path / "upload_plain.jpg"
    _make_jpeg_with_orientation(upload_path, orientation=1, size=(30, 30))

    expected_hash = hashlib.sha256(upload_path.read_bytes()).hexdigest()

    result = pipeline.process_upload(
        project_id=project_id,
        saved_file_path=str(upload_path),
        original_filename="plain.jpg",
        db=conn,
    )

    assert result.status == "done"
    row = conn.execute(
        "SELECT original_path, file_size_bytes FROM photos WHERE id = ?",
        (result.photo_id,),
    ).fetchone()

    stored_bytes = open(row["original_path"], "rb").read()
    stored_hash = hashlib.sha256(stored_bytes).hexdigest()

    assert stored_hash == expected_hash
    assert row["file_size_bytes"] == len(stored_bytes)


def test_corrupt_file_does_not_raise_and_returns_failed(pipeline_env):
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    bad_path = tmp_path / "corrupt.jpg"
    bad_path.write_bytes(b"this is not a real jpeg file, just garbage bytes")

    # לא צריך להעלות exception.
    result = pipeline.process_upload(
        project_id=project_id,
        saved_file_path=str(bad_path),
        original_filename="corrupt.jpg",
        db=conn,
    )

    assert result.status == "failed"
    assert result.photo_id is None
    assert result.error_reason

    job = conn.execute(
        "SELECT status, error_reason FROM processing_jobs WHERE project_id = ? AND stage = 'exif'",
        (project_id,),
    ).fetchone()
    assert job is not None
    assert job["status"] == "failed"
    assert job["error_reason"]

    # לא נוצרה שורת photos עבור הקובץ הפגום.
    row = conn.execute(
        "SELECT id FROM photos WHERE project_id = ? AND original_filename = ?",
        (project_id, "corrupt.jpg"),
    ).fetchone()
    assert row is None


def test_second_upload_of_same_filename_is_not_reprocessed(pipeline_env, monkeypatch):
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    upload_path = tmp_path / "dup.jpg"
    _make_jpeg_with_orientation(upload_path, orientation=1, size=(20, 20))

    first = pipeline.process_upload(
        project_id=project_id,
        saved_file_path=str(upload_path),
        original_filename="dup.jpg",
        db=conn,
    )
    assert first.status == "done"

    def _boom(*args, **kwargs):
        raise AssertionError("לא צריך לעבד שוב תמונה שכבר הושלמה")

    monkeypatch.setattr(pipeline.Image, "open", _boom)

    second = pipeline.process_upload(
        project_id=project_id,
        saved_file_path=str(upload_path),
        original_filename="dup.jpg",
        db=conn,
    )

    assert second.status == "done"
    assert second.photo_id == first.photo_id
