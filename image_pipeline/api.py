"""
SmartAlbum - image-pipeline: F02 (תיקון כיוון EXIF + thumbnails) ו-F04
(דירוג חדות/חשיפה + קיבוץ תמונות דומות).

ממשק מחייב מוגדר ב-INTERFACES.md סעיף 4.1 - אין להמציא חתימות אחרות.

הסטאטוס הנוכחי של המודול הזה (ראה README למעלה בכל פונקציה):
  - process_upload: F02 מלא (issue #3). חלקי F04 (sharpness_score/
    exposure_score) **לא** ממומשים כאן בכוונה - משאירים NULL ומחכים ל-issue #5.
  - group_similar_photos: stub בלבד, ממתין ל-issue #5 (F04) - לא כולל
    לוגיקה מזויפת, רק raise NotImplementedError מתועד.

אחריות המודול: originals (ללא שינוי בייט), thumbnails, exif_orientation,
sharpness/exposure/similarity (F04, issue #5). לא נוגע ב-spreads/selection.
"""

from __future__ import annotations

import io
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps, UnidentifiedImageError

import storage

# תג EXIF "Orientation" (0x0112 / 274) - ראה PIL.ExifTags.Base.Orientation.
_EXIF_ORIENTATION_TAG = 0x0112

# גודל thumbnail קבוע - הצלע הגדולה ביותר, לפי F02 ("גודל קבוע, מהיר, לשימוש
# בכל ה-UI"). שומר aspect ratio.
_THUMBNAIL_MAX_SIDE = 800
_THUMBNAIL_JPEG_QUALITY = 85

# מיפוי פורמט Pillow -> סיומת קובץ, לשימוש רק כגיבוי כש-original_filename
# לא מכיל סיומה מוכרת. לא משפיע על תוכן הבייטים שנשמרים (F02: ללא שינוי).
_FORMAT_TO_EXT = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "GIF": ".gif",
    "BMP": ".bmp",
    "TIFF": ".tif",
    "WEBP": ".webp",
    "HEIF": ".heic",
}
_KNOWN_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif"}


@dataclass
class PhotoUploadResult:
    photo_id: str | None        # None אם הקובץ נדחה
    status: Literal["done", "failed"]
    error_reason: str | None    # F01


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pick_ext(original_filename: str, pil_format: str | None) -> str:
    suffix = Path(original_filename).suffix.lower()
    if suffix in _KNOWN_EXTS:
        return suffix
    return _FORMAT_TO_EXT.get(pil_format or "", ".jpg")


def _set_job_status(
    db: sqlite3.Connection,
    project_id: str,
    stage: str,
    status: Literal["pending", "running", "done", "failed"],
    error_reason: str | None = None,
) -> None:
    """Upsert שורת processing_jobs עבור (project_id, stage) - שורה אחת
    per stage per project (F01, F10), כך שניסיון חוזר מעדכן ולא מצרף."""
    db.execute(
        """
        INSERT INTO processing_jobs (id, project_id, stage, status, error_reason, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, stage) DO UPDATE SET
            status = excluded.status,
            error_reason = excluded.error_reason,
            updated_at = excluded.updated_at
        """,
        (str(uuid.uuid4()), project_id, stage, status, error_reason, _now_iso()),
    )
    db.commit()


def _find_existing_photo(
    db: sqlite3.Connection, project_id: str, original_filename: str
) -> sqlite3.Row | None:
    cur = db.execute(
        "SELECT id, thumb_path FROM photos WHERE project_id = ? AND original_filename = ?",
        (project_id, original_filename),
    )
    return cur.fetchone()


def process_upload(
    project_id: str,
    saved_file_path: str,
    original_filename: str,
    db: sqlite3.Connection,
) -> PhotoUploadResult:
    """F01/F02: יוצר שורת photos, מעביר את הקובץ ל-storage.original_path
    (ללא שינוי בייט), מתקן כיוון ויוצר thumbnail.

    F04 (sharpness_score/exposure_score/similarity_group_id) - **לא** מחושב
    כאן בכוונה; נשאר NULL עד issue #5 (ראה group_similar_photos למטה).

    כשל בקובץ בודד לא מעלה exception שעוצר קבצים אחרים - נלכד פנימית
    ומוחזר PhotoUploadResult(status="failed").
    """
    # --- ניסיון-חוזר / דה-דופ: תמונה שכבר עובדה בהצלחה לא מעובדת פעמיים ---
    existing = _find_existing_photo(db, project_id, original_filename)
    if existing is not None and existing["thumb_path"]:
        return PhotoUploadResult(photo_id=existing["id"], status="done", error_reason=None)

    photo_id = existing["id"] if existing is not None else str(uuid.uuid4())

    # --- שלב 1: קריאת הקובץ הגולמי + ולידציה + הפקת thumbnail בזיכרון. ---
    # לא נוגעים בדיסק/DB עד שהכל הצליח, כדי שכשל לא ישאיר שורות/קבצים חצי-כתובים.
    try:
        if not os.path.isfile(saved_file_path):
            raise FileNotFoundError(f"קובץ שהועלה לא נמצא בנתיב הזמני: {saved_file_path}")

        raw_bytes = Path(saved_file_path).read_bytes()
        if not raw_bytes:
            raise ValueError("הקובץ שהועלה ריק (0 בייטים)")

        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.load()  # מפענח את כל הקובץ - תופס קבצים פגומים/לא נתמכים
            pil_format = img.format

            exif = img.getexif()
            exif_orientation = exif.get(_EXIF_ORIENTATION_TAG)

            # תיקון כיוון - רק על ה-thumbnail (F02: המקור נשמר ללא שינוי).
            oriented = ImageOps.exif_transpose(img)
            if oriented.mode not in ("RGB", "L"):
                oriented = oriented.convert("RGB")
            oriented.thumbnail(
                (_THUMBNAIL_MAX_SIDE, _THUMBNAIL_MAX_SIDE), Image.Resampling.LANCZOS
            )
            thumb_width, thumb_height = oriented.size

            thumb_buffer = io.BytesIO()
            oriented.save(thumb_buffer, format="JPEG", quality=_THUMBNAIL_JPEG_QUALITY)
            thumb_bytes = thumb_buffer.getvalue()
    except (UnidentifiedImageError, FileNotFoundError, OSError, ValueError) as exc:
        reason = f"קובץ תמונה פגום או לא נתמך: {exc}"
        _set_job_status(db, project_id, "exif", "failed", reason)
        return PhotoUploadResult(photo_id=None, status="failed", error_reason=reason)

    # --- שלב 2: הכל תקין - כותבים לדיסק (originals/thumbnails) ול-DB. ---
    ext = _pick_ext(original_filename, pil_format)
    original_path = storage.original_path(project_id, photo_id, ext)
    thumb_path = storage.thumbnail_path(project_id, photo_id)

    try:
        # bytes-exact copy, לעולם לא נפתח/נשמר מחדש ע"י Pillow (F02).
        original_path.write_bytes(raw_bytes)
        thumb_path.write_bytes(thumb_bytes)
    except OSError as exc:
        reason = f"כשל בכתיבת קבצים לדיסק: {exc}"
        _set_job_status(db, project_id, "thumbnail", "failed", reason)
        return PhotoUploadResult(photo_id=None, status="failed", error_reason=reason)

    now = _now_iso()
    db.execute(
        """
        INSERT INTO photos (
            id, project_id, original_filename, original_path, file_size_bytes,
            uploaded_at, thumb_path, exif_orientation, width, height, error_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(id) DO UPDATE SET
            original_filename = excluded.original_filename,
            original_path = excluded.original_path,
            file_size_bytes = excluded.file_size_bytes,
            thumb_path = excluded.thumb_path,
            exif_orientation = excluded.exif_orientation,
            width = excluded.width,
            height = excluded.height,
            error_reason = NULL
        """,
        (
            photo_id,
            project_id,
            original_filename,
            str(original_path),
            len(raw_bytes),
            now,
            str(thumb_path),
            exif_orientation,
            thumb_width,
            thumb_height,
        ),
    )
    db.commit()

    _set_job_status(db, project_id, "exif", "done")
    _set_job_status(db, project_id, "thumbnail", "done")

    return PhotoUploadResult(photo_id=photo_id, status="done", error_reason=None)


def group_similar_photos(project_id: str, db: sqlite3.Connection) -> None:
    """F04: מחשב similarity_group_id (perceptual hash, imagehash) וכותב
    ל-photos. לא מוחק/פוסל תמונות - selection-engine מחליט על סמך זה (F06).

    TODO(issue #5): עדיין לא ממומש - ה-issue הנוכחי (#3) מכסה רק F02
    (EXIF orientation + thumbnails). בכוונה לא נכתבה כאן לוגיקה חלקית/מזויפת
    (למשל "קיבוץ" שרירותי) - כך ש-selection-engine לא יקבל similarity_group_id
    שגוי. selection-engine צריך להתייחס ל-similarity_group_id=NULL כ"לא חושב
    עדיין", לא כ"אין תמונות דומות".
    """
    raise NotImplementedError(
        "group_similar_photos (F04) ממתין ל-issue #5 - לא ממומש ב-issue #3."
    )
