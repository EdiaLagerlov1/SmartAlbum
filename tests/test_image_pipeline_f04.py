"""
בדיקות יחידה ל-image_pipeline F04 (issue #5): דירוג חדות/חשיפה וקיבוץ
תמונות דומות.

מכסה:
  (א) שני קבצים דומים מאוד (אותה תמונה פיקסל-לפיקסל, ותמונה + גרסת JPEG
      עם quality שונה במקצת) מקבלים את אותו similarity_group_id.
  (ב) שתי תמונות שונות מאוד (תבניות/צבעים שונים לגמרי) לא מקבלות אותו
      similarity_group_id (ונשארות כל אחת ללא קבוצה - NULL, תמונה יחידה).
  (ג) sharpness_score של תמונה מטושטשת (Gaussian blur) נמוך משל התמונה
      המקורית החדה.
  (ד) exposure_score מגיב בכיוון סביר: תמונה אחידה מאוד בהירה/כהה מקבלת
      ציון נמוך יותר מתמונה "מאוזנת" (אפורה, בהירות ~128).

גם כאן: F04 היא המלצה בלבד - אין בדיקה (ואין קוד) שמוחקת/פוסלת תמונה
על סמך ציון נמוך.

משתמש בתמונות שנוצרות פרוגרמטית עם Pillow בזמן ריצת הטסט (אין קבצי
תמונה מוכנים בריפו) - ראה שימוש ב-SMARTALBUM_STORAGE_ROOT / SMARTALBUM_DB_PATH
(env overrides) כדי לא לכתוב ל-storage/ האמיתי, בהתאם לדוגמת
tests/test_image_pipeline.py הקיים.
"""

from __future__ import annotations

import io
import os
import random
import sys
import uuid

import pytest
from PIL import Image, ImageFilter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _make_checkerboard_jpeg_bytes(size=(64, 64), quality=90) -> bytes:
    """תבנית "שחמט" דקה (בלוקים של 4 פיקסלים) - הרבה אנרגיית קצוות, שימושי
    ל-sharpness (Laplacian variance). **לא** משמש ל-similarity: תבנית
    כה "high-frequency" רגישה מאוד לארטיפקטים של דחיסת JPEG (בלוקי DCT
    8x8 מתנגשים עם בלוקי 4px) - שתי עותקים זהים ב-quality שונה יכולים
    לקבל perceptual hash שונה מהותית. ל-similarity משתמשים ב-`_make_mosaic_jpeg_bytes`
    (בלוקי צבע גדולים יותר, "דמוי-תצלום") למטה.
    """
    img = Image.new("RGB", size, color="white")
    w, h = size
    block = 4
    for y in range(h):
        for x in range(w):
            if ((x // block) + (y // block)) % 2 == 0:
                img.putpixel((x, y), (20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _make_mosaic_jpeg_bytes(seed: int, size=(64, 64), cell=16, quality=90) -> bytes:
    """"תמונה" פרוגרמטית דמוית-תצלום: גריד של בלוקים צבעוניים (16x16
    פיקסלים, גדולים בהרבה מבלוק ה-DCT של JPEG, 8x8) עם צבעים דטרמיניסטיים
    לפי `seed`. אותו seed = "אותה תמונה" (מקבלת perceptual hash קרוב גם
    אחרי שינוי JPEG quality); seed שונה = תמונה שונה מהותית (hash רחוק).
    """
    rnd = random.Random(seed)
    img = Image.new("RGB", size)
    w, h = size
    for by in range(0, h, cell):
        for bx in range(0, w, cell):
            color = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
            for y in range(by, min(by + cell, h)):
                for x in range(bx, min(bx + cell, w)):
                    img.putpixel((x, y), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _make_solid_jpeg_bytes(size=(64, 64), gray_value=128) -> bytes:
    img = Image.new("RGB", size, color=(gray_value, gray_value, gray_value))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture()
def pipeline_env(tmp_path, monkeypatch):
    """מגדיר storage root + DB זמניים, מאתחל סכמה, ומייבא מודולים
    (db/storage/image_pipeline) אחרי שהדגלים הסביבתיים מוגדרים - בהתאם
    ל-fixture המקביל ב-tests/test_image_pipeline.py."""
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
        (project_id, "טסט F04", "2026-01-01T00:00:00+00:00"),
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


def _upload(pipeline, conn, project_id, tmp_path, filename, jpeg_bytes):
    upload_path = tmp_path / filename
    upload_path.write_bytes(jpeg_bytes)
    result = pipeline.process_upload(
        project_id=project_id,
        saved_file_path=str(upload_path),
        original_filename=filename,
        db=conn,
    )
    assert result.status == "done", result.error_reason
    return result.photo_id


def _similarity_group_id(conn, photo_id):
    row = conn.execute(
        "SELECT similarity_group_id FROM photos WHERE id = ?", (photo_id,)
    ).fetchone()
    return row["similarity_group_id"]


# --- (א)/(ב) קיבוץ תמונות דומות -------------------------------------------


def test_identical_images_get_same_similarity_group(pipeline_env):
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    mosaic = _make_mosaic_jpeg_bytes(seed=42, quality=90)
    photo_id_1 = _upload(pipeline, conn, project_id, tmp_path, "a1.jpg", mosaic)
    # "כפילות" - אותה תמונה (אותו seed) אבל נשמרת ב-JPEG quality שונה
    # (מדמה "אותה תמונה, קובץ אחר") - שם קובץ שונה כדי לא להיתפס ע"י דה-דופ
    # לפי original_filename ב-process_upload עצמו.
    mosaic_requantized = _make_mosaic_jpeg_bytes(seed=42, quality=60)
    photo_id_2 = _upload(pipeline, conn, project_id, tmp_path, "a2.jpg", mosaic_requantized)

    pipeline.group_similar_photos(project_id, conn)

    group_1 = _similarity_group_id(conn, photo_id_1)
    group_2 = _similarity_group_id(conn, photo_id_2)
    assert group_1 is not None
    assert group_2 is not None
    assert group_1 == group_2


def test_very_different_images_do_not_share_similarity_group(pipeline_env):
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    photo_id_1 = _upload(
        pipeline, conn, project_id, tmp_path, "mosaic_a.jpg", _make_mosaic_jpeg_bytes(seed=42)
    )
    photo_id_2 = _upload(
        pipeline, conn, project_id, tmp_path, "mosaic_b.jpg", _make_mosaic_jpeg_bytes(seed=99)
    )

    pipeline.group_similar_photos(project_id, conn)

    group_1 = _similarity_group_id(conn, photo_id_1)
    group_2 = _similarity_group_id(conn, photo_id_2)
    # כל אחת "יחידה" (אין תמונה דומה לה בפרויקט) - נשארת NULL (ראה docstring
    # group_similar_photos: תמונה יחידה לא מקבלת קבוצה ייחודית לעצמה).
    assert group_1 is None
    assert group_2 is None


def test_duplicate_does_not_falsely_group_with_unrelated_photo(pipeline_env):
    """שלוש תמונות: שתי "כפילויות" (אותו seed, jpeg quality שונה) ותמונה
    שלישית שונה לגמרי (seed אחר) - הכפילויות מתקבצות יחד, השלישית לא."""
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    dup_1 = _upload(
        pipeline, conn, project_id, tmp_path, "dup1.jpg", _make_mosaic_jpeg_bytes(seed=7, quality=95)
    )
    dup_2 = _upload(
        pipeline, conn, project_id, tmp_path, "dup2.jpg", _make_mosaic_jpeg_bytes(seed=7, quality=60)
    )
    other = _upload(
        pipeline, conn, project_id, tmp_path, "other.jpg", _make_mosaic_jpeg_bytes(seed=123)
    )

    pipeline.group_similar_photos(project_id, conn)

    group_dup_1 = _similarity_group_id(conn, dup_1)
    group_dup_2 = _similarity_group_id(conn, dup_2)
    group_other = _similarity_group_id(conn, other)

    assert group_dup_1 is not None and group_dup_1 == group_dup_2
    assert group_other is None


# --- (ג) sharpness_score -----------------------------------------------


def test_blurred_image_has_lower_sharpness_than_sharp_image(pipeline_env):
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    sharp_img = Image.open(io.BytesIO(_make_checkerboard_jpeg_bytes(size=(80, 80))))
    sharp_buf = io.BytesIO()
    sharp_img.save(sharp_buf, format="JPEG", quality=95)

    blurred_img = sharp_img.filter(ImageFilter.GaussianBlur(radius=4))
    blurred_buf = io.BytesIO()
    blurred_img.save(blurred_buf, format="JPEG", quality=95)

    sharp_id = _upload(pipeline, conn, project_id, tmp_path, "sharp.jpg", sharp_buf.getvalue())
    blurred_id = _upload(pipeline, conn, project_id, tmp_path, "blurred.jpg", blurred_buf.getvalue())

    sharp_row = conn.execute(
        "SELECT sharpness_score FROM photos WHERE id = ?", (sharp_id,)
    ).fetchone()
    blurred_row = conn.execute(
        "SELECT sharpness_score FROM photos WHERE id = ?", (blurred_id,)
    ).fetchone()

    assert sharp_row["sharpness_score"] is not None
    assert blurred_row["sharpness_score"] is not None
    assert sharp_row["sharpness_score"] > blurred_row["sharpness_score"]


# --- (ד) exposure_score --------------------------------------------------


def test_exposure_score_reacts_to_overexposed_and_underexposed_images(pipeline_env):
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    balanced_id = _upload(
        pipeline, conn, project_id, tmp_path, "balanced.jpg", _make_solid_jpeg_bytes(gray_value=128)
    )
    bright_id = _upload(
        pipeline, conn, project_id, tmp_path, "bright.jpg", _make_solid_jpeg_bytes(gray_value=250)
    )
    dark_id = _upload(
        pipeline, conn, project_id, tmp_path, "dark.jpg", _make_solid_jpeg_bytes(gray_value=5)
    )

    def _exposure(photo_id):
        row = conn.execute(
            "SELECT exposure_score FROM photos WHERE id = ?", (photo_id,)
        ).fetchone()
        return row["exposure_score"]

    balanced_score = _exposure(balanced_id)
    bright_score = _exposure(bright_id)
    dark_score = _exposure(dark_id)

    assert balanced_score is not None and bright_score is not None and dark_score is not None
    # "מאוזנת" (אפור 128) צריכה לקבל ציון גבוה יותר מאוד-בהיר ומאוד-כהה.
    assert balanced_score > bright_score
    assert balanced_score > dark_score


# --- F04 כ"המלצה בלבד" - אין מחיקה/פסילה -------------------------------


def test_low_scores_do_not_remove_or_exclude_photo(pipeline_env):
    """F04 מפורש: תמונה לעולם לא נמחקת/נפסלת בגלל ציון נמוך - גם תמונה
    מאוד מטושטשת/מאוד overexposed נשארת שורה מלאה ב-photos עם flag='none'
    (ברירת המחדל), לא מסומנת exclude אוטומטית."""
    pipeline = pipeline_env["pipeline"]
    conn = pipeline_env["conn"]
    project_id = pipeline_env["project_id"]
    tmp_path = pipeline_env["tmp_path"]

    very_bright_id = _upload(
        pipeline, conn, project_id, tmp_path, "overexposed.jpg", _make_solid_jpeg_bytes(gray_value=255)
    )

    row = conn.execute(
        "SELECT flag, selected_for_album, error_reason FROM photos WHERE id = ?",
        (very_bright_id,),
    ).fetchone()
    assert row is not None
    assert row["flag"] == "none"
    assert row["selected_for_album"] == 0  # עדיין לא נבחרה - אבל לא כי נפסלה, selection-engine עוד לא רץ
    assert row["error_reason"] is None
