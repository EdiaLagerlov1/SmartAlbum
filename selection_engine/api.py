"""
SmartAlbum - selection-engine: F03 (סימון חובה/החרגה) - issue #5.

ממשק מחייב מוגדר ב-INTERFACES.md סעיף 4.2 - אין להמציא חתימות אחרות.
מודול זה *לא* מעבד פיקסלים ולא נוגע ב-HTTP - אלה קוראים לו דרך `main.py`
(עטיפה דקה 1:1, ללא לוגיקה נוספת ב-route handler, לפי הכלל בסעיף 1).

היקף המודול כאן (issue #5 / F03 בלבד):
  - `set_flag`       - כתיבת photos.flag בודד (none/must/exclude).
  - `check_must_limit` - ספירת תמונות flag='must' בפרויקט + בדיקת הגבלת 60.

`flag` הוא עמודה **יחידה** ב-`photos` (ראה schema.sql) עם `CHECK (flag IN
('none', 'must', 'exclude'))` - לכן טכנית אי אפשר שתמונה תהיה גם must וגם
exclude בו-זמנית (אין שני בוליאניים נפרדים). ה-CHECK constraint ב-DB הוא
הגנת-עומק; האימות העיקרי (כדי להחזיר הודעת שגיאה ברורה בעברית, לא
sqlite3.IntegrityError גולמי) הוא כאן, ב-`set_flag`, ונאכף שוב ב-`main.py`
(400 מסודר) - F03 מחייב דחייה "ב-UI/API layer, לא רק בלוגיקה".

F05/F06 (תיוג שלבי אירוע, אלגוריתם בחירה) אינם ממומשים במודול הזה עדיין -
יתועדו/יתווספו ב-issue נפרד, לפי אותו חוזה ב-INTERFACES.md סעיף 4.2.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

# סדר הערכים כאן הוא גם סדר התיעוד ב-schema.sql וב-INTERFACES.md - אל תשנה
# בלי לעדכן את שניהם קודם.
VALID_FLAGS: tuple[str, ...] = ("none", "must", "exclude")

# F03: "יותר מ-60 תמונות מסומנות must -> חוסם יצירה". הגבול עצמו (60) הוא
# ערך-אפיון, לא ניחוש - אם ישתנה, זה עדכון ל-INTERFACES.md לפני שינוי כאן.
MUST_LIMIT = 60


class InvalidFlagError(ValueError):
    """F03: flag שאינו אחד מ-VALID_FLAGS. main.py תופס ומחזיר 400 בעברית -
    לא מגיע ל-DB (ה-CHECK constraint הוא הגנת-עומק בלבד, לא התגובה הראשית)."""


def set_flag(
    photo_id: str,
    flag: Literal["none", "must", "exclude"],
    db: sqlite3.Connection,
) -> bool:
    """F03: כתיבת flag בודד לתמונה. **דריסה** של הערך הקודם - flag הוא
    עמודה יחידה, כך שעדכון must -> exclude (או כל מעבר אחר) על אותה תמונה
    משנה את הערך במקום, אין "שני flags" שנשארים.

    מעלה `InvalidFlagError` אם flag אינו אחד מ-VALID_FLAGS (לפני כל פנייה
    ל-DB). מחזיר True אם עודכנה שורת photos קיימת, False אם photo_id לא
    נמצא (הקורא ב-main.py מחליט אם זו 404).
    """
    if flag not in VALID_FLAGS:
        raise InvalidFlagError(
            f"ערך flag לא חוקי: {flag!r} - מותר רק 'none', 'must' או 'exclude'"
        )

    cur = db.execute("UPDATE photos SET flag = ? WHERE id = ?", (flag, photo_id))
    db.commit()
    return cur.rowcount > 0


@dataclass
class MustCountCheck:
    must_count: int
    ok: bool  # False אם must_count > MUST_LIMIT (60)


def check_must_limit(project_id: str, db: sqlite3.Connection) -> MustCountCheck:
    """F03: סופר תמונות flag='must' בפרויקט. `ok=False` כש-`must_count >
    MUST_LIMIT` - זה לא חוסם את הסימון עצמו (המשתמשת יכולה לסמן יותר מ-60
    ולהמשיך), אבל כל "יצירה" (run_selection F06 ואילך - generate_layout/
    regenerate_layout) **חייבת** לקרוא לפונקציה הזו ולהיחסם אם `ok is
    False`, לפי החוזה המתועד ב-INTERFACES.md סעיף 4.2.
    """
    row = db.execute(
        "SELECT COUNT(*) AS cnt FROM photos WHERE project_id = ? AND flag = 'must'",
        (project_id,),
    ).fetchone()
    must_count = row["cnt"] if row is not None else 0
    return MustCountCheck(must_count=must_count, ok=must_count <= MUST_LIMIT)
