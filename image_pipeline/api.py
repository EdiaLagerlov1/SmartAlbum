"""
SmartAlbum - image-pipeline: F02 (תיקון כיוון EXIF + thumbnails) ו-F04
(דירוג חדות/חשיפה + קיבוץ תמונות דומות).

ממשק מחייב מוגדר ב-INTERFACES.md סעיף 4.1 - אין להמציא חתימות אחרות.

הסטאטוס הנוכחי של המודול הזה (ראה README למעלה בכל פונקציה):
  - process_upload: F02 מלא (issue #3) + F04 (issue #5) - מחשב גם
    sharpness_score/exposure_score על ה-thumbnail המתוקן, בתוך אותו try
    שבו הקובץ נפתח/מעובד.
  - group_similar_photos: ממומש (issue #5) - מחשב perceptual hash לכל
    תמונה שעברה עיבוד (יש לה thumb_path) ומקבץ תמונות קרובות (Hamming
    distance קטן מסף) לאותו similarity_group_id. ראה הערת imagehash
    fallback למטה.

אחריות המודול: originals (ללא שינוי בייט), thumbnails, exif_orientation,
sharpness/exposure/similarity (F04, issue #5). לא נוגע ב-spreads/selection.
**F04 היא המלצה בלבד**: אין כאן ואין להוסיף כאן שום לוגיקת מחיקה/פסילה
של תמונה על סמך ציון נמוך, וגם לא זיהוי פנים/זהות או אימון מודל - אסור
במפורש באפיון.

הערת imagehash (fallback): `requirements.txt`/`INTERFACES.md` מתעדים
תלות ב-`imagehash` (perceptual hash), אבל בסביבת הפיתוח/CI הנוכחית של
הפרויקט הזה PyPI חסום (בדיוק כמו ההערה הקיימת ב-INTERFACES.md על
FastAPI/httpx) ו-`imagehash` **לא מותקן בפועל** (`import imagehash`
נכשל עם ModuleNotFoundError). לכן המודול הזה מנסה לייבא `imagehash`
ברכה (best-effort) ונופל בחזרה למימוש "average hash" עצמאי עם
Pillow+numpy בלבד (ראה `_average_hash` / `_AverageHash` למטה) שתואם
ממשק-חישוב (אותו `__sub__`/Hamming distance). TODO: אם `imagehash`
יותקן בעתיד (סביבת production עם גישת רשת), אפשר/רצוי להסיר את ה-
fallback ולהשתמש תמיד ב-`imagehash.phash` (מדויק יותר מ-average hash).
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

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

import storage

try:
    import imagehash  # type: ignore

    _HAS_IMAGEHASH = True
except ImportError:  # pragma: no cover - תלוי בסביבת ההרצה, ראה הערה למעלה
    imagehash = None  # type: ignore
    _HAS_IMAGEHASH = False

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

# --- F04: קיבוץ תמונות דומות -------------------------------------------
# גודל ה-hash הפרצפטואלי כש-imagehash לא מותקן (average hash, 8x8 -> 64 ביט,
# תואם בגודלו ל-imagehash.phash הסטנדרטי כדי שסף ה-Hamming distance יהיה בסדר גודל דומה).
_HASH_SIZE = 8

# סף Hamming distance לקיבוץ שתי תמונות לאותה similarity_group_id. נבחר
# אמפירית: hash של 64 ביט (8x8), 2 תמונות "כמעט זהות" (אותה תמונה אחרי
# דחיסת JPEG שונה/שינוי בהירות קל) נבדלות בכמה ביטים בודדים; תמונות שונות
# באופן מובחן נבדלות בעשרות ביטים. 10/64 (~15.6%) הוא סף נפוץ בספרות
# ל-perceptual hashing (both average-hash ו-phash) - מספיק מחמיר כדי לא
# לקבץ תמונות שונות, מספיק סלחני לדחיסה/שינויי בהירות קלים.
_SIMILARITY_HAMMING_THRESHOLD = 10


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


def _compute_sharpness_score(gray: np.ndarray) -> float:
    """F04: ציון חדות (המלצה בלבד - לעולם לא עילה למחיקה אוטומטית, F04).

    שיטה: שונות (variance) של אופרטור Laplacian - קירוב דיסקרטי לנגזרת
    השנייה, רגיש לקצוות/פרטים חדים. ציון גבוה = יותר "אנרגיית קצוות" =
    תמונה חדה יותר ככל הנראה; ציון נמוך (קרוב ל-0) = תמונה מטושטשת/שטוחה.

    ממומש עם numpy בלבד (קונבולוציה 3x3 ידנית, לא `cv2.Laplacian` ולא
    `scipy.ndimage` - ראה INTERFACES.md סעיף 1: הסטאק שנבחר ל-F04 הוא
    "Laplacian-variance/numpy", לא cv2/scipy): kernel [[0,1,0],[1,-4,1],
    [0,1,0]] מיושם כסכום של 4 views מוזזים פחות 4 פעמים המרכז (שקול
    מתמטית לקונבולוציה 'valid', בלי padding בקצוות).
    """
    if gray.size == 0:
        return 0.0
    h, w = gray.shape
    if h < 3 or w < 3:
        return 0.0
    center = gray[1:-1, 1:-1]
    up = gray[0:-2, 1:-1]
    down = gray[2:, 1:-1]
    left = gray[1:-1, 0:-2]
    right = gray[1:-1, 2:]
    laplacian = up + down + left + right - 4.0 * center
    return float(np.var(laplacian))


def _compute_exposure_score(gray: np.ndarray) -> float:
    """F04: ציון חשיפה בטווח 0..1 (המלצה בלבד - F04, לא עילה למחיקה).

    מדד: 1 - (ממוצע |ערך-פיקסל - 128| / 128), מחושב על ה-grayscale.
    128 הוא "אמצע" סקאלת בהירות 0..255 - תמונה עם היסטוגרמת בהירות
    שמרוכזת סביב 128 (חשיפה מאוזנת) מקבלת ציון קרוב ל-1; תמונה שההיסטוגרמה
    שלה "דחוסה" לקיצוות (קרוב ל-0 = underexposed, קרוב ל-255 =
    overexposed) מקבלת ציון קרוב ל-0. זה heuristic פשוט (לא מודל מאומן -
    אסור באפיון), לא קובע exposure "נכון" אובייקטיבית (תמונות אמנותיות
    high/low-key עלולות לקבל ציון נמוך בצדק-לא-מוחלט) - בדיוק בגלל זה
    זו המלצה, לא קריטריון פסילה.
    """
    if gray.size == 0:
        return 0.0
    mean_abs_dev = float(np.mean(np.abs(gray - 128.0)))
    normalized = min(mean_abs_dev / 128.0, 1.0)
    return 1.0 - normalized


class _AverageHash:
    """Fallback ל-`imagehash.ImageHash` כש-`imagehash` לא מותקן (ראה הערת
    ה-docstring העליונה של המודול). "Average hash": מקטינים ל-8x8
    grayscale, משווים כל פיקסל לממוצע (1 אם מעל הממוצע, 0 אחרת) -> 64 ביט.
    תומך ב-operator `-` (Hamming distance) כמו `imagehash.ImageHash`, כדי
    ש-`group_similar_photos` יתייחס לשני המימושים (imagehash / fallback)
    באופן אחיד בלי `if`-ים נפרדים בלוגיקת הקיבוץ.
    """

    __slots__ = ("bits",)

    def __init__(self, bits: np.ndarray) -> None:
        self.bits = bits  # 1D בוליאני, אורך _HASH_SIZE*_HASH_SIZE

    def __sub__(self, other: "_AverageHash") -> int:
        return int(np.count_nonzero(self.bits != other.bits))


def _compute_photo_hash(img: Image.Image):
    """F04: perceptual hash של תמונה - `imagehash.phash` אם מותקן, אחרת
    `_AverageHash` (ראה docstring המודול - imagehash לא מותקן בסביבה זו).
    שני המימושים תומכים ב-`a - b` -> Hamming distance (int)."""
    if _HAS_IMAGEHASH:
        return imagehash.phash(img)
    small = img.convert("L").resize((_HASH_SIZE, _HASH_SIZE), Image.Resampling.LANCZOS)
    arr = np.asarray(small, dtype=np.float64)
    avg = float(arr.mean())
    bits = (arr > avg).flatten()
    return _AverageHash(bits)


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
    """F01/F02/F04: יוצר שורת photos, מעביר את הקובץ ל-storage.original_path
    (ללא שינוי בייט), מתקן כיוון ויוצר thumbnail, ומחשב sharpness_score/
    exposure_score על ה-thumbnail המתוקן (F04 - המלצה בלבד, ראה
    `_compute_sharpness_score`/`_compute_exposure_score`).

    similarity_group_id **לא** מחושב כאן - נכתב ע"י `group_similar_photos`
    שרצה פעם אחת על כל תמונות הפרויקט (צריך את כולן כדי לקבץ), לא per-file.

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

            # F04: sharpness/exposure מחושבים על ה-thumbnail המתוקן (אותה
            # תמונה שה-UI מציג) - מהיר, ולא דורש פתיחה נוספת של המקור.
            gray = oriented.convert("L") if oriented.mode != "L" else oriented
            gray_arr = np.asarray(gray, dtype=np.float64)
            sharpness_score = _compute_sharpness_score(gray_arr)
            exposure_score = _compute_exposure_score(gray_arr)

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
            uploaded_at, thumb_path, exif_orientation, width, height,
            sharpness_score, exposure_score, error_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(id) DO UPDATE SET
            original_filename = excluded.original_filename,
            original_path = excluded.original_path,
            file_size_bytes = excluded.file_size_bytes,
            thumb_path = excluded.thumb_path,
            exif_orientation = excluded.exif_orientation,
            width = excluded.width,
            height = excluded.height,
            sharpness_score = excluded.sharpness_score,
            exposure_score = excluded.exposure_score,
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
            sharpness_score,
            exposure_score,
        ),
    )
    db.commit()

    _set_job_status(db, project_id, "exif", "done")
    _set_job_status(db, project_id, "thumbnail", "done")

    return PhotoUploadResult(photo_id=photo_id, status="done", error_reason=None)


def group_similar_photos(project_id: str, db: sqlite3.Connection) -> None:
    """F04: מחשב similarity_group_id (perceptual hash - `imagehash.phash`
    אם מותקן, אחרת fallback ל-average hash עצמאי, ראה docstring המודול)
    וכותב ל-photos. **לא מוחק/פוסל תמונות** - selection-engine מחליט על
    סמך זה בלבד (F06, מצמצם כפילויות).

    אלגוריתם: עוברים על כל תמונות הפרויקט שעברו עיבוד (יש להן thumb_path),
    מחשבים hash per thumbnail, ואז clustering עם union-find פשוט (מספר
    התמונות בפרויקט חתונה בודד קטן יחסית - עשרות/מאות, לא מיליונים -
    השוואת כל הזוגות O(n^2) מספיק מהיר ופשוט בלי תלות נוספת כמו
    scipy.cluster): כל זוג עם Hamming distance <= `_SIMILARITY_HAMMING_THRESHOLD`
    מאוחד לאותו component (union-find מבטיח שרשרת a~b~c תתקבץ כולה יחד,
    גם אם a~c בעצמם מעל הסף), ולכל component בגודל >= 2 מוקצה
    `similarity_group_id` (uuid) משותף. **תמונה יחידה
    (בלי אף תמונה דומה) נשארת עם similarity_group_id=NULL** (לא מקבלת
    קבוצה ייחודית לעצמה) - כך ש-selection-engine יכול להתייחס ל-"יש
    similarity_group_id" כ"יש כפילויות פוטנציאליות לצמצם" (F06) בלי
    לסנן NULL-ים בנפרד. NULL גם ממשיך לשמש למקרה "לא ניתן לחשב" (למשל
    thumbnail חסר/פגום) - עקבי עם החוזה ב-INTERFACES.md סעיף 4.1.

    לא מעלה exception על כשל בתמונה בודדת (thumbnail לא נפתח) - מדלגת
    עליה (נשארת similarity_group_id=NULL) וממשיכה עם השאר, בהתאם ל-F01/F10
    ("כשל בקובץ בודד לא עוצר את יתר העיבוד").
    """
    _set_job_status(db, project_id, "grouping", "running")
    try:
        rows = db.execute(
            "SELECT id, thumb_path FROM photos WHERE project_id = ? AND thumb_path IS NOT NULL",
            (project_id,),
        ).fetchall()

        hashes: dict[str, object] = {}
        for row in rows:
            try:
                with Image.open(row["thumb_path"]) as img:
                    hashes[row["id"]] = _compute_photo_hash(img)
            except (UnidentifiedImageError, FileNotFoundError, OSError):
                # thumbnail חסר/פגום - לא עוצר את קיבוץ שאר התמונות (F01/F10).
                continue

        # --- union-find (disjoint set) נאיבי, כדי ששרשרת a~b~c תתקבץ ---
        # כולה גם אם a~c בעצמם מעל הסף (ייצוג מדויק יותר מ"זיווג זוגות" פשוט).
        photo_ids = list(hashes.keys())
        parent: dict[str, str] = {pid: pid for pid in photo_ids}

        def _find(pid: str) -> str:
            root = pid
            while parent[root] != root:
                root = parent[root]
            parent[pid] = root  # path compression
            return root

        def _union(a: str, b: str) -> None:
            root_a, root_b = _find(a), _find(b)
            if root_a != root_b:
                parent[root_a] = root_b

        for i in range(len(photo_ids)):
            id_i = photo_ids[i]
            for j in range(i + 1, len(photo_ids)):
                id_j = photo_ids[j]
                if (hashes[id_i] - hashes[id_j]) <= _SIMILARITY_HAMMING_THRESHOLD:
                    _union(id_i, id_j)

        components: dict[str, list[str]] = {}
        for pid in photo_ids:
            components.setdefault(_find(pid), []).append(pid)

        for members in components.values():
            if len(members) < 2:
                continue  # תמונה יחידה - נשארת similarity_group_id=NULL (ראה docstring)
            group_id = str(uuid.uuid4())
            for member_id in members:
                db.execute(
                    "UPDATE photos SET similarity_group_id = ? WHERE id = ?",
                    (group_id, member_id),
                )
        db.commit()
        _set_job_status(db, project_id, "grouping", "done")
    except Exception as exc:
        _set_job_status(db, project_id, "grouping", "failed", str(exc))
        raise
