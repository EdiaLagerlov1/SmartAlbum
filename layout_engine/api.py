"""
SmartAlbum - layout-engine: F07 (יצירת 15 כפולות והתאמת תבנית, issue #8) +
F08 (החלפת תמונה ושינוי תבנית, issue #9 - חלק ה-backend).

ממשק מחייב מוגדר ב-INTERFACES.md סעיף 4.3 - אין להמציא חתימות אחרות.
מודול זה *לא* מממש את לוגיקת הבחירה (selection-engine, F06) ו*לא* מעבד
פיקסלים (image-pipeline) - רק סידור תמונות שכבר נבחרו ב-grid של 15 כפולות,
לפי הנתונים (orientation/sharpness/exposure) שכבר קיימים ב-`photos`.

היקף המודול כאן (issue #8 / F07 + issue #9 / F08 "החלפת תמונה ושינוי תבנית"):
  - `generate_layout` - F07: בונה בדיוק 15 שורות `spreads` (position 1..15)
    ו-`spread_photos` לפי אחת מ-6 התבניות הקבועות (`templates.json`). קובעת
    תמונה "דומיננטית" (מרכזית) לכל כפולה ובוחרת תבנית לפי כמות/כיוון
    התמונות שהוקצו לאותה כפולה.
  - `replace_photo_in_slot` - F08 (issue #9): מחליפה תמונה בודדת בכפולה
    קיימת בתמונה אחרת מתוך תמונות הפרויקט (שאינה `flag='exclude'`).
  - `change_template` - F08 (issue #9): מחליפה את התבנית של כפולה קיימת,
    בכפוף להתאמת מספר הסלוטים למספר התמונות הקיימות בכפולה (כדי לא "לאבד"
    תמונות).
  - `get_spreads` - קריאה (read-only) של מצב הכפולות הנוכחי של פרויקט -
    משמש גם את ה-UI (frontend-rtl) להצגה/רינדור, וגם את הטסטים של "שחזור"
    (reload) ב-F08.

**מודל ה-save/restore שנבחר כאן (F08, issue #9 - אין "draft" נפרד)**: לפי
ההנחיה המפורשת ב-issue - `generate_layout`/`replace_photo_in_slot`/
`change_template` כולן כותבות ומבצעות `commit()` ל-SQLite **באופן מיידי**,
בדיוק כמו `generate_layout` הקיים (F07). אין שכבת "draft בזיכרון" שממתינה
ל"שמירה מפורשת" - "שמירה מפורשת" (לפי האפיון) מתקיימת כבר ברגע הקריאה
לפונקציה (היא *היא* ה"שמירה" - אין פעולת save נפרדת ב-backend); "פתיחה
מחדש משחזרת את הגרסה האחרונה" מתקיימת אוטומטית כי `get_spreads`/`GET
/api/projects/<id>/spreads` קוראים את אותן שורות SQLite שנכתבו - אין cache
בזיכרון-תהליך שצריך "לשחזר" ממנו. זו הפרשנות הפשוטה ביותר העקבית עם
`schema.sql` (spreads/spread_photos הן הטבלאות היחידות, בלי טבלת
"spreads_draft" sibling) ועם התנהגות `generate_layout` הקיימת כבר. ראה
דיון מלא ב-INTERFACES.md סעיף 4.3.

**`spreads.version`**: מקודם (+1) בכל קריאה מצליחה ל-`replace_photo_in_slot`
או `change_template` על אותה כפולה (לא רק ב"יצירה מחדש" המלאה של סעיף 6
ב-INTERFACES.md - זו קביעה מפורשת של הסוכן הזה, ראה תיעוד ב-INTERFACES.md
סעיף 4.3, כי לא היה architect זמין לתאם איתו, לפי הנחיית ה-issue). `version`
**לא** משמש כאן למימוש undo/redo בפועל (זה נשאר מחוץ להיקף, כפי שה-issue
מבהיר) - הוא רק מונה-עדכונים גלוי ל-API/UI (לדוגמה לאיתור "המצב השתנה
מאז הטעינה האחרונה"), עקבי עם התיאור ב-`schema.sql`.

**דה-דופ בהחלפת תמונה (F08 vs F07)**: כברירת מחדל `replace_photo_in_slot`
**דוחה** תמונה חדשה שכבר משובצת בכפולה *אחרת* באלבום (ממשיך את כלל-הדה-דופ
של F07) - אלא אם הקורא מעביר `allow_duplicate=True` במפורש (אנלוגי ל-
`explicit_duplicates` של `generate_layout` - "המשתמשת הוסיפה תמונה שוב
במפורש" היא תמיד פעולה נפרדת/מפורשת, לא ברירת מחדל של האלגוריתם, ראה
ה-docstring של `generate_layout` מעלה ודרישת ה-issue).

**תבנית אחרי swap (F08)**: `replace_photo_in_slot` **לא** מחשבת מחדש
תבנית/דומיננטיות - התמונה החדשה פשוט "נכנסת" לאותו slot_index (ולאותו
is_dominant) שבו הייתה התמונה הישנה. אם המשתמשת רוצה תבנית אחרת (כי כיוון
התמונה החדשה לא מתאים יותר) - זו קריאה נפרדת ומפורשת ל-`change_template`,
לא תוצאה-לוואי אוטומטית של swap (כדי לא "להפתיע" את המשתמשת בשינוי פריסה
שהיא לא ביקשה - ראה דיון ב-INTERFACES.md סעיף 4.3).

**לא ממומש כאן בכוונה**: `regenerate_layout` (F08, "יצירה מחדש" המלאה,
כולל modal אישור + `export_agent.snapshot_structure` - זרימה נפרדת
המתועדת בסעיף 6 ב-INTERFACES.md) - מחוץ להיקף issue #9 כפי שתואר במשימה;
`replace_photo_in_slot`/`change_template` (סעיף זה) הן הפעולות שבאמת
התבקשו ב-issue #9 ("החלפת תמונות ושינוי תבנית").

כללי חסימת-יצירה (ראה INTERFACES.md סעיף 4.2, הערת architect מ-issue #5):
`generate_layout` חייבת לקרוא ל-`selection_engine.check_must_limit` כצעד
ראשון ולהיחסם (status="error") אם `must_count > 60` - בדיוק כמו
`run_selection`. זו הסיבה היחידה לייבוא ישיר של `selection_engine.api` כאן
(קריאת-פונקציה ישירה בין מודולים, לא HTTP - לפי INTERFACES.md סעיף 1).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from selection_engine import api as selection_engine

# ===========================================================================
# קטלוג התבניות (6 תבניות קבועות, 1-4 תמונות בכפולה) - נטען פעם אחת מ-
# templates.json (ראה INTERFACES.md סעיף 4.3 לפורמט המדויק: id/slot_count/
# slots[{index,x,y,w,h,orientation,dominant}]). frontend-rtl ו-export-agent
# קוראים את אותו קובץ JSON - אל תשכפל את התוכן בקוד Python.
# ===========================================================================
_TEMPLATES_PATH = Path(__file__).resolve().parent / "templates.json"


def _load_templates() -> dict[str, dict]:
    data = json.loads(_TEMPLATES_PATH.read_text(encoding="utf-8"))
    return {tpl["id"]: tpl for tpl in data["templates"]}


TEMPLATES: dict[str, dict] = _load_templates()

# --- קבועי-אפיון (F07): בדיוק 15 כפולות, עד 4 תמונות בכפולה (לפי התבנית
# הגדולה ביותר בקטלוג - grid-mixed). שינוי דורש עדכון INTERFACES.md קודם. ---
SPREAD_COUNT = 15
MAX_PHOTOS_PER_SPREAD = 4
MIN_SELECTED_FOR_LAYOUT = SPREAD_COUNT  # לפחות תמונה אחת לכל כפולה
MAX_SELECTED_FOR_LAYOUT = SPREAD_COUNT * MAX_PHOTOS_PER_SPREAD  # 60, תואם MUST_LIMIT של F06


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_job_status(
    db: sqlite3.Connection,
    project_id: str,
    stage: str,
    status: Literal["pending", "running", "done", "failed"],
    error_reason: str | None = None,
) -> None:
    """Upsert שורת processing_jobs - אותה תבנית בדיוק כמו image_pipeline/
    selection_engine (מוחזק בנפרד, לא מיובא - מודולים עצמאיים, ראה
    INTERFACES.md סעיף 1)."""
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


@dataclass
class LayoutResult:
    status: Literal["ok", "error"]
    spread_ids: list[str]
    message: str | None


# ===========================================================================
# עזרים: חלוקת תמונות ל-15 קבוצות, כיוון/איכות, בחירת תבנית, שיבוץ סלוטים.
# ===========================================================================

_DEFAULT_PHOTO_INFO: dict = {
    "width": None,
    "height": None,
    "sharpness_score": None,
    "exposure_score": None,
}


def _dedupe_preserving_explicit(
    photo_ids: list[str], explicit_duplicates: set[str]
) -> list[str]:
    """כל תמונה פעם אחת כברירת מחדל; תמונה ב-`explicit_duplicates` *לא*
    מצומצמת - כל ההופעות שלה ב-`photo_ids` נשמרות (המשתמשת הוסיפה אותה
    שוב במפורש, לא ברירת מחדל של האלגוריתם - ראה docstring `generate_layout`).
    שומר על סדר ה-input (סדר כרונולוגי/selection_order)."""
    seen: set[str] = set()
    result: list[str] = []
    for pid in photo_ids:
        if pid in explicit_duplicates:
            result.append(pid)
            continue
        if pid in seen:
            continue
        seen.add(pid)
        result.append(pid)
    return result


def _dedupe_within_group(group: list[str]) -> list[str]:
    """הגנת-עומק: `spread_photos` יש לו UNIQUE(spread_id, photo_id) - תמונה
    לא יכולה להופיע פעמיים *באותה* כפולה. אם שתי הופעות של תמונה
    `explicit_duplicates` נפלו (במקרה) לאותה קבוצה אחרי החלוקה - משאירים
    רק את ההופעה הראשונה בקבוצה הזו (היא עדיין יכולה להופיע בכפולה אחרת)."""
    seen: set[str] = set()
    result: list[str] = []
    for pid in group:
        if pid in seen:
            continue
        seen.add(pid)
        result.append(pid)
    return result


def _distribute_sizes(n: int, spread_count: int) -> list[int]:
    """חלוקת n תמונות ל-spread_count קבוצות, כמה שיותר שווה, בלי לשבור את
    הסדר הכרונולוגי (כל קבוצה היא "חלון" רציף מתוך רשימת הקלט). העודף
    (n % spread_count) מחולק לכפולות ה*ראשונות* (לפי סדר כרונולוגי) - בחירה
    שרירותית פשוטה, אין לה משמעות נרטיבית (לא "הכפולות הראשונות חשובות
    יותר"), רק קלה למימוש/לבדיקה. עם n בין 15..60 (התחום בפועל, ראה F06
    MUST_LIMIT=60 ו-MIN_SELECTED_FOR_LAYOUT=15 מעלה) התוצאה היא תמיד 1..4
    תמונות לכפולה - תואם את קטלוג 6 התבניות (1-4 סלוטים)."""
    base, remainder = divmod(n, spread_count)
    return [base + 1 if i < remainder else base for i in range(spread_count)]


def _load_photo_info(
    project_id: str, photo_ids: list[str], db: sqlite3.Connection
) -> dict[str, dict]:
    """שולף width/height (כיוון) ו-sharpness_score/exposure_score (איכות,
    F04) לכל תמונה רלוונטית, בשאילתה אחת (לא per-photo)."""
    distinct_ids = list(dict.fromkeys(photo_ids))
    if not distinct_ids:
        return {}
    placeholders = ",".join("?" for _ in distinct_ids)
    rows = db.execute(
        f"""SELECT id, width, height, sharpness_score, exposure_score
            FROM photos WHERE project_id = ? AND id IN ({placeholders})""",
        (project_id, *distinct_ids),
    ).fetchall()
    return {
        row["id"]: {
            "width": row["width"],
            "height": row["height"],
            "sharpness_score": row["sharpness_score"],
            "exposure_score": row["exposure_score"],
        }
        for row in rows
    }


def _orientation(info_entry: dict | None) -> Literal["landscape", "portrait"]:
    """'portrait' רק אם height > width בוודאות; כל מקרה אחר (landscape
    אמיתי, ריבוע, או width/height חסרים) -> 'landscape' (ברירת מחדל
    מתועדת - "לא מנחש" portrait בלי עדות חד-משמעית מה-EXIF/image-pipeline)."""
    entry = info_entry or _DEFAULT_PHOTO_INFO
    width, height = entry.get("width"), entry.get("height")
    if width is not None and height is not None and height > width:
        return "portrait"
    return "landscape"


def _quality_key(info_entry: dict | None) -> tuple[float, float]:
    """(sharpness_score, exposure_score), NULL/חסר -> -1.0 - אותו עקרון
    בדיוק כמו `selection_engine.api._quality_sort_key` (F04: לא ציון מומצא,
    רק דירוג-יחסי בין מועמדות; מודול זה לא מייבא את הפונקציה הפרטית ההיא -
    כל מודול מחזיק את ההיגיון שלו, ראה הערת-ראש המודול)."""
    entry = info_entry or _DEFAULT_PHOTO_INFO
    sharpness = entry.get("sharpness_score")
    exposure = entry.get("exposure_score")
    return (
        sharpness if sharpness is not None else -1.0,
        exposure if exposure is not None else -1.0,
    )


def _pick_dominant(group: list[str], info: dict[str, dict]) -> str:
    """תמונה "מרכזית"/דומיננטית של הכפולה (F07): האיכות הגבוהה ביותר
    (sharpness_score, exposure_score) מבין תמונות הכפולה. `max()` עם `key`
    שומר על ההופעה *הראשונה* בשוויון (סדר כרונולוגי/selection_order,
    כבר השמור בסדר `group`) - "לא מנחש", נפילה דטרמיניסטית לכרונולוגיה."""
    return max(group, key=lambda pid: _quality_key(info.get(pid)))


def _choose_template(group: list[str], info: dict[str, dict]) -> str:
    """בוחרת תבנית מתוך 6 התבניות הקבועות לפי כמות התמונות בכפולה והתאמת
    כיוון (landscape/portrait) - ראה `templates.json`:

      1 תמונה  -> single-feature-landscape / single-feature-portrait,
                  לפי כיוון התמונה היחידה (שהיא אוטומטית גם הדומיננטית).
      2 תמונות -> duo-portrait אם *שתיהן* portrait, אחרת duo-landscape
                  (כולל המקרה המעורב - 1 portrait + 1 landscape - ברירת
                  מחדל ל-landscape, מתועד: "בלי התאמה טובה בדיוק, לא
                  מנחש/חותך - רק בוחר תבנית סבירה; ההתאמה-עם-שוליים
                  לתמונה שלא תואמת בדיוק את orientation הסלוט היא אחריות
                  שכבת-התצוגה/export, לא layout-engine - ראה היקף המודול
                  בראש הקובץ").
      3 תמונות -> trio-mixed (כל הסלוטים "any" - אין תלות בכיוון).
      4 תמונות -> grid-mixed (כל הסלוטים "any").
    """
    n = len(group)
    if n == 1:
        return (
            "single-feature-portrait"
            if _orientation(info.get(group[0])) == "portrait"
            else "single-feature-landscape"
        )
    if n == 2:
        orientations = [_orientation(info.get(pid)) for pid in group]
        return "duo-portrait" if orientations.count("portrait") == 2 else "duo-landscape"
    if n == 3:
        return "trio-mixed"
    if n == 4:
        return "grid-mixed"
    raise ValueError(
        f"layout-engine: גודל קבוצה לא נתמך ({n}) - התבניות הקיימות תומכות רק ב-1..4 "
        "תמונות בכפולה (ראה MAX_PHOTOS_PER_SPREAD)"
    )


def _assign_slots(
    template: dict, group: list[str], dominant_id: str
) -> list[tuple[int, str, bool]]:
    """משבצת כל תמונה בקבוצה לסלוט בתבנית: התמונה הדומיננטית -> הסלוט עם
    `dominant: true` (יש בדיוק אחד כזה בכל תבנית בקטלוג); שאר התמונות ->
    שאר הסלוטים, לפי סדר הסלוטים (index) ולפי הסדר הכרונולוגי שבו הגיעו
    בקבוצה. מחזיר list[(slot_index, photo_id, is_dominant)]."""
    slots = sorted(template["slots"], key=lambda s: s["index"])
    others = iter(pid for pid in group if pid != dominant_id)
    assignments: list[tuple[int, str, bool]] = []
    for slot in slots:
        if slot["dominant"]:
            assignments.append((slot["index"], dominant_id, True))
        else:
            assignments.append((slot["index"], next(others), False))
    return assignments


# ===========================================================================
# F07 (issue #8): generate_layout - הפונקציה היחידה שממומשת בהיקף הזה.
# ===========================================================================
def generate_layout(
    project_id: str,
    selected_photo_ids: list[str],
    db: sqlite3.Connection,
    explicit_duplicates: set[str] | None = None,
) -> LayoutResult:
    """F07 (issue #8): בונה בדיוק 15 שורות `spreads` (position 1..15)
    ו-`spread_photos` לפי אחת מ-6 התבניות הקבועות (`templates.json`).

    קלט: `selected_photo_ids` היא **רשימת ה-photo_id הרשמית היחידה** שהקורא
    (frontend-rtl/main.py) מעביר - נשלפת אצלו באמצעות ה-query המחייב
    (`SELECT id FROM photos WHERE project_id=? AND selected_for_album=1
    ORDER BY selection_order`, ראה INTERFACES.md סעיף 4.2) *לפני* הקריאה
    לכאן. הפונקציה הזו לא שולפת selected_for_album בעצמה - כך שאפשר גם
    להעביר רשימה מתוקנת-ידנית (למשל ל-regenerate_layout העתידי, F08).

    אלגוריתם:
      1. `check_must_limit` כצעד ראשון (כמו run_selection, F06) - `must_count
         > 60` -> status="error", **בלי לכתוב כלום** ("יצירה" חסומה - ראה
         כלל-חסימת-היצירה ב-INTERFACES.md סעיף 4.2).
      2. דה-דופ: כל תמונה פעם אחת בכל האלבום כברירת מחדל; תמונה שמופיעה
         ב-`explicit_duplicates` (set של photo_id) שומרת את *כל* הופעותיה
         ב-`selected_photo_ids` - זו המנגנון היחיד ל"המשתמשת הוסיפה אותה
         שוב במפורש" (לא ברירת מחדל של האלגוריתם, לפי ה-issue).
      3. סך התמונות (אחרי דה-דופ) חייב להיות בין 15 (MIN_SELECTED_FOR_LAYOUT,
         תמונה אחת לפחות לכל כפולה) ל-60 (MAX_SELECTED_FOR_LAYOUT, 4 לכל
         כפולה - תואם MUST_LIMIT) - אחרת status="error" (לדוגמה "אין מספיק
         תמונות נבחרות ל-15 כפולות" - "status" הוא Literal["ok","error"]
         לפי INTERFACES.md סעיף 4.3, אין status נפרד ל"too_few"; ה-message
         מסביר את הסיבה), בלי לכתוב כלום.
      4. חלוקה ל-15 קבוצות רציפות (שומרות סדר כרונולוגי/selection_order) -
         כמה שיותר שווה (`_distribute_sizes`); העודף מחולק לכפולות
         הראשונות (בחירה שרירותית מתועדת, לא נרטיבית).
      5. לכל קבוצה: תמונה דומיננטית = האיכות הגבוהה ביותר (sharpness/
         exposure, F04) - קובעת גם את ה-slot הדומיננטי בתבנית (F07: "תמונה
         מרכזית בכל כפולה"); תבנית נבחרת לפי כמות+כיוון התמונות בקבוצה
         (`_choose_template`, ראה docstring שם).
      6. **הרצה חוזרת מחליפה את הכפולות הקודמות של הפרויקט במלואה** - לא
         מצרפת (בדיוק כמו `run_selection` ל-F06): כל שורות `spreads`/
         `spread_photos` הקיימות לפרויקט נמחקות לפני כתיבת הכפולות החדשות.
         (הערה: זה *לא* ה-"regenerate" המתועד ב-F08/סעיף 6 - זה דורש snapshot
         מפורש + אישור UI ו-`spreads.version` מקודם; זה תחום F08/issue #9,
         לא ממומש כאן - ראה docstring המודול).
    """
    explicit_duplicates = explicit_duplicates or set()
    _set_job_status(db, project_id, "layout", "running")

    must_check = selection_engine.check_must_limit(project_id, db)
    if not must_check.ok:
        _set_job_status(db, project_id, "layout", "failed", "too_many_must")
        return LayoutResult(
            status="error",
            spread_ids=[],
            message=(
                f"יותר מ-{selection_engine.MUST_LIMIT} תמונות מסומנות 'חובה' "
                f"({must_check.must_count}) - יש לתקן את הבחירה לפני יצירת הכפולות"
            ),
        )

    deduped = _dedupe_preserving_explicit(selected_photo_ids, explicit_duplicates)
    n = len(deduped)

    if n < MIN_SELECTED_FOR_LAYOUT:
        _set_job_status(db, project_id, "layout", "failed", "too_few_selected_photos")
        return LayoutResult(
            status="error",
            spread_ids=[],
            message=(
                f"יש רק {n} תמונות נבחרות - נדרשות לפחות {MIN_SELECTED_FOR_LAYOUT} "
                f"כדי למלא {SPREAD_COUNT} כפולות (תמונה אחת לפחות בכל כפולה). "
                "יש להריץ בחירה (run_selection) או להוסיף תמונות"
            ),
        )

    if n > MAX_SELECTED_FOR_LAYOUT:
        _set_job_status(db, project_id, "layout", "failed", "too_many_selected_photos")
        return LayoutResult(
            status="error",
            spread_ids=[],
            message=(
                f"יש {n} תמונות נבחרות - יותר מ-{MAX_SELECTED_FOR_LAYOUT} "
                f"(המקסימום: {SPREAD_COUNT} כפולות כפול {MAX_PHOTOS_PER_SPREAD} תמונות לכפולה)"
            ),
        )

    info = _load_photo_info(project_id, deduped, db)

    sizes = _distribute_sizes(n, SPREAD_COUNT)
    groups: list[list[str]] = []
    idx = 0
    for size in sizes:
        groups.append(_dedupe_within_group(deduped[idx : idx + size]))
        idx += size

    now = _now_iso()

    # "הרצה מחדש" מחליפה את הכפולות הקודמות במלואה - לא מצרפת (ראה (6) ב-docstring).
    db.execute(
        "DELETE FROM spread_photos WHERE spread_id IN (SELECT id FROM spreads WHERE project_id = ?)",
        (project_id,),
    )
    db.execute("DELETE FROM spreads WHERE project_id = ?", (project_id,))

    spread_ids: list[str] = []
    for position, group in enumerate(groups, start=1):
        template_id = _choose_template(group, info)
        template = TEMPLATES[template_id]
        dominant_id = _pick_dominant(group, info)
        spread_id = str(uuid.uuid4())

        db.execute(
            """INSERT INTO spreads (id, project_id, position, template_id, version, updated_at)
               VALUES (?, ?, ?, ?, 1, ?)""",
            (spread_id, project_id, position, template_id, now),
        )
        for slot_index, photo_id, is_dominant in _assign_slots(template, group, dominant_id):
            db.execute(
                """INSERT INTO spread_photos (spread_id, photo_id, slot_index, is_dominant)
                   VALUES (?, ?, ?, ?)""",
                (spread_id, photo_id, slot_index, 1 if is_dominant else 0),
            )
        spread_ids.append(spread_id)

    db.commit()
    _set_job_status(db, project_id, "layout", "done")

    return LayoutResult(status="ok", spread_ids=spread_ids, message=None)


# ===========================================================================
# F08 (issue #9): replace_photo_in_slot / change_template / get_spreads.
# ראה docstring המודול (למעלה) להחלטות המתועדות (save/restore, version,
# דה-דופ, תבנית-אחרי-swap) ואת INTERFACES.md סעיף 4.3 לדיון המלא.
# ===========================================================================

_SWAP_ERROR_MESSAGES: dict[str, str] = {
    "spread_not_found": "הכפולה המבוקשת לא נמצאה בפרויקט הזה",
    "old_photo_not_in_spread": "התמונה המוחלפת אינה משובצת בכפולה הזו",
    "new_photo_not_found": "התמונה החדשה אינה חלק מתמונות הפרויקט הזה",
    "new_photo_excluded": "לא ניתן להוסיף תמונה שסומנה 'exclude' (הוחרגה) לכפולה",
    "new_photo_already_in_spread": "התמונה החדשה כבר משובצת בכפולה הזו בסלוט אחר",
    "new_photo_already_in_album": (
        "התמונה החדשה כבר מופיעה בכפולה אחרת באלבום - "
        "יש להעביר allow_duplicate=True כדי להוסיף אותה גם כאן במפורש"
    ),
}

_TEMPLATE_ERROR_MESSAGES: dict[str, str] = {
    "spread_not_found": "הכפולה המבוקשת לא נמצאה בפרויקט הזה",
    "template_not_found": "מזהה התבנית אינו קיים בקטלוג (templates.json)",
}


@dataclass
class ReplacePhotoResult:
    status: Literal["ok", "error"]
    error_reason: str | None   # מפתח מתוך _SWAP_ERROR_MESSAGES, None אם ok
    message: str | None        # הודעה בעברית (גם בהצלחה - למשל "no-op")
    spread_id: str | None


@dataclass
class ChangeTemplateResult:
    status: Literal["ok", "error"]
    error_reason: str | None   # מפתח מתוך _TEMPLATE_ERROR_MESSAGES, None אם ok
    message: str | None
    spread_id: str | None
    template_id: str | None    # התבנית בפועל אחרי הקריאה (ישנה אם error)


def _get_spread_row(project_id: str, spread_id: str, db: sqlite3.Connection):
    return db.execute(
        "SELECT id, project_id, template_id, version FROM spreads WHERE id = ? AND project_id = ?",
        (spread_id, project_id),
    ).fetchone()


def _bump_spread_version(db: sqlite3.Connection, spread_id: str, *, template_id: str | None = None) -> None:
    """מקדמת spreads.version ב-1 ומעדכנת updated_at - נקראת מ-
    replace_photo_in_slot/change_template בכל מוטציה מצליחה (ראה docstring
    המודול - "מקודם בכל קריאה מצליחה", לא רק ב-regenerate המלא של סעיף 6)."""
    if template_id is not None:
        db.execute(
            "UPDATE spreads SET template_id = ?, version = version + 1, updated_at = ? WHERE id = ?",
            (template_id, _now_iso(), spread_id),
        )
    else:
        db.execute(
            "UPDATE spreads SET version = version + 1, updated_at = ? WHERE id = ?",
            (_now_iso(), spread_id),
        )


def replace_photo_in_slot(
    project_id: str,
    spread_id: str,
    old_photo_id: str,
    new_photo_id: str,
    db: sqlite3.Connection,
    *,
    allow_duplicate: bool = False,
) -> ReplacePhotoResult:
    """F08 (issue #9): מחליפה את `old_photo_id` ב-`new_photo_id` באותו
    slot_index (ואותו is_dominant) בתוך `spread_id`.

    ולידציות (לפי סדר, הראשונה שנכשלת עוצרת - לא נכתב כלום):
      1. `spread_id` קיים ומשתייך ל-`project_id` -> אחרת "spread_not_found".
      2. `old_photo_id` משובץ בפועל באותה כפולה -> אחרת "old_photo_not_in_spread".
      3. אם `new_photo_id == old_photo_id` - no-op מוצלח (status="ok", בלי
         לכתוב כלום/לקדם version - אין שינוי אמיתי).
      4. `new_photo_id` הוא תמונת-פרויקט אמיתית (שייכת ל-project_id הזה)
         -> אחרת "new_photo_not_found".
      5. `new_photo_id` אינה `flag='exclude'` -> אחרת "new_photo_excluded"
         (לפי האפיון המפורש ב-issue - "מתמונות הפרויקט שלא הוחרגו").
      6. `new_photo_id` אינה כבר בכפולה הזו (בסלוט אחר) -> אחרת
         "new_photo_already_in_spread" (לא הגיוני, ובכל מקרה ה-UNIQUE
         constraint של spread_photos(spread_id, photo_id) יחסום את זה).
      7. אם `allow_duplicate` is False (ברירת מחדל): `new_photo_id` אינה
         משובצת בכפולה *אחרת* באלבום הזה -> אחרת "new_photo_already_in_album"
         (ממשיך את כלל-הדה-דופ של F07 - "כל תמונה פעם אחת" כברירת מחדל,
         החריגה היחידה היא בקשה מפורשת, בדיוק כמו explicit_duplicates).

    בהצלחה: מעדכנת את שורת spread_photos (photo_id בלבד - slot_index/
    is_dominant נשארים כמו שהיו), מקדמת spreads.version+1 ו-updated_at,
    ו-commit מיידי (ראה דיון save/restore ב-docstring המודול).

    **לא** מחשבת מחדש תבנית/דומיננטיות - ראה docstring המודול.
    """
    spread_row = _get_spread_row(project_id, spread_id, db)
    if spread_row is None:
        return ReplacePhotoResult(
            status="error",
            error_reason="spread_not_found",
            message=_SWAP_ERROR_MESSAGES["spread_not_found"],
            spread_id=None,
        )

    current_slot = db.execute(
        "SELECT slot_index, is_dominant FROM spread_photos WHERE spread_id = ? AND photo_id = ?",
        (spread_id, old_photo_id),
    ).fetchone()
    if current_slot is None:
        return ReplacePhotoResult(
            status="error",
            error_reason="old_photo_not_in_spread",
            message=_SWAP_ERROR_MESSAGES["old_photo_not_in_spread"],
            spread_id=spread_id,
        )

    if new_photo_id == old_photo_id:
        return ReplacePhotoResult(
            status="ok",
            error_reason=None,
            message="התמונה החדשה זהה לישנה - לא בוצע שינוי",
            spread_id=spread_id,
        )

    new_photo_row = db.execute(
        "SELECT flag FROM photos WHERE id = ? AND project_id = ?",
        (new_photo_id, project_id),
    ).fetchone()
    if new_photo_row is None:
        return ReplacePhotoResult(
            status="error",
            error_reason="new_photo_not_found",
            message=_SWAP_ERROR_MESSAGES["new_photo_not_found"],
            spread_id=spread_id,
        )

    if new_photo_row["flag"] == "exclude":
        return ReplacePhotoResult(
            status="error",
            error_reason="new_photo_excluded",
            message=_SWAP_ERROR_MESSAGES["new_photo_excluded"],
            spread_id=spread_id,
        )

    already_in_this_spread = db.execute(
        "SELECT 1 FROM spread_photos WHERE spread_id = ? AND photo_id = ?",
        (spread_id, new_photo_id),
    ).fetchone()
    if already_in_this_spread is not None:
        return ReplacePhotoResult(
            status="error",
            error_reason="new_photo_already_in_spread",
            message=_SWAP_ERROR_MESSAGES["new_photo_already_in_spread"],
            spread_id=spread_id,
        )

    if not allow_duplicate:
        already_in_album = db.execute(
            """SELECT 1 FROM spread_photos sp JOIN spreads s ON sp.spread_id = s.id
               WHERE s.project_id = ? AND sp.photo_id = ?""",
            (project_id, new_photo_id),
        ).fetchone()
        if already_in_album is not None:
            return ReplacePhotoResult(
                status="error",
                error_reason="new_photo_already_in_album",
                message=_SWAP_ERROR_MESSAGES["new_photo_already_in_album"],
                spread_id=spread_id,
            )

    db.execute(
        "UPDATE spread_photos SET photo_id = ? WHERE spread_id = ? AND slot_index = ?",
        (new_photo_id, spread_id, current_slot["slot_index"]),
    )
    _bump_spread_version(db, spread_id)
    db.commit()

    return ReplacePhotoResult(status="ok", error_reason=None, message=None, spread_id=spread_id)


def change_template(
    project_id: str,
    spread_id: str,
    new_template_id: str,
    db: sqlite3.Connection,
) -> ChangeTemplateResult:
    """F08 (issue #9): מחליפה את התבנית של `spread_id` ל-`new_template_id`,
    ללא תלות בבחירת התבנית האוטומטית של `generate_layout` (F07).

    ולידציות (לפי סדר, הראשונה שנכשלת עוצרת - לא נכתב כלום):
      1. `spread_id` קיים ומשתייך ל-`project_id` -> אחרת "spread_not_found".
      2. `new_template_id` קיים בקטלוג הקבוע (`TEMPLATES`, `templates.json`)
         -> אחרת "template_not_found".
      3. `slot_count` של התבנית החדשה **שווה בדיוק** למספר התמונות הקיימות
         בכפולה הזו -> אחרת "slot_count_mismatch". כלל זה מכוון, לפי ה-issue
         ("להחליט כלל סביר כדי לא לאבד תמונות בשקט"): גם *פחות* סלוטים (היה
         מוחק תמונות) וגם *יותר* סלוטים (היה משאיר סלוטים ריקים בלי תמונה -
         גם זו תוצאה לא-תקינה/מבלבלת ב-UI) נדחים - דחייה מפורשת עם status
         ברור, לא דילול/ריקון שקט. אם המשתמשת רוצה גם לשנות כמות תמונות
         וגם תבנית - זה זורם דרך `replace_photo_in_slot`/regenerate, לא
         דרך `change_template` לבדו.

    בהצלחה: משבצת מחדש את התמונות הקיימות לסלוטים של התבנית החדשה -
    התמונה שהייתה דומיננטית (is_dominant=1) ממשיכה להיות דומיננטית (משובצת
    לסלוט ה-dominant של התבנית החדשה), שאר התמונות משובצות לשאר הסלוטים
    לפי הסדר הקיים (slot_index עולה) - בדיוק אותה לוגיקה כמו `_assign_slots`
    ב-`generate_layout` (F07), בלי לחשב מחדש דומיננטיות/כיוון (זו בחירת-
    תבנית *מפורשת* של המשתמשת - אין "ניחוש" נוסף). מעדכנת spreads.template_id,
    מקדמת version+1 ו-updated_at, commit מיידי (ראה docstring המודול).
    """
    spread_row = _get_spread_row(project_id, spread_id, db)
    if spread_row is None:
        return ChangeTemplateResult(
            status="error",
            error_reason="spread_not_found",
            message=_TEMPLATE_ERROR_MESSAGES["spread_not_found"],
            spread_id=None,
            template_id=None,
        )

    if new_template_id not in TEMPLATES:
        return ChangeTemplateResult(
            status="error",
            error_reason="template_not_found",
            message=_TEMPLATE_ERROR_MESSAGES["template_not_found"],
            spread_id=spread_id,
            template_id=spread_row["template_id"],
        )

    current_rows = db.execute(
        "SELECT slot_index, photo_id, is_dominant FROM spread_photos WHERE spread_id = ? ORDER BY slot_index",
        (spread_id,),
    ).fetchall()

    new_template = TEMPLATES[new_template_id]
    if new_template["slot_count"] != len(current_rows):
        return ChangeTemplateResult(
            status="error",
            error_reason="slot_count_mismatch",
            message=(
                f"התבנית '{new_template_id}' מתאימה ל-{new_template['slot_count']} תמונות, "
                f"אך לכפולה הזו יש {len(current_rows)} תמונות - לא ניתן לשנות תבנית "
                "בלי לאבד/להשאיר ריק סלוט (יש להתאים את מספר התמונות בכפולה קודם)"
            ),
            spread_id=spread_id,
            template_id=spread_row["template_id"],
        )

    group = [row["photo_id"] for row in current_rows]
    dominant_rows = [row for row in current_rows if row["is_dominant"]]
    # הגנת-עומק: לפי schema.sql כל כפולה אמורה לבחור דומיננטי יחיד; אם
    # בכל זאת (קצה-מקרה היסטורי) אין/יש כמה - נופלים לתמונה הכרונולוגית
    # הראשונה בקבוצה, כמו _pick_dominant (לא מנחש, נפילה דטרמיניסטית).
    dominant_id = dominant_rows[0]["photo_id"] if dominant_rows else group[0]

    db.execute("DELETE FROM spread_photos WHERE spread_id = ?", (spread_id,))
    for slot_index, photo_id, is_dominant in _assign_slots(new_template, group, dominant_id):
        db.execute(
            "INSERT INTO spread_photos (spread_id, photo_id, slot_index, is_dominant) VALUES (?, ?, ?, ?)",
            (spread_id, photo_id, slot_index, 1 if is_dominant else 0),
        )

    _bump_spread_version(db, spread_id, template_id=new_template_id)
    db.commit()

    return ChangeTemplateResult(
        status="ok", error_reason=None, message=None, spread_id=spread_id, template_id=new_template_id
    )


def get_spreads(project_id: str, db: sqlite3.Connection) -> list[dict]:
    """קריאה (read-only) של מצב הכפולות הנוכחי של פרויקט, ממוין לפי position.
    משמש גם את `GET /api/projects/<id>/spreads` (frontend-rtl) וגם את
    "שחזור" (F08) - קורא ישירות מ-SQLite, בלי cache בזיכרון, ראה docstring
    המודול (מודל save/restore).

    מחזיר רשימת dict-ים JSON-serializable (לא dataclass - עקבי עם סעיף 1
    ב-INTERFACES.md), אחד per spread:
        {"spread_id", "position", "template_id", "version", "updated_at",
         "photos": [{"slot_index", "photo_id", "is_dominant"}, ...]}
    לא בונה thumbnail_url - זו אחריות main.py (כמו ב-list_photos, F01).
    """
    spread_rows = db.execute(
        "SELECT id, position, template_id, version, updated_at FROM spreads "
        "WHERE project_id = ? ORDER BY position",
        (project_id,),
    ).fetchall()

    result: list[dict] = []
    for row in spread_rows:
        photo_rows = db.execute(
            "SELECT slot_index, photo_id, is_dominant FROM spread_photos "
            "WHERE spread_id = ? ORDER BY slot_index",
            (row["id"],),
        ).fetchall()
        result.append(
            {
                "spread_id": row["id"],
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
    return result
