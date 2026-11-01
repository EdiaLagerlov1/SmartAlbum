"""
SmartAlbum - selection-engine: F03 (סימון חובה/החרגה, issue #4) + F05
(תיוג שלבי אירוע וקיבוץ, issue #6).

ממשק מחייב מוגדר ב-INTERFACES.md סעיף 4.2 - אין להמציא חתימות אחרות.
מודול זה *לא* מעבד פיקסלים ולא נוגע ב-HTTP - אלה קוראים לו דרך `main.py`
(עטיפה דקה 1:1, ללא לוגיקה נוספת ב-route handler, לפי הכלל בסעיף 1).

היקף המודול כאן:
  - `set_flag`         - כתיבת photos.flag בודד (none/must/exclude). F03.
  - `check_must_limit` - ספירת תמונות flag='must' בפרויקט + בדיקת הגבלת 60. F03.
  - `tag_event_stages` - קיבוץ לפי זמן (taken_at) לשלבי אירוע + סימון
                          "לבדיקה ידנית" כש-timestamp חסר/אמינות נמוכה. F05.
  - `run_selection`    - אלגוריתם בחירה עד 60 תמונות: מכליל את כל ה-must,
                          מכבד את כל ה-exclude, מצמצם כפילויות לפי
                          similarity_group_id, ומסמן "חוסר תמונות" כש-סך
                          התמונות התקינות (לא exclude) קטן מ-15. F06.

`flag` הוא עמודה **יחידה** ב-`photos` (ראה schema.sql) עם `CHECK (flag IN
('none', 'must', 'exclude'))` - לכן טכנית אי אפשר שתמונה תהיה גם must וגם
exclude בו-זמנית (אין שני בוליאניים נפרדים). ה-CHECK constraint ב-DB הוא
הגנת-עומק; האימות העיקרי (כדי להחזיר הודעת שגיאה ברורה בעברית, לא
sqlite3.IntegrityError גולמי) הוא כאן, ב-`set_flag`, ונאכף שוב ב-`main.py`
(400 מסודר) - F03 מחייב דחייה "ב-UI/API layer, לא רק בלוגיקה".
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
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


# ===========================================================================
# F05 (issue #6): תיוג שלבי אירוע וקיבוץ על בסיס זמן ותוכן.
# ===========================================================================
#
# פער ידוע מה-pipeline העליון (image-pipeline, F02): נכון לעכשיו
# `image_pipeline/api.py` (`process_upload`) **אינו ממלא את `photos.taken_at`**
# מה-EXIF של הקובץ (אין קריאת תג `DateTimeOriginal`/`DateTime` בקוד הקיים -
# נבדק ב-issue #6) - `taken_at` נשאר NULL לכל התמונות כרגע. זה תועד למטה
# כ-TODO ל-image-pipeline (לא מתוקן כאן - selection-engine לא "מתקן" מודול
# אחר, רק בונה את tag_event_stages כך שהיא מתמודדת בחוכמה עם NULL גורף
# ולא קורסת, ראה fallback "NO_TIMESTAMP" מתחת).
#
# "קיבוץ... על בסיס זמן **ותוכן**" (embeddings/metadata, לפי האפיון): אין
# עדיין embeddings/ניתוח-תוכן שנחשף ע"י image-pipeline (`group_similar_photos`
# ב-F04 מחשב רק perceptual hash לצמצום *כפילויות*, לא embedding סמנטי
# ל"שלב אירוע"). לכן המימוש כאן הוא **מבוסס-זמן בלבד** (gap-based clustering
# על `taken_at`) - זה "אלגוריתם סביר" לפי האפיון, אבל חלק "התוכן" הוא TODO
# עתידי (ראה גם TODO ל-image-pipeline למעלה) - מתועד כפער פתוח, לא ממומש
# ב-heuristic מומצא (לדוגמה זיהוי-פנים/סצנה) שחורג מהיקף המודול הזה.

# שמות שלבי אירוע - קבוע מתועד (ראה גם INTERFACES.md סעיף 4.2 ו-schema.sql
# ההערה על `event_stage`: "למשל 'getting_ready' | 'ceremony' | 'party'...").
# הסדר כאן הוא גם הסדר הכרונולוגי שבו הם מוקצים לקבוצות-הזמן שהתגלו
# (קבוצה 0 הכרונולוגית ביותר -> EVENT_STAGE_SEQUENCE[0], וכו'). אם מתגלות
# יותר קבוצות-זמן מאשר יש שמות ברשימה - קבוצות העודפות **לא** מקבלות שם
# מומצא; הן נשארות event_stage=NULL עם ביטחון נמוך (ראה _OVERFLOW_CONFIDENCE
# למטה) - "לא מנחש" גם כשיש יותר מדי קבוצות-זמן, לא רק כש-timestamp חסר.
EVENT_STAGE_SEQUENCE: tuple[str, ...] = ("getting_ready", "ceremony", "reception", "party")

# סף ביטחון (0..1) - מתחתיו photos.needs_stage_review = 1 (לפי schema.sql/
# INTERFACES.md: "ביטחון נמוך -> UI מציג 'לבדיקה', לא כעובדה"). ערך-אפיון
# נבחר (0.5) - שינוי שלו הוא עדכון ל-INTERFACES.md לפני שינוי כאן.
EVENT_STAGE_CONFIDENCE_THRESHOLD = 0.5

# "פער גדול" בין שתי תמונות עוקבות (לפי taken_at) = גבול בין שלבי אירוע
# (gap-based clustering, לפי האפיון: "פער זמן גדול... = גבול בין שלבים").
# 2 שעות נבחר אמפירית/סבירות-תחום: תוך שלב אירוע בודד (הכנות/טקס/קבלה/
# ריקודים) צילומים נוטים להיות דקות בודדות זה מזה; המעבר בין שלבים בחתונה
# טיפוסית הוא לפחות שעה-שעתיים (נסיעה/הפסקה/החלפת מיקום). ערך-אפיון -
# שינוי דורש עדכון INTERFACES.md קודם.
EVENT_STAGE_GAP_SECONDS = 2 * 60 * 60

# ביטחון כש-taken_at חסר/לא ניתן לפענוח - "לא מנחש" (לפי האפיון), אז מינימלי.
_NO_TIMESTAMP_CONFIDENCE = 0.0

# ביטחון כש-כל התמונות (עם timestamp) מתקבצות לקבוצת-זמן אחת בודדת - אין
# "פערים" שמאפשרים לחלק לשלבים, כך שאין בסיס אמין לקבוע event_stage (נשאר
# NULL) - ביטחון מתחת לסף, מסומן לבדיקה.
_SINGLE_CLUSTER_CONFIDENCE = 0.3

# ביטחון לקבוצת-זמן עודפת (אין לה שם פנוי ב-EVENT_STAGE_SEQUENCE) - ראה
# ההערה למעלה על "לא מנחש גם כשיש יותר מדי קבוצות".
_OVERFLOW_CLUSTER_CONFIDENCE = 0.3

# ביטחון לקבוצת-זמן "רגילה" (יש לה שם פנוי ברשימה): גבוה אם יש בה כמה
# תמונות (תבנית צפיפות-זמן ברורה לשלב), מעט נמוך יותר (אבל עדיין מעל הסף)
# אם היא מכילה תמונה בודדת בלבד (ייתכן outlier, אבל עדיין יש גבולות-זמן
# ברורים משני הצדדים שמבדילים אותה כשלב נפרד).
_MIN_CLUSTER_SIZE_FOR_HIGH_CONFIDENCE = 2
_HIGH_CONFIDENCE = 0.85
_SINGLETON_IN_SEQUENCE_CONFIDENCE = 0.6


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_job_status(
    db: sqlite3.Connection,
    project_id: str,
    stage: str,
    status: Literal["pending", "running", "done", "failed"],
    error_reason: str | None = None,
) -> None:
    """Upsert שורת processing_jobs עבור (project_id, stage) - תואם בדיוק את
    `image_pipeline.api._set_job_status` (אותה סכמה/upsert), אבל מוחזק כאן
    בנפרד ולא מיובא משם - selection-engine ו-image-pipeline הם מודולים
    עצמאיים (ראה INTERFACES.md סעיף 1: "תקשורת היא קריאת פונקציה ישירה",
    לא תלות הדדית בין מודולי-עיבוד עצמם; שניהם תלויים רק ב-db/schema המשותפים).
    """
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


def _parse_taken_at(raw: str | None) -> datetime | None:
    """מפענח `photos.taken_at` (ISO-8601, לפי schema.sql) ל-`datetime`.
    מחזיר None גם כש-NULL/ריק וגם כשהמחרוזת קיימת אבל לא ניתנת לפענוח
    (לא מעלה exception - "לא קורסת", לפי דרישת ה-issue) - שני המקרים
    מטופלים אותו דבר (ללא timestamp אמין)."""
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def tag_event_stages(project_id: str, db: sqlite3.Connection) -> None:
    """F05 (issue #6): מקבץ את תמונות הפרויקט לשלבי אירוע על בסיס `taken_at`
    (זמן; ראה הערת "זמן ותוכן" בראש הסעיף - חלק ה"תוכן" הוא TODO פתוח כל
    עוד image-pipeline לא חושף embeddings/ניתוח-תוכן). כותב `photos.event_stage`
    + `photos.event_stage_confidence` לכל תמונת הפרויקט, ו-`photos.needs_stage_review`
    (1 כש-timestamp חסר/לא ניתן לפענוח, או כש-confidence < EVENT_STAGE_CONFIDENCE_THRESHOLD).

    אלגוריתם (gap-based clustering, ללא ניחוש):
      1. תמונות בלי `taken_at` תקין (NULL או מחרוזת שלא ניתנת לפענוח) -
         מקבלות event_stage=NULL, confidence=0.0, needs_stage_review=1 -
         **לא** fallback לפי `uploaded_at`/`id` בניסיון "לנחש" שלב; ה"fallback"
         היחיד הוא שהן ממשיכות לכל אורך הפרויקט (לא עוצר את תיוג שאר
         התמונות, וממשיך לעבוד גם אם *כל* התמונות בפרויקט חסרות timestamp -
         ראה בדיקה ייעודית ל"אין קריסה").
      2. שאר התמונות (עם `taken_at` תקין) ממוינות כרונולוגית ומחולקות
         לקבוצות-זמן רציפות: פער > `EVENT_STAGE_GAP_SECONDS` בין שתי תמונות
         עוקבות (במיון כרונולוגי) = גבול בין קבוצות (שלבים פוטנציאליים).
      3. קבוצה כרונולוגית ראשונה/שנייה/... מקבלת שם מ-`EVENT_STAGE_SEQUENCE`
         לפי הסדר (positional mapping - לא ניתוח תוכן, ראה הערה בראש הסעיף);
         קבוצה עודפת (יותר קבוצות מאשר שמות) נשארת event_stage=NULL
         (ביטחון נמוך, מסומנת לבדיקה) - "לא מנחש" שם.
      4. אם כל התמונות עם timestamp מתקבצות לקבוצה אחת בלבד (אין אף פער
         גדול) - אין בסיס לחלק לשלבים, כך שכולן מקבלות event_stage=NULL
         בביטחון נמוך (מסומנות לבדיקה), ולא שם מנוחש.

    "מאפשר למשתמשת לשנות סדר כפולות ידנית" (חלק מה-issue): זו בעיקרה F07/
    layout-engine (ראה עדכון ב-INTERFACES.md סעיף 4.2) - הפונקציה הזו לא
    נועלת כלום; `event_stage`/`needs_stage_review` הם הצעה הניתנת לשינוי,
    לא עובדה נעולה (כמו F08 לגבי run_selection).
    """
    _set_job_status(db, project_id, "tagging", "running")
    try:
        rows = db.execute(
            "SELECT id, taken_at FROM photos WHERE project_id = ? ORDER BY uploaded_at ASC, id ASC",
            (project_id,),
        ).fetchall()

        timestamped: list[tuple[str, datetime]] = []
        for row in rows:
            dt = _parse_taken_at(row["taken_at"])
            if dt is None:
                db.execute(
                    """UPDATE photos SET event_stage = NULL, event_stage_confidence = ?,
                       needs_stage_review = 1 WHERE id = ?""",
                    (_NO_TIMESTAMP_CONFIDENCE, row["id"]),
                )
            else:
                timestamped.append((row["id"], dt))

        if timestamped:
            timestamped.sort(key=lambda item: item[1])

            # --- gap-based clustering: פער > EVENT_STAGE_GAP_SECONDS = גבול ---
            clusters: list[list[tuple[str, datetime]]] = [[timestamped[0]]]
            for prev_item, curr_item in zip(timestamped, timestamped[1:]):
                gap_seconds = (curr_item[1] - prev_item[1]).total_seconds()
                if gap_seconds > EVENT_STAGE_GAP_SECONDS:
                    clusters.append([])
                clusters[-1].append(curr_item)

            if len(clusters) <= 1:
                # אין אף פער גדול - אין בסיס לחלק לשלבים (ראה docstring, 4).
                for photo_id, _dt in timestamped:
                    db.execute(
                        """UPDATE photos SET event_stage = NULL, event_stage_confidence = ?,
                           needs_stage_review = 1 WHERE id = ?""",
                        (_SINGLE_CLUSTER_CONFIDENCE, photo_id),
                    )
            else:
                for idx, cluster in enumerate(clusters):
                    if idx < len(EVENT_STAGE_SEQUENCE):
                        stage_name: str | None = EVENT_STAGE_SEQUENCE[idx]
                        confidence = (
                            _HIGH_CONFIDENCE
                            if len(cluster) >= _MIN_CLUSTER_SIZE_FOR_HIGH_CONFIDENCE
                            else _SINGLETON_IN_SEQUENCE_CONFIDENCE
                        )
                    else:
                        stage_name = None
                        confidence = _OVERFLOW_CLUSTER_CONFIDENCE

                    needs_review = 1 if confidence < EVENT_STAGE_CONFIDENCE_THRESHOLD else 0
                    for photo_id, _dt in cluster:
                        db.execute(
                            """UPDATE photos SET event_stage = ?, event_stage_confidence = ?,
                               needs_stage_review = ? WHERE id = ?""",
                            (stage_name, confidence, needs_review, photo_id),
                        )

        db.commit()
        _set_job_status(db, project_id, "tagging", "done")
    except Exception as exc:
        db.rollback()
        _set_job_status(db, project_id, "tagging", "failed", str(exc))
        raise


# ===========================================================================
# F06 (issue #7): אלגוריתם בחירה - עד 60 תמונות לאלבום.
# ===========================================================================
#
# חוזה מחייב (ראה INTERFACES.md סעיף 4.2, ועדכון ה-architect שם מ-issue #5
# "כלל חסימת-יצירה"):
#   - `run_selection` קורא ל-`check_must_limit` כצעד **ראשון** - אם
#     `must_count > MUST_LIMIT (60)`, מחזיר status="too_many_must" **בלי**
#     לכתוב `selected_for_album`/`selection_order` בכלל (לא "לוקח את כל ה-60
#     הראשונים" - זה היה דורס בחירה ידנית/תמונות must בלי התרעה; המשתמשת
#     חייבת לתקן בחירה ולהריץ מחדש, לפי F03).
#   - אחרת: מכליל את כל ה-must (עד 60 - מובטח ע"י ה-check למעלה), מכבד את
#     כל ה-exclude (מסונן לגמרי, לא מגיע אפילו למאגר המועמדות), ומצמצם
#     כפילויות לפי similarity_group_id (F04) בין שאר התמונות (flag='none')
#     עד למכסה (60 - מספר ה-must).
#   - אם סך התמונות התקינות (flag != 'exclude', כולל must+none) בפרויקט
#     קטן מ-MIN_VALID_PHOTOS (15) - status="too_few_photos", גם בלי לכתוב
#     כלום (לא "בחירה חלקית שקטה" - לפי האפיון: "מציגים בקשה להוסיף תמונות").
#
# אות האיכות לדירוג/בחירת-נציג-מקבוצה: `sharpness_score`/`exposure_score`
# (F04 - "המלצה בלבד, לעולם לא עילה למחיקה אוטומטית"). השימוש כאן **אינו**
# "מחיקה" - אף תמונה לא נפסלת/נמחקת בגלל ציון נמוך; האלגוריתם רק מדרג בין
# מועמדות *תקינות* (לא must, לא exclude) כשיש יותר מדי כדי להחליט על סדר
# עדיפות למכסה המוגבלת (60) ועל מי מייצג קבוצת-כפילויות - תמונה לא
# "נפסלת", רק לא נבחרת הפעם; זו המלצה הניתנת לשינוי ידני אחר-כך (F08),
# לא נעילה. sharpness_score הוא ה-key הראשי (Laplacian-variance, לא
# מנורמל 0..1 - משמש כסדר-יחסי בין תמונות פרויקט אחד, לא כסף אבסולוטי),
# exposure_score (0..1) הוא tie-breaker שני. תמונות בלי ציון (NULL, למשל
# אם image-pipeline לא הצליח לחשב) מקבלות המפתח הנמוך ביותר (-1.0) - לא
# "מומצא" ציון נייטרלי - כך שהן נדרגות אחרונות אבל עדיין יכולות להיבחר אם
# אין מועמדות טובות יותר ויש מקום במכסה.
MIN_VALID_PHOTOS = 15


def _quality_sort_key(row: sqlite3.Row) -> tuple[float, float]:
    """מפתח דירוג איכות (גבוה יותר = מועדף יותר) - ראה הערת-הראש מעל.
    (sharpness_score, exposure_score), NULL -> -1.0 (לא ציון מומצא)."""
    sharpness = row["sharpness_score"] if row["sharpness_score"] is not None else -1.0
    exposure = row["exposure_score"] if row["exposure_score"] is not None else -1.0
    return (sharpness, exposure)


def _effective_datetime(row: sqlite3.Row) -> datetime:
    """זמן "אפקטיבי" לסדר כרונולוגי מוצע (`selection_order`): `taken_at`
    (F05) אם ניתן לפענוח, אחרת `uploaded_at` (NOT NULL לפי schema.sql, כך
    שיש תמיד נפילה תקינה - לא "מנחש", רק מחליף אות-זמן חסרה באות-זמן אחרת
    שקיימת בוודאות). datetime naive (בלי timezone) מנורמל ל-UTC כדי שלא
    תהיה השוואת naive/aware שמעלה TypeError במיון."""
    dt = _parse_taken_at(row["taken_at"]) or _parse_taken_at(row["uploaded_at"])
    if dt is None:
        # לא אמור לקרות (uploaded_at הוא NOT NULL ותמיד ISO-8601 תקין בפועל),
        # אבל נפילה בטוחה בלי קריסה אם בכל זאת קרה (למשל בטסט עם ערך משובש).
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class SelectionResult:
    status: Literal["ok", "too_many_must", "too_few_photos"]
    selected_photo_ids: list[str]
    message: str | None


def run_selection(project_id: str, db: sqlite3.Connection) -> SelectionResult:
    """F06 (issue #7): בוחרת עד MUST_LIMIT (60) תמונות לאלבום.

    אלגוריתם (לפי INTERFACES.md סעיף 4.2):
      1. `check_must_limit` כצעד ראשון - `must_count > 60` -> status=
         "too_many_must", **בלי** לכתוב כלום (לא נוגעת ב-selected_for_album/
         selection_order הקיימים - "יצירה" חסומה, לא "בחירה חלקית").
      2. תמונות flag='exclude' מסוננות לגמרי - לעולם לא מועמדות, אפילו לא
         נכנסות לספירת "תקינות" לצורך הכלל הזה (הן *לא* תקינות, בהגדרה).
      3. סך התמונות התקינות (flag != 'exclude', כולל must+none) בפרויקט
         קטן מ-MIN_VALID_PHOTOS (15) -> status="too_few_photos", גם בלי
         לכתוב כלום - "מציגים בקשה להוסיף תמונות", לא בחירה חלקית שקטה.
      4. אחרת: כל תמונות ה-must (flag='must') נכללות תמיד (מובטח <= 60
         ע"י שלב 1). יתר המכסה (60 - מספר ה-must) מתמלא ממועמדות flag='none',
         אחרי צמצום כפילויות: תוך קבוצת similarity_group_id (F04) נבחר נציג
         אחד בלבד - הציון הגבוה ביותר (`_quality_sort_key`) - ושאר חברי
         הקבוצה לא נכללים אוטומטית. קבוצה שיש לה כבר נציג בין ה-must
         (המשתמשת "הוסיפה במפורש" תמונה מאותה קבוצה ע"י סימון must) מדולגת
         כליל בשלב הזה - אין טעם להוסיף עוד "כפילות" אוטומטית מקבוצה
         שהמשתמשת כבר כיסתה במפורש; אם המשתמשת מסמנת must לכמה תמונות
         מאותה קבוצה - כולן נכללות (זה בדיוק ה-"אלא אם המשתמשת הוסיפה
         תמונה נוספת במפורש מאותה קבוצה" באפיון - המנגנון הקיים היחיד
         ל"הוספה מפורשת" הוא דגל must, אין פרמטר override נפרד בחוזה).
         תמונות בלי similarity_group_id (NULL - "תמונה יחידה", לפי
         image_pipeline.group_similar_photos) נחשבות מועמדות עצמאיות,
         לא מדוללות מול אף תמונה אחרת.
      5. תוצאה סופית (must + נבחרות-none) ממוינת כרונולוגית (`_effective_datetime`)
         ונכתבת ל-`photos.selected_for_album=1` + `photos.selection_order`
         (1-based, לפי הסדר הזה) - זו *המלצה* ניתנת לשינוי ידני אחר-כך
         (F08), לא נעילה. כל שאר תמונות הפרויקט מקבלות
         `selected_for_album=0`/`selection_order=NULL` (דריסה של בחירה קודמת
         אם `run_selection` רץ בעבר - "הרצה מחדש" מחליפה את הבחירה הקודמת
         במלואה, לא מצרפת לה).
    """
    _set_job_status(db, project_id, "selection", "running")

    must_check = check_must_limit(project_id, db)
    if not must_check.ok:
        _set_job_status(db, project_id, "selection", "failed", "too_many_must")
        return SelectionResult(
            status="too_many_must",
            selected_photo_ids=[],
            message=(
                f"יותר מ-{MUST_LIMIT} תמונות מסומנות 'חובה' ({must_check.must_count}) - "
                "יש לתקן את הבחירה (להוריד סימון 'חובה' מחלק מהתמונות) לפני יצירת האלבום"
            ),
        )

    rows = db.execute(
        """
        SELECT id, flag, similarity_group_id, sharpness_score, exposure_score,
               taken_at, uploaded_at
        FROM photos WHERE project_id = ?
        """,
        (project_id,),
    ).fetchall()

    valid_rows = [row for row in rows if row["flag"] != "exclude"]
    if len(valid_rows) < MIN_VALID_PHOTOS:
        _set_job_status(db, project_id, "selection", "failed", "too_few_photos")
        return SelectionResult(
            status="too_few_photos",
            selected_photo_ids=[],
            message=(
                f"יש רק {len(valid_rows)} תמונות תקינות (לא מוחרגות) בפרויקט - "
                f"נדרשות לפחות {MIN_VALID_PHOTOS} כדי ליצור אלבום. יש להוסיף תמונות"
            ),
        )

    must_rows = [row for row in valid_rows if row["flag"] == "must"]
    other_rows = [row for row in valid_rows if row["flag"] != "must"]

    # קבוצות similarity_group_id שכבר מיוצגות ע"י תמונת must - מדולגות כליל
    # משלב צמצום-הכפילויות האוטומטי (ראה (4) ב-docstring).
    must_group_ids = {row["similarity_group_id"] for row in must_rows if row["similarity_group_id"]}

    groups: dict[str, list[sqlite3.Row]] = {}
    standalone: list[sqlite3.Row] = []
    for row in other_rows:
        gid = row["similarity_group_id"]
        if not gid:
            standalone.append(row)
        elif gid in must_group_ids:
            continue  # קבוצה שכבר מכוסה ע"י must - לא מוסיפים נציג אוטומטי
        else:
            groups.setdefault(gid, []).append(row)

    candidates = standalone + [max(members, key=_quality_sort_key) for members in groups.values()]
    candidates.sort(key=_quality_sort_key, reverse=True)

    remaining_capacity = max(0, MUST_LIMIT - len(must_rows))
    chosen_others = candidates[:remaining_capacity]

    selected_rows = must_rows + chosen_others
    selected_rows.sort(key=_effective_datetime)
    selected_ids = [row["id"] for row in selected_rows]

    # "הרצה מחדש" מחליפה את הבחירה הקודמת במלואה - לא מצרפת אליה.
    db.execute(
        "UPDATE photos SET selected_for_album = 0, selection_order = NULL WHERE project_id = ?",
        (project_id,),
    )
    for order, photo_id in enumerate(selected_ids, start=1):
        db.execute(
            "UPDATE photos SET selected_for_album = 1, selection_order = ? WHERE id = ?",
            (order, photo_id),
        )
    db.commit()
    _set_job_status(db, project_id, "selection", "done")

    return SelectionResult(status="ok", selected_photo_ids=selected_ids, message=None)
