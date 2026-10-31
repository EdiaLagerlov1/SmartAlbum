"""
בדיקות יחידה ל-image_pipeline (F02 - issue #3, F04 - issue #5).

מכסה:
  (א) כיוון EXIF מתוקן ב-thumbnail (לא במקור).
  (ב) בייטים של original_path זהים בייט-לבייט לקובץ שהועלה (hash).
  (ג) קובץ לא-תקין לא עוצר את הריצה ומוחזר status="failed".
  (ד) F04: sharpness_score גבוה יותר לתמונה חדה מאשר לגרסה מטושטשת שלה.
  (ה) F04: exposure_score גבוה יותר לתמונה מאוזנת מאשר לתמונה שרופה/חשוכה.
  (ו) F04: group_similar_photos מקבץ תמונות כמעט-זהות לאותו similarity_group_id,
      ולא מקבץ תמונה שונה מהותית לאותה קבוצה; מעדכן processing_jobs(stage='grouping').

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

import numpy as np
import pytest
from PIL import Image, ImageFilter

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


def _make_checkerboard_jpeg(path, size=(64, 64), cell=4, seed: int = 0) -> None:
    """תמונה עם תדר-גבוה (לוח שחמט + רעש קל) - "חדה" מבחינת variance of
    Laplacian. seed מאפשר לבנות שתי תמונות "כמעט-זהות" (אותו seed, שינוי
    זעיר) עבור טסט הקיבוץ (F04)."""
    rng = np.random.default_rng(seed)
    w, h = size
    arr = np.zeros((h, w), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            arr[y, x] = 255 if ((x // cell) + (y // cell)) % 2 == 0 else 0
    noise = rng.integers(-3, 4, size=arr.shape)
    arr = np.clip(arr.astype(int) + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr, mode="L").convert("RGB")
    img.save(path, "JPEG", quality=95)


def _make_solid_jpeg(path, gray_value: int, size=(64, 64)) -> None:
    """תמונה חלקה בגוון אחיד - שימושי לטסט חשיפה (over/under-exposed)."""
    img = Image.new("L", size, color=gray_value).convert("RGB")
    img.save(path, "JPEG", quality=95)


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


# ============================================================
# F04 (issue #5): sharpness_score / exposure_score / group_similar_photos
# ============================================================


def test_sharp_image_scores_higher_sharpness_than_blurred_version(pipeline_env):
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    sharp_path = tmp_path / "sharp.jpg"
    _make_checkerboard_jpeg(sharp_path, seed=1)

    blurred_path = tmp_path / "blurred.jpg"
    with Image.open(sharp_path) as img:
        blurred = img.filter(ImageFilter.GaussianBlur(radius=4))
        blurred.save(blurred_path, "JPEG", quality=95)

    sharp_result = pipeline.process_upload(
        project_id=project_id,
        saved_file_path=str(sharp_path),
        original_filename="sharp.jpg",
        db=conn,
    )
    blurred_result = pipeline.process_upload(
        project_id=project_id,
        saved_file_path=str(blurred_path),
        original_filename="blurred.jpg",
        db=conn,
    )

    assert sharp_result.status == "done"
    assert blurred_result.status == "done"

    sharp_row = conn.execute(
        "SELECT sharpness_score FROM photos WHERE id = ?", (sharp_result.photo_id,)
    ).fetchone()
    blurred_row = conn.execute(
        "SELECT sharpness_score FROM photos WHERE id = ?", (blurred_result.photo_id,)
    ).fetchone()

    assert sharp_row["sharpness_score"] is not None
    assert blurred_row["sharpness_score"] is not None
    assert sharp_row["sharpness_score"] > blurred_row["sharpness_score"]


def test_balanced_exposure_scores_higher_than_blown_out_image(pipeline_env):
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    balanced_path = tmp_path / "balanced.jpg"
    _make_solid_jpeg(balanced_path, gray_value=128)

    overexposed_path = tmp_path / "overexposed.jpg"
    _make_solid_jpeg(overexposed_path, gray_value=255)

    balanced_result = pipeline.process_upload(
        project_id=project_id,
        saved_file_path=str(balanced_path),
        original_filename="balanced.jpg",
        db=conn,
    )
    overexposed_result = pipeline.process_upload(
        project_id=project_id,
        saved_file_path=str(overexposed_path),
        original_filename="overexposed.jpg",
        db=conn,
    )

    assert balanced_result.status == "done"
    assert overexposed_result.status == "done"

    balanced_row = conn.execute(
        "SELECT exposure_score FROM photos WHERE id = ?", (balanced_result.photo_id,)
    ).fetchone()
    overexposed_row = conn.execute(
        "SELECT exposure_score FROM photos WHERE id = ?", (overexposed_result.photo_id,)
    ).fetchone()

    assert balanced_row["exposure_score"] is not None
    assert overexposed_row["exposure_score"] is not None
    assert balanced_row["exposure_score"] > overexposed_row["exposure_score"]


def test_group_similar_photos_groups_near_duplicates_not_different_photo(pipeline_env):
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    # שתי תמונות "כמעט-זהות" (אותו seed, רעש זעיר אקראי שונה בכל קריאה
    # אבל אותו דפוס בסיס) - מדמה שתי תמונות מאותו בורסט.
    near_dup_a = tmp_path / "burst_a.jpg"
    near_dup_b = tmp_path / "burst_b.jpg"
    _make_checkerboard_jpeg(near_dup_a, seed=42)
    _make_checkerboard_jpeg(near_dup_b, seed=42)

    # תמונה שונה מהותית (דפוס הפוך) - לא אמורה להתקבץ עם הזוג הקודם.
    different = tmp_path / "different.jpg"
    img = Image.new("L", (64, 64), color=0)
    for y in range(0, 64, 2):
        for x in range(64):
            img.putpixel((x, y), 255)
    img.convert("RGB").save(different, "JPEG", quality=95)

    result_a = pipeline.process_upload(
        project_id=project_id, saved_file_path=str(near_dup_a),
        original_filename="burst_a.jpg", db=conn,
    )
    result_b = pipeline.process_upload(
        project_id=project_id, saved_file_path=str(near_dup_b),
        original_filename="burst_b.jpg", db=conn,
    )
    result_c = pipeline.process_upload(
        project_id=project_id, saved_file_path=str(different),
        original_filename="different.jpg", db=conn,
    )
    assert "done" == result_a.status == result_b.status == result_c.status

    pipeline.group_similar_photos(project_id, conn)

    row_a = conn.execute(
        "SELECT similarity_group_id FROM photos WHERE id = ?", (result_a.photo_id,)
    ).fetchone()
    row_b = conn.execute(
        "SELECT similarity_group_id FROM photos WHERE id = ?", (result_b.photo_id,)
    ).fetchone()
    row_c = conn.execute(
        "SELECT similarity_group_id FROM photos WHERE id = ?", (result_c.photo_id,)
    ).fetchone()

    assert row_a["similarity_group_id"] is not None
    assert row_b["similarity_group_id"] is not None
    assert row_a["similarity_group_id"] == row_b["similarity_group_id"]

    # "different" אין לה אף תמונה דומה בפרויקט - נשארת תמונה יחידה, ולפי
    # ההתנהגות הנוכחית של group_similar_photos (ראה docstring ב-
    # image_pipeline/api.py) תמונה יחידה לא מקבלת קבוצה ייחודית לעצמה,
    # אלא נשארת similarity_group_id=NULL.
    assert row_c["similarity_group_id"] is None

    job = conn.execute(
        "SELECT status, error_reason FROM processing_jobs WHERE project_id = ? AND stage = 'grouping'",
        (project_id,),
    ).fetchone()
    assert job is not None
    assert job["status"] == "done"
    assert job["error_reason"] is None


def test_group_similar_photos_does_not_raise_and_skips_photos_without_thumbnail(pipeline_env):
    """F01/F10: תמונה שנכשלה ב-process_upload (אין thumb_path) לא מפילה
    את group_similar_photos. שתי תמונות "תקינות" כמעט-זהות (אותו seed)
    מתקבצות יחד; הקובץ הפגום לא יצר שורת photos כלל ולכן לא יכול לקבל
    similarity_group_id - רק מאשרים שהקיבוץ לא קורס ומסתיים 'done'."""
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    ok_path_1 = tmp_path / "ok1.jpg"
    ok_path_2 = tmp_path / "ok2.jpg"
    _make_checkerboard_jpeg(ok_path_1, seed=7)
    _make_checkerboard_jpeg(ok_path_2, seed=7)
    ok_result_1 = pipeline.process_upload(
        project_id=project_id, saved_file_path=str(ok_path_1),
        original_filename="ok1.jpg", db=conn,
    )
    ok_result_2 = pipeline.process_upload(
        project_id=project_id, saved_file_path=str(ok_path_2),
        original_filename="ok2.jpg", db=conn,
    )
    assert ok_result_1.status == "done"
    assert ok_result_2.status == "done"

    bad_path = tmp_path / "bad.jpg"
    bad_path.write_bytes(b"not a real image")
    bad_result = pipeline.process_upload(
        project_id=project_id, saved_file_path=str(bad_path),
        original_filename="bad.jpg", db=conn,
    )
    assert bad_result.status == "failed"

    # לא אמור להעלות exception.
    pipeline.group_similar_photos(project_id, conn)

    ok_row_1 = conn.execute(
        "SELECT similarity_group_id FROM photos WHERE id = ?", (ok_result_1.photo_id,)
    ).fetchone()
    ok_row_2 = conn.execute(
        "SELECT similarity_group_id FROM photos WHERE id = ?", (ok_result_2.photo_id,)
    ).fetchone()
    # שתי התמונות התקינות "כמעט-זהות" (אותו seed) - מתקבצות לאותה קבוצה
    # לא-ריקה, ולא נשארות NULL (זה היה נשאר תמונה יחידה אילו הייתה רק אחת).
    assert ok_row_1["similarity_group_id"] is not None
    assert ok_row_2["similarity_group_id"] is not None
    assert ok_row_1["similarity_group_id"] == ok_row_2["similarity_group_id"]

    # הקובץ הפגום לא יצר שורת photos כלל (ראה test_corrupt_file... מעלה) -
    # ומכאן שאין לו similarity_group_id לבדוק; רק מאשרים שהקיבוץ לא קרס.
    job = conn.execute(
        "SELECT status FROM processing_jobs WHERE project_id = ? AND stage = 'grouping'",
        (project_id,),
    ).fetchone()
    assert job["status"] == "done"
