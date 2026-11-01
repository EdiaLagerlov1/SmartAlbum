"""
SmartAlbum - export-agent: תת-קבוצה מכוונת, רק `snapshot_structure` (F08,
issue #9), **לא** `export_album` (F09, issue #10 - עדיין חסום/לא ממומש).

ממשק מחייב מוגדר ב-INTERFACES.md סעיף 4.4 - אין להמציא חתימות אחרות.

היקף המודול כאן (issue #9 / F08 בלבד):
  - `snapshot_structure` - F08: כותבת את מצב ה-`spreads`/`spread_photos`
    הנוכחי של הפרויקט ל-JSON ב-`storage.export_structure_prev_path`, *לפני*
    שה-caller (frontend-rtl) קורא ל-`layout_engine.regenerate_layout` (ראה
    INTERFACES.md סעיף 6, זרימת F08 "יצירה מחדש" - שלבים 1-2). זה "גיבוי
    לפני שינוי הרסני" - לא snapshot היסטורי מרובה-גרסאות (נדרס בכל קריאה,
    בדיוק כמו ש-`album_structure_prev.json` מתואר ב-storage.py/INTERFACES.md
    כ"הגיבוי" היחיד, לא רשימת-גיבויים).

**לא ממומש כאן בכוונה, מחוץ להיקף issue #9**: `export_album`/`ExportResult`
(F09, ייצוא PDF עם WeasyPrint + כתיבת `album_structure.json`) - תלוי
ב-WeasyPrint שאינו מותקן בסביבה הזו (PyPI חסום, ראה INTERFACES.md סעיף 1)
ו-issue #10 חסום/לא ממומש עדיין. `snapshot_structure` **אינה תלויה
ב-WeasyPrint/רינדור HTML בכלל** - היא dump JSON טהור של מצב ה-DB, ולכן
מומשה כבר כאן כתלות-קדימה הכרחית של F08 (אין דרך לממש regenerate_layout's
flow המחייב בלי שהיא קיימת). אם תרצו לקרוא ל-`export_agent.export_album` -
הפונקציה הזו *לא קיימת* במודול הזה בהיקף הנוכחי; זה מחוץ לתחום, ממתין
ל-issue #10.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import storage


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_structure(project_id: str, db: sqlite3.Connection) -> str:
    """F08 (issue #9): כותבת את מצב ה-`spreads`/`spread_photos` הנוכחי של
    הפרויקט ל-JSON בנתיב `storage.export_structure_prev_path(project_id)`,
    ומחזירה את הנתיב (כ-`str`) שנכתב אליו.

    נקראת ע"י ה-caller (frontend-rtl) *לפני* `layout_engine.regenerate_layout`
    - ראה INTERFACES.md סעיף 6 (זרימת F08 "יצירה מחדש", שלבים 1-2). אין כאן
    אכיפה שהיא נקראת בפועל לפני regenerate_layout (אין state משותף/lock
    ביניהן) - זו אחריות ה-UI flow המתועדת, לא קוד הגנתי כאן.

    פורמט ה-JSON (לא מתועד כחוזה מחייב חוץ-ל-snapshot/restore הידני
    המתואר ב-INTERFACES.md סעיף 6 - "השחזור ידני מתוך album_structure_prev.json",
    כלומר אדם/סוכן קורא את הקובץ, לא קוד אוטומטי שמפענח אותו היום):

        {
          "project_id": "...",
          "snapshotted_at": "<ISO-8601 UTC>",
          "spreads": [
            {
              "id": "...", "position": 1, "template_id": "...",
              "version": 1, "updated_at": "...",
              "photos": [
                {"slot_index": 0, "photo_id": "...", "is_dominant": true},
                ...
              ]
            },
            ...
          ]
        }

    פרויקט ללא כפולות (לפני generate_layout, או project_id שלא קיים כלל)
    -> נכתב קובץ תקין עם `"spreads": []` (לא exception/404 - זו פונקציית
    utility טהורה שרק קוראת DB וכותבת קובץ, לא route handler; 404-check על
    קיום הפרויקט הוא אחריות שכבת ה-HTTP אם/כשתיחשף endpoint, לא כאן).
    """
    spread_rows = db.execute(
        """SELECT id, position, template_id, version, updated_at
           FROM spreads WHERE project_id = ? ORDER BY position""",
        (project_id,),
    ).fetchall()

    spreads: list[dict] = []
    for row in spread_rows:
        photo_rows = db.execute(
            """SELECT slot_index, photo_id, is_dominant FROM spread_photos
               WHERE spread_id = ? ORDER BY slot_index""",
            (row["id"],),
        ).fetchall()
        spreads.append(
            {
                "id": row["id"],
                "position": row["position"],
                "template_id": row["template_id"],
                "version": row["version"],
                "updated_at": row["updated_at"],
                "photos": [
                    {
                        "slot_index": p["slot_index"],
                        "photo_id": p["photo_id"],
                        "is_dominant": bool(p["is_dominant"]),
                    }
                    for p in photo_rows
                ],
            }
        )

    structure = {
        "project_id": project_id,
        "snapshotted_at": _now_iso(),
        "spreads": spreads,
    }

    path = storage.export_structure_prev_path(project_id)
    path.write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
