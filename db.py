"""
SmartAlbum - גישה ל-DB המשותף (architect-owned contract).

DB יחיד ל-SQLite לכל הפרויקט (לא DB נפרד למודול) - הסכמה המחייבת
מוגדרת ב-schema.sql, לא כאן. מודול זה חושף:

  - get_connection()  - חיבור sqlite3 יחיד, עם foreign_keys=ON ו-Row factory.
  - init_db()         - מריץ את schema.sql (idempotent, CREATE TABLE IF NOT EXISTS).
  - delete_project()  - הפונקציה היחידה שמוחקת פרויקט: רשומות DB + קבצים בדיסק (F10).

כל מודול אחר (image-pipeline, selection-engine, layout-engine, export-agent,
frontend-rtl) ניגש ל-DB *רק* דרך get_connection() של מודול זה - אסור לפתוח
חיבור sqlite3 ישירות במקום אחר, כדי ששינוי עתידי בנתיב/הגדרות יהיה במקום אחד.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import storage

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_DEFAULT_DB_PATH = storage.get_storage_root() / "smartalbum.db"


def get_db_path(db_path: str | os.PathLike | None = None) -> Path:
    """נתיב קובץ ה-DB בפועל. ניתן לעקוף עם משתנה סביבה SMARTALBUM_DB_PATH
    (שימושי לטסטים), או עם ארגומנט מפורש."""
    if db_path is not None:
        return Path(db_path)
    override = os.environ.get("SMARTALBUM_DB_PATH")
    if override:
        return Path(override)
    return _DEFAULT_DB_PATH


def get_connection(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """מחזיר חיבור sqlite3 חדש, מוגדר עקבי לכל המודולים:
    - foreign_keys = ON (אכיפת ON DELETE CASCADE וכו')
    - row_factory = sqlite3.Row (גישה כ-dict-like, נוח ל-JSON serialization)

    הקורא אחראי לסגור את החיבור (context manager / conn.close()).
    """
    path = get_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | os.PathLike | None = None) -> None:
    """יוצר את כל הטבלאות מ-schema.sql אם לא קיימות. בטוח להרצה חוזרת
    (idempotent) - נקרא בעליית השרת וגם מכל סויטת טסטים."""
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn = get_connection(db_path)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


def delete_project(project_id: str, db_path: str | os.PathLike | None = None) -> None:
    """מוחק פרויקט לגמרי: כל השורות הקשורות ב-DB + כל הקבצים בדיסק (F10).

    זו הפונקציה היחידה שמבצעת מחיקת פרויקט - אין לממש מחיקה ידנית
    (שורה-שורה / תיקייה-תיקייה) בשום מודול אחר.

    ON DELETE CASCADE ב-schema.sql מטפל ב-photos / spreads / spread_photos /
    processing_jobs הקשורים ל-project_id ברגע שמוחקים את שורת projects,
    אבל אנחנו מוחקים במפורש כדי שהפונקציה תהיה תקינה גם אם מישהו
    מריץ אותה מול DB ישן בלי PRAGMA foreign_keys מופעל.
    """
    conn = get_connection(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM spread_photos WHERE spread_id IN "
                      "(SELECT id FROM spreads WHERE project_id = ?)", (project_id,))
        conn.execute("DELETE FROM spreads WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM processing_jobs WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM photos WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
    finally:
        conn.close()

    # קבצים בדיסק (originals/thumbnails/exports) - נמחקים רק אחרי שה-DB
    # commit הצליח, כדי שלא נאבד קבצים אם מחיקת ה-DB נכשלת.
    storage.delete_project_files(project_id)
