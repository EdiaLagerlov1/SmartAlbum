"""
SmartAlbum - מבנה האחסון בדיסק (architect-owned contract).

כל מודול שצריך לקרוא/לכתוב קבצים (image-pipeline, export-agent, frontend-rtl
כשהוא מגיש קבצים) חייב לעבור דרך הפונקציות כאן ולא לבנות נתיבים "ביד" -
כך שינוי מבנה תיקיות בעתיד לא שובר קוד בכמה מקומות.

מבנה:

storage/
  smartalbum.db                    # ה-DB המשותף (ברירת מחדל, ראה db.py)
  projects/<project_id>/
    originals/<photo_id>.<ext>     # F02: לעולם לא נכתב/נערך אחרי ההעלאה
    thumbnails/<photo_id>.jpg      # הפקה חוזרת אפשרית, לא "מקור"
    exports/
      album.pdf                    # F09
      album_structure.json         # F09: מבנה לשחזור
      album_structure_prev.json    # גיבוי לפני "יצירה מחדש" (F08) - ראה INTERFACES.md
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# שורש האחסון - יחסי לשורש הריפו כברירת מחדל. ניתן לעקוף עם משתנה סביבה
# SMARTALBUM_STORAGE_ROOT (שימושי לטסטים / פריסה).
_DEFAULT_STORAGE_ROOT = Path(__file__).resolve().parent / "storage"


def get_storage_root() -> Path:
    """מחזיר את תיקיית השורש של האחסון (storage/)."""
    override = os.environ.get("SMARTALBUM_STORAGE_ROOT")
    root = Path(override) if override else _DEFAULT_STORAGE_ROOT
    return root


def project_dir(project_id: str) -> Path:
    """storage/projects/<project_id>/"""
    return get_storage_root() / "projects" / project_id


def originals_dir(project_id: str, create: bool = True) -> Path:
    path = project_dir(project_id) / "originals"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def thumbnails_dir(project_id: str, create: bool = True) -> Path:
    path = project_dir(project_id) / "thumbnails"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def exports_dir(project_id: str, create: bool = True) -> Path:
    path = project_dir(project_id) / "exports"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_project_dirs(project_id: str) -> None:
    """נקרא בעת יצירת פרויקט (F01) - מכין את שלושת תתי-התיקיות."""
    originals_dir(project_id)
    thumbnails_dir(project_id)
    exports_dir(project_id)


def original_path(project_id: str, photo_id: str, ext: str) -> Path:
    """נתיב קובץ המקור. ext כולל נקודה, למשל '.jpg'. לא נכתב מעבר ליצירה הראשונית (F02)."""
    return originals_dir(project_id) / f"{photo_id}{ext}"


def thumbnail_path(project_id: str, photo_id: str, ext: str = ".jpg") -> Path:
    return thumbnails_dir(project_id) / f"{photo_id}{ext}"


def export_pdf_path(project_id: str) -> Path:
    return exports_dir(project_id) / "album.pdf"


def export_structure_path(project_id: str) -> Path:
    return exports_dir(project_id) / "album_structure.json"


def export_structure_prev_path(project_id: str) -> Path:
    """גיבוי מבנה האלבום הקודם, נכתב ע"י frontend-rtl/layout-engine לפני
    'יצירה מחדש' (F08) שמוחקת שינויים ידניים - ראה INTERFACES.md סעיף F08."""
    return exports_dir(project_id) / "album_structure_prev.json"


def delete_project_files(project_id: str) -> None:
    """מוחק את כל עץ הקבצים של הפרויקט (originals/thumbnails/exports).

    נקרא תמיד מתוך db.delete_project() - אין לקרוא ישירות ממודול אחר
    בלי למחוק גם את רשומות ה-DB (F10: מחיקת פרויקט מוחקת מקורות+תוצרים).
    לא נכשל אם התיקייה לא קיימת.
    """
    path = project_dir(project_id)
    if path.exists():
        shutil.rmtree(path)
