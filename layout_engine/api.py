"""
SmartAlbum - layout-engine: F07 (יצירת 15 כפולות והתאמת תבנית, issue #8).

ממשק מחייב מוגדר ב-INTERFACES.md סעיף 4.3 - אין להמציא חתימות אחרות.
מודול זה *לא* מממש את לוגיקת הבחירה (selection-engine, F06) ו*לא* מעבד
פיקסלים (image-pipeline) - רק סידור תמונות שכבר נבחרו ב-grid של 15 כפולות,
לפי הנתונים (orientation/sharpness/exposure) שכבר קיימים ב-`photos`.

היקף המודול כאן (issue #8 / F07 בלבד):
  - `generate_layout` - F07: בונה בדיוק 15 שורות `spreads` (position 1..15)
    ו-`spread_photos` לפי אחת מ-6 התבניות הקבועות (`templates.json`). קובעת
    תמונה "דומיננטית" (מרכזית) לכל כפולה ובוחרת תבנית לפי כמות/כיוון
    התמונות שהוקצו לאותה כפולה.

**לא ממומש כאן בכוונה** (היקף F08, issue #9 - ראה גם ההערה ב-INTERFACES.md
סעיף 4.3 המתועדת כ"חוזה מיועד" לעתיד, לא קוד קיים): `regenerate_layout`,
`replace_photo_in_slot`, `change_template`. אלה דורשים החלטת-ארכיטקט מפורשת
על `spreads.version` (incrementing על "יצירה מחדש") ועל חוזה ה-snapshot/undo
(סעיף 6 ב-INTERFACES.md) - לא "מומצאים" כאן בלי תיאום, לפי ההנחיה המפורשת
ל-issue הזה.

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
