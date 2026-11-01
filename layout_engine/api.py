"""
SmartAlbum - layout-engine: F07 (יצירת 15 כפולות והתאמת תבנית, issue #8).

ממשק מחייב מוגדר ב-INTERFACES.md סעיף 4.3 - אין להמציא חתימות אחרות.
מודול זה *לא* מממש את לוגיקת הבחירה (selection-engine, F06) ו*לא* מעבד
פיקסלים (image-pipeline) - רק סידור תמונות שכבר נבחרו ב-grid של 15 כפולות,
לפי הנתונים (orientation/sharpness/exposure) שכבר קיימים ב-`photos`.

היקף המודול כאן (issue #8 / F07 + issue #9 / F08):
  - `generate_layout` - F07: בונה בדיוק 15 שורות `spreads` (position 1..15)
    ו-`spread_photos` לפי אחת מ-6 התבניות הקבועות (`templates.json`). קובעת
    תמונה "דומיננטית" (מרכזית) לכל כפולה ובוחרת תבנית לפי כמות/כיוון
    התמונות שהוקצו לאותה כפולה.
  - `regenerate_layout` - F08 (issue #9): "יצירה מחדש" - מריץ שוב את אותו
    אלגוריתם (משותף עם `generate_layout` דרך `_run_layout_algorithm`), שולף
    את `selected_photo_ids` בעצמו מה-DB (לא מקבל כפרמטר - ראה docstring),
    ומקפיץ את `spreads.version` ל-`max(version קיים לפרויקט) + 1`. הקורא
    (frontend-rtl) **חייב** לקבל אישור מפורש ולקרוא ל-
    `export_agent.snapshot_structure` *לפני* קריאה לכאן (סעיף 6 ב-
    INTERFACES.md) - לא נאכף כאן בקוד (זו אחריות ה-UI/ה-caller, לא ניתנת
    לאכיפה מתוך הפונקציה עצמה בלי state נוסף).
  - `replace_photo_in_slot` - F08 (issue #9): מחליפה תמונה בסלוט בודד בכפולה
    קיימת בתמונה אחרת מתוך תמונות הפרויקט שאינן `flag='exclude'`.
  - `change_template` - F08 (issue #9): משנה את התבנית של כפולה קיימת,
    ומתאימה (reconcile) את שיבוץ התמונות הקיימות לסלוטים של התבנית החדשה.

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


def _assign_slots_partial(
    template: dict, group: list[str], dominant_id: str
) -> list[tuple[int, str, bool]]:
    """כמו `_assign_slots`, אבל **סובלת פחות תמונות מכמות הסלוטים** בתבנית
    (שימוש ב-`change_template`, F08 - "יותר סלוטים מתמונות" הוא מקרה חוקי
    כש-slot_count של התבנית החדשה גדול ממספר התמונות שהיו משובצות קודם).
    הסלוט הדומיננטי משובץ תמיד (מחייב ש-`dominant_id` יהיה ב-`group`);
    שאר הסלוטים משובצים לפי סדר index, כל עוד יש תמונות נותרות ב-`group`
    - סלוטים לא-דומיננטיים בלי תמונה פנויה **נשארים ריקים** (לא שורת
    `spread_photos`), במקום להעלות `StopIteration` כמו `_assign_slots`
    (שמניחה len(group) == slot_count, תקף רק ל-generate_layout/
    regenerate_layout שבהן קבוצת התמונות כבר נבנתה בדיוק במידה הזו)."""
    slots = sorted(template["slots"], key=lambda s: s["index"])
    others = iter(pid for pid in group if pid != dominant_id)
    assignments: list[tuple[int, str, bool]] = []
    for slot in slots:
        if slot["dominant"]:
            assignments.append((slot["index"], dominant_id, True))
        else:
            photo_id = next(others, None)
            if photo_id is not None:
                assignments.append((slot["index"], photo_id, False))
    return assignments


# ===========================================================================
# אלגוריתם משותף ל-generate_layout (F07) ול-regenerate_layout (F08) - אותו
# היגיון בדיוק, רק ה-`version` שנכתב ל-spreads.version משתנה בין הקוראים
# (1 תמיד ל-generate_layout הראשוני; max(גרסה קיימת)+1 ל-regenerate_layout).
# אין שכפול לוגיקה בין שתי הפונקציות הציבוריות - שתיהן קוראות לכאן.
# ===========================================================================
def _run_layout_algorithm(
    project_id: str,
    selected_photo_ids: list[str],
    db: sqlite3.Connection,
    explicit_duplicates: set[str] | None,
    version: int,
) -> LayoutResult:
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
               VALUES (?, ?, ?, ?, ?, ?)""",
            (spread_id, project_id, position, template_id, version, now),
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
# F07 (issue #8): generate_layout.
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
    להעביר רשימה מתוקנת-ידנית.

    אלגוריתם: ראה `_run_layout_algorithm` (משותף עם `regenerate_layout`,
    F08). ההבדל היחיד בין שתי הפונקציות הציבוריות: `generate_layout` כותבת
    `spreads.version = 1` תמיד (יצירה ראשונית); `regenerate_layout` מקפיצה
    את הגרסה (ראה docstring שם). כל קריאה (כולל לראשונה) **מחליפה** את
    הכפולות הקודמות של הפרויקט במלואה - לא מצרפת (בדיוק כמו `run_selection`
    ל-F06).
    """
    return _run_layout_algorithm(
        project_id, selected_photo_ids, db, explicit_duplicates, version=1
    )


# ===========================================================================
# F08 (issue #9): regenerate_layout / replace_photo_in_slot / change_template.
# ===========================================================================
def _max_spread_version(project_id: str, db: sqlite3.Connection) -> int:
    row = db.execute(
        "SELECT MAX(version) AS max_version FROM spreads WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    max_version = row["max_version"] if row else None
    return max_version if max_version is not None else 0


def regenerate_layout(project_id: str, db: sqlite3.Connection, **kwargs) -> LayoutResult:
    """F08 (issue #9): "יצירה מחדש" - ראה INTERFACES.md סעיף 4.3/6 לחוזה
    המלא. **הקורא (frontend-rtl) חייב לקבל אישור מפורש מהמשתמשת ולקרוא
    ל-`export_agent.snapshot_structure(project_id, db)` לפני הקריאה לכאן** -
    זה *לא* נאכף כאן בקוד (אין state ייעודי ל"האם אושר" - זו אחריות ה-UI
    flow, לפי החוזה המתועד; הפונקציה הזו היא רק המימוש של "בצע את היצירה
    מחדש בפועל", לא ה-gate של האישור).

    שונה מ-`generate_layout` בשני דברים בלבד:
      1. **שולפת `selected_photo_ids` בעצמה מה-DB** (לא מקבלת כפרמטר) - אותה
         שאילתה מחייבת בדיוק כמו ב-F06/F07 (`SELECT id FROM photos WHERE
         project_id=? AND selected_for_album=1 ORDER BY selection_order`,
         INTERFACES.md סעיף 4.2) - "רשימת ה-photo_id הרשמית היחידה". קריאה
         מחדש ל-selection_engine כאן (לא רק main.py) כי regenerate_layout
         *היא* הכניסה היחידה ל"יצירה מחדש" - אין route handler שמתווך,
         לפי החתימה המתועדת (`**kwargs`, בלי `selected_photo_ids`).
      2. **מקפיצה את `spreads.version`**: `max(spreads.version הקיים
         לפרויקט) + 1` (0 אם אין שורות spreads קודמות לפרויקט - גרסה 1,
         בדיוק כמו generate_layout ראשוני). נקבע *לפני* המחיקה/כתיבה (לא
         ניתן לקרוא MAX אחרי שה-DELETE כבר רץ). כל 15 הכפולות של ההרצה הזו
         מקבלות את אותה גרסה (version הוא "גרסת הפריסה כולה", לא מונה
         per-spread עצמאי - עקבי עם "regenerate מחליף את כל 15 הכפולות
         במכה אחת").

    `**kwargs` נתמך לפי החתימה המתועדת ב-INTERFACES.md (`regenerate_layout(
    project_id, db, **kwargs)`); הפרמטר האופציונלי היחיד שנתמך בפועל כאן
    הוא `explicit_duplicates` (`set[str] | None`, אותה סמנטיקה כמו
    `generate_layout`) - מועבר דרך kwargs כי החתימה המחייבת לא מפרטת אותו
    כפרמטר שמי נפרד.
    """
    explicit_duplicates = kwargs.get("explicit_duplicates")

    selected_photo_ids = [
        row["id"]
        for row in db.execute(
            """SELECT id FROM photos WHERE project_id = ? AND selected_for_album = 1
               ORDER BY selection_order""",
            (project_id,),
        ).fetchall()
    ]

    next_version = _max_spread_version(project_id, db) + 1

    return _run_layout_algorithm(
        project_id, selected_photo_ids, db, explicit_duplicates, version=next_version
    )


def _slot_lookup(template: dict) -> dict[int, dict]:
    return {slot["index"]: slot for slot in template["slots"]}


def replace_photo_in_slot(
    spread_id: str, slot_index: int, new_photo_id: str, db: sqlite3.Connection
) -> bool:
    """F08 (issue #9): מחליפה את התמונה בסלוט בודד בכפולה קיימת.

    **קונבנציית הדיווח על כשל** (תואמת את הדפוס הקיים ב-`selection_engine.
    api.set_flag`: מחזירה `bool` - `True` בהצלחה, `False` על כשל-ולידציה
    "נורמלי"/צפוי, **בלי** להעלות exception - לפי חתימת
    `-> bool` המתועדת ב-INTERFACES.md סעיף 4.3, שאין בה ערוץ שגיאה נפרד
    (לא dataclass עם status/message כמו ב-LayoutResult). `False` מוחזר
    כש:
      - `spread_id` לא קיים.
      - `slot_index` לא קיים בתבנית הנוכחית של הכפולה הזו (לא בטווח
        0..slot_count-1 של `TEMPLATES[spread.template_id]`).
      - `new_photo_id` לא קיים, או שייך לפרויקט אחר (לא לפרויקט של
        הכפולה הזו).
      - `new_photo_id` הוא `flag='exclude'` (האפיון: "תמונות הפרויקט שלא
        הוחרגו" בלבד).
      - `new_photo_id` כבר משובץ לסלוט *אחר* באותה כפולה עצמה - היה
        שובר את `UNIQUE(spread_id, photo_id)` ב-schema.sql; מטופל כ-כשל
        ולידציה "נורמלי" (לא `sqlite3.IntegrityError` גולמי), לא נתמך
        בהיקף הזה (העברת תמונה בין שני סלוטים דורשת שתי פעולות/swap ייעודי
        - לא ממומש כאן, אין לזה דרישה מפורשת ב-issue).
      - שגיאות DB "לא נורמליות" (constraint violations לא-צפויות אחרות)
        עדיין מועלות כ-exception רגיל - רק כשלי-ולידציה *צפויים* מוחזרים
        כ-`False`, לפי אותה קונבנציה כמו `set_flag`.

    **אין דה-דופ גלובלי לכל האלבום כאן בכוונה**: "כל תמונה פעם אחת בכל
    האלבום, אלא אם המשתמשת הוסיפה אותה שוב במפורש" (F07/האפיון) - הקריאה
    ל-`replace_photo_in_slot` *היא* בדיוק "פעולת ה-UI הנפרדת" של הוספה
    מפורשת (לא ברירת מחדל של אלגוריתם) - לכן לא חוסמים כאן תמונה שכבר
    מופיעה בכפולה *אחרת* בפרויקט; רק באותה כפולה עצמה (ראה מעלה), בגלל
    constraint ה-DB.
    """
    spread_row = db.execute(
        "SELECT project_id, template_id FROM spreads WHERE id = ?", (spread_id,)
    ).fetchone()
    if spread_row is None:
        return False

    template = TEMPLATES.get(spread_row["template_id"])
    if template is None:
        return False
    slots = _slot_lookup(template)
    if slot_index not in slots:
        return False

    photo_row = db.execute(
        "SELECT project_id, flag FROM photos WHERE id = ?", (new_photo_id,)
    ).fetchone()
    if photo_row is None:
        return False
    if photo_row["project_id"] != spread_row["project_id"]:
        return False
    if photo_row["flag"] == "exclude":
        return False

    existing_in_spread = db.execute(
        "SELECT slot_index FROM spread_photos WHERE spread_id = ? AND photo_id = ?",
        (spread_id, new_photo_id),
    ).fetchone()
    if existing_in_spread is not None and existing_in_spread["slot_index"] != slot_index:
        return False

    is_dominant = 1 if slots[slot_index]["dominant"] else 0

    db.execute(
        "DELETE FROM spread_photos WHERE spread_id = ? AND slot_index = ?",
        (spread_id, slot_index),
    )
    db.execute(
        """INSERT INTO spread_photos (spread_id, photo_id, slot_index, is_dominant)
           VALUES (?, ?, ?, ?)""",
        (spread_id, new_photo_id, slot_index, is_dominant),
    )
    db.execute(
        "UPDATE spreads SET updated_at = ? WHERE id = ?", (_now_iso(), spread_id)
    )
    db.commit()
    return True


def change_template(spread_id: str, new_template_id: str, db: sqlite3.Connection) -> bool:
    """F08 (issue #9): משנה את התבנית של כפולה קיימת ומתאימה (reconcile) את
    שיבוץ התמונות הקיימות לסלוטים של התבנית החדשה.

    **קונבנציית כשל**: אותה קונבנציה כמו `replace_photo_in_slot` (ו-
    `set_flag`) - `bool`, `False` על כשל-ולידציה צפוי, בלי exception:
      - `spread_id` לא קיים -> `False`.
      - `new_template_id` לא קיים ב-`TEMPLATES` (קטלוג `templates.json`) ->
        `False`.

    **כלל ההתאמה (reconciliation) כש-slot_count משתנה** - מתועד כאן כי
    האפיון לא קובע אותו באופן מפורש, רק "להתאים בצורה סבירה" (ראה ה-issue):
    עקבי עם ההיגיון הקיים ב-`_assign_slots`/`_pick_dominant` של F07 -
    "התמונה הדומיננטית נשארת דומיננטית אם אפשר":
      1. שולפים את שיבוץ התמונות הקיים (`spread_photos`, ממוין לפי
         `slot_index` הישן), ומזהים איזו תמונה הייתה דומיננטית (אם הייתה).
      2. בונים רשימה מסודרת: התמונה שהייתה דומיננטית קודם (אם קיימת)
         ראשונה, ואז שאר התמונות לפי סדר ה-slot_index הישן שלהן (המשכיות
         עם `_assign_slots`: "דומיננטי ראשון, שאר הסלוטים לפי סדר index").
      3. אם לתבנית החדשה יש **פחות** סלוטים מכמות התמונות המשובצות -
         התמונות *העודפות* (מסוף הרשימה הממוינת משלב 2, אחרי שנשמרו
         ה-slot_count הראשונות - שהדומיננטית הקודמת, אם הייתה, היא תמיד
         בהן כי היא ראשונה) **נשמטות מהכפולה הזו** (מחיקת שורת
         `spread_photos` שלהן) - לא נמחקות מהפרויקט/מהאלבום, רק מהכפולה
         הזו; המשתמשת יכולה לשבץ אותן בכפולה אחרת דרך `replace_photo_in_slot`
         בעתיד. זו ברירת המחדל הפשוטה והדטרמיניסטית ביותר - "שמור את מי
         שהיה חשוב (דומיננטי) + הראשונים לפי סדר, השמט את הנותרים".
      4. אם לתבנית החדשה יש **יותר** סלוטים מכמות התמונות המשובצות -
         הסלוטים הנוספים (הלא-דומיננטיים) **נשארים ריקים** (אין שורת
         `spread_photos` בשבילם) - אין כאן תמונה "להמציא" לשבץ אוטומטית;
         המשתמשת משלימה אותם ידנית אחר-כך דרך `replace_photo_in_slot`.
      5. התמונה ש"ראשונה" ברשימה הממוינת (הדומיננטית הקודמת, אם הייתה,
         אחרת התמונה שהייתה ב-slot_index 0 הישן) משובצת לסלוט ה-`dominant:
         true` של התבנית החדשה (יש בדיוק אחד כזה בכל תבנית בקטלוג) - "התמונה
         הדומיננטית נשארת דומיננטית אם אפשר", כנדרש ב-issue.
      6. אם לכפולה לא הייתה אף תמונה משובצת קודם (מקרה-קצה, לא אמור לקרות
         אחרי `generate_layout`/`regenerate_layout` תקינים) - התבנית
         מתעדכנת בלבד, `spread_photos` נשארת ריקה.

    לא משנה את `spreads.version` - שינוי תבנית הוא עדכון-עריכה ידני בודד על
    כפולה אחת, לא "יצירה מחדש" של כל 15 הכפולות (`version` מקופץ רק
    ב-`regenerate_layout`, לפי `schema.sql`: "version מקדם כל 'יצירה
    מחדש'"). מעדכן את `spreads.updated_at` - עקבי עם ההערה ב-`schema.sql`
    על העמודה הזו ("לכל עדכון: יצירה/swap/שינוי תבנית").
    """
    new_template = TEMPLATES.get(new_template_id)
    if new_template is None:
        return False

    spread_row = db.execute(
        "SELECT project_id FROM spreads WHERE id = ?", (spread_id,)
    ).fetchone()
    if spread_row is None:
        return False

    existing_rows = db.execute(
        """SELECT photo_id, slot_index, is_dominant FROM spread_photos
           WHERE spread_id = ? ORDER BY slot_index""",
        (spread_id,),
    ).fetchall()

    dominant_photo_id = next(
        (row["photo_id"] for row in existing_rows if row["is_dominant"]), None
    )
    ordered_photo_ids = [row["photo_id"] for row in existing_rows]
    if dominant_photo_id is not None:
        ordered_photo_ids = [dominant_photo_id] + [
            pid for pid in ordered_photo_ids if pid != dominant_photo_id
        ]

    new_slot_count = len(new_template["slots"])
    kept_photo_ids = ordered_photo_ids[:new_slot_count]

    db.execute("DELETE FROM spread_photos WHERE spread_id = ?", (spread_id,))

    if kept_photo_ids:
        new_dominant_id = kept_photo_ids[0]
        for slot_index, photo_id, is_dominant in _assign_slots_partial(
            new_template, kept_photo_ids, new_dominant_id
        ):
            db.execute(
                """INSERT INTO spread_photos (spread_id, photo_id, slot_index, is_dominant)
                   VALUES (?, ?, ?, ?)""",
                (spread_id, photo_id, slot_index, 1 if is_dominant else 0),
            )

    db.execute(
        "UPDATE spreads SET template_id = ?, updated_at = ? WHERE id = ?",
        (new_template_id, _now_iso(), spread_id),
    )
    db.commit()
    return True
