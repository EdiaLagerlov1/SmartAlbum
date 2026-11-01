"""
SmartAlbum - export-agent: `snapshot_structure` (F08, issue #9) +
`export_album`/`ExportResult` (F09, issue #10).

ממשק מחייב מוגדר ב-INTERFACES.md סעיף 4.4 - אין להמציא חתימות אחרות.

היקף המודול כאן:
  - `snapshot_structure` (F08, issue #9) - כותבת את מצב ה-`spreads`/
    `spread_photos` הנוכחי של הפרויקט ל-JSON ב-`storage.export_structure_prev_path`,
    *לפני* שה-caller (frontend-rtl) קורא ל-`layout_engine.regenerate_layout`
    (ראה INTERFACES.md סעיף 6, זרימת F08 "יצירה מחדש" - שלבים 1-2). זה "גיבוי
    לפני שינוי הרסני" - לא snapshot היסטורי מרובה-גרסאות (נדרס בכל קריאה,
    בדיוק כמו ש-`album_structure_prev.json` מתואר ב-storage.py/INTERFACES.md
    כ"הגיבוי" היחיד, לא רשימת-גיבויים). **לא נגענו בלוגיקה הזו** - ללא תלות
    ב-PDF rendering בכלל.
  - `export_album` (F09, issue #10) - מייצא את האלבום הסופי: מרנדר PDF
    (עמוד אחד = כפולה אחת, לפי `spreads`/`spread_photos` הנוכחיים) וכותב
    `album_structure.json` (מבנה האלבום הסופי, לשחזור - לא ה-"_prev" של F08).
    לפני רינדור, בודקת שני תנאי-תקינות על מצב הבחירה (ראה docstring
    `export_album`) - אם מופרים, **לא** נוצר PDF, רק עדכון `processing_jobs`
    stage='export' ל-failed.

> **הערת-יישום (export-agent, issue #10) - תחליף ל-WeasyPrint**: סעיף 1
> וסעיף 4.4 של INTERFACES.md (וה-docstring המקורי כאן) מתעדים WeasyPrint
> כמרנדר המתוכנן ("מרנדר את אותו HTML/CSS של ה-preview"). בדיוק כמו הערת
> ה-architect על FastAPI/httpx (סעיף 1) והערת image-pipeline על `imagehash`
> (סעיף 4.1) - בסביבת הפיתוח/CI הנוכחית PyPI חסום ו-WeasyPrint **לא ניתן
> להתקין בפועל** (אין גם את תלויות-המערכת שלו - Pango/Cairo). לכן המימוש
> כאן משתמש ב-**reportlab** (מותקן מראש, pure-Python, לא תלוי בבינארי
> מערכת) כדי לשמור על "PDF זהה ל-preview" בלי HTML renderer כלל: במקום
> לרנדר HTML/CSS, `export_album` מחשבת ישירות את גיאומטריית ה-slot-ים
> (קואורדינטות השבר x/y/w/h מ-`layout_engine/templates.json`, **אותו
> קטלוג תבניות בדיוק** שה-UI/layout-engine קוראים - לא לוגיקת-פריסה
> חדשה) וממירה אותן לנקודות PDF, ואז מצייר כל תמונה לתוך המלבן שלה עם
> **center-crop** (שקול ל-`object-fit: cover` ב-`static/style.css`,
> `.spread-photo-slot img`) באמצעות Pillow (crop+resize) לפני ציור
> ל-canvas (`reportlab.pdfgen.canvas`, דרך `ImageReader`). הסדר/השיבוץ
> לכל כפולה זהה ל-`GET /api/projects/<id>/spreads` (ממוין לפי `slot_index`)
> - אותו מקור-אמת (`spread_photos`), לא חישוב נפרד. **TODO**: אם WeasyPrint
> (ותלויות המערכת שלו) יותקנו בעתיד (סביבת production עם גישת רשת) - אפשר
> לשקול מעבר לרינדור HTML/CSS אמיתי של אותו template שה-UI מציג, אבל זה
> שינוי ל-INTERFACES.md לפני קוד, כרגיל.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

import storage
from layout_engine.api import TEMPLATES

# ===========================================================================
# גודל עמוד ה-PDF: עמוד אחד = כפולה אחת (לא "2 עמודים לכל כפולה" - ראה
# INTERFACES.md/הנחיית ה-issue). יחס-רוחב/גובה קבוע, נבחר שרירותית (אין
# דרישת-אפיון לגודל פיזי מדויק - אישור-לדפוס/רזולוציה/שוליים הם O02,
# מחוץ להיקף, ראה .claude/agents/export-agent.md). ביחידות נקודה (pt,
# 1/72 אינץ') - יחס רוחב:גובה 1.6:1, קרוב ליחס הרוחב/גובה הממוצע של
# כפולת אלבום פתוחה.
# ===========================================================================
SPREAD_PAGE_WIDTH: float = 1600.0
SPREAD_PAGE_HEIGHT: float = 1000.0

# אותו גבול בדיוק כמו selection_engine.MUST_LIMIT / layout_engine.
# MAX_SELECTED_FOR_LAYOUT (60) - "סימן שסטטוס הבחירה לא תקין" אם נחרג
# ממנו *בפועל* באלבום שמיועד לייצוא (ספירה לפי photo_id ייחודיים שמשובצים
# בפועל ב-spread_photos של הפרויקט, לא selected_for_album - ה-PDF מייצא
# את מה שבאמת בכפולות, לא את מה ש"נבחר" תאורטית לפני ה-layout).
MAX_PHOTOS_IN_ALBUM: int = 60


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
    selection_engine/layout_engine (מוחזק בנפרד, לא מיובא - מודולים עצמאיים,
    ראה INTERFACES.md סעיף 1)."""
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


# ===========================================================================
# F09 (issue #10): export_album.
# ===========================================================================
@dataclass
class ExportResult:
    status: Literal["ok", "error"]
    pdf_path: str | None
    structure_path: str | None
    message: str | None


def _load_spreads_for_export(project_id: str, db: sqlite3.Connection) -> list[dict]:
    """שולפת את `spreads`/`spread_photos` הנוכחיים של הפרויקט, ממוינים לפי
    `position`/`slot_index` - **אותו סדר בדיוק** כמו `GET /api/projects/
    <id>/spreads` (שה-UI מציג) ו-`snapshot_structure` מעלה - מקור-אמת יחיד
    (אין חישוב סדר נפרד כאן). כל רשומת photo כוללת גם `original_path`
    (לרינדור - לא נכתב ל-JSON הסופי, ראה `_structure_dict`)."""
    spread_rows = db.execute(
        """SELECT id, position, template_id, version, updated_at
           FROM spreads WHERE project_id = ? ORDER BY position""",
        (project_id,),
    ).fetchall()

    spreads: list[dict] = []
    for row in spread_rows:
        photo_rows = db.execute(
            """SELECT sp.slot_index, sp.photo_id, sp.is_dominant, p.original_path
               FROM spread_photos sp JOIN photos p ON sp.photo_id = p.id
               WHERE sp.spread_id = ? ORDER BY sp.slot_index""",
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
                        "original_path": p["original_path"],
                    }
                    for p in photo_rows
                ],
            }
        )
    return spreads


def _check_album_selection_guard(project_id: str, db: sqlite3.Connection) -> str | None:
    """בדיקת-תקינות לפני ייצוא (ראה .claude/agents/export-agent.md, "כללים"):
    "אל תיצור PDF אם יש תמונות must שלא נכללו, או אם המספר הכולל חורג
    מ-60 - זה סימן שסטטוס הבחירה לא תקין". מחזירה הודעת שגיאה בעברית אם
    מופר תנאי, אחרת None.

    "נכללו"/"המספר הכולל" נבדקים לפי מה שבאמת משובץ ב-`spread_photos` של
    הפרויקט כרגע (photo_id ייחודיים) - זה מה שבאמת יודפס ל-PDF, לא
    `photos.selected_for_album` (שהוא קלט ל-layout-engine, לא הפלט הסופי
    שה-export-agent אחראי עליו)."""
    must_rows = db.execute(
        "SELECT id FROM photos WHERE project_id = ? AND flag = 'must'", (project_id,)
    ).fetchall()
    must_ids = {row["id"] for row in must_rows}

    included_rows = db.execute(
        """SELECT DISTINCT sp.photo_id FROM spread_photos sp
           JOIN spreads s ON sp.spread_id = s.id
           WHERE s.project_id = ?""",
        (project_id,),
    ).fetchall()
    included_ids = {row["photo_id"] for row in included_rows}

    missing_must = must_ids - included_ids
    if missing_must:
        return (
            f"יש {len(missing_must)} תמונות המסומנות 'חובה' שלא נכללו באלבום "
            "- מצב הבחירה אינו תקין, יש להריץ מחדש את יצירת/עריכת הכפולות "
            "לפני ייצוא"
        )

    if len(included_ids) > MAX_PHOTOS_IN_ALBUM:
        return (
            f"האלבום מכיל {len(included_ids)} תמונות - יותר מ-"
            f"{MAX_PHOTOS_IN_ALBUM} המותר; מצב הבחירה אינו תקין"
        )

    return None


def _center_crop_cover(img: Image.Image, target_w: float, target_h: float) -> Image.Image:
    """Center-crop ל-aspect ratio של `target_w`/`target_h`, שקול ל-CSS
    `object-fit: cover` (`static/style.css`, `.spread-photo-slot img`):
    התמונה ממלאת את כל המלבן בלי "ריבוטינג" (letterbox) - העודף (ברוחב או
    בגובה, לפי מי מהשניים "רחב יותר" מהיחס המבוקש) נחתך משני הצדדים
    באופן שווה (מרכז)."""
    target_aspect = target_w / target_h
    src_w, src_h = img.size
    src_aspect = src_w / src_h

    if src_aspect > target_aspect:
        new_w = max(1, min(src_w, round(src_h * target_aspect)))
        left = (src_w - new_w) // 2
        box = (left, 0, left + new_w, src_h)
    else:
        new_h = max(1, min(src_h, round(src_w / target_aspect)))
        top = (src_h - new_h) // 2
        box = (0, top, src_w, top + new_h)
    return img.crop(box)


def _draw_spread_page(c: pdf_canvas.Canvas, spread: dict) -> None:
    """מצייר כפולה אחת (עמוד PDF אחד) - כל תמונה למלבן-הסלוט שלה, לפי
    קואורדינטות השבר (x/y/w/h) של `layout_engine/templates.json` (אותו
    קטלוג תבניות בדיוק שה-UI/layout-engine קוראים דרך `TEMPLATES`, ראה
    הערת-המודול העליונה - לא לוגיקת-פריסה חדשה)."""
    template = TEMPLATES.get(spread["template_id"])
    slot_lookup = {slot["index"]: slot for slot in template["slots"]} if template else {}

    for photo in spread["photos"]:
        slot = slot_lookup.get(photo["slot_index"])
        if slot is None:
            # template_id/slot_index לא תקין (מצב-קצה לא צפוי אחרי
            # generate_layout/regenerate_layout תקינים) - לא עוצר את שאר
            # התמונות/הכפולה (עקבי עם "כשל בבודד לא עוצר אחרים", F01/F10).
            continue

        rect_w = slot["w"] * SPREAD_PAGE_WIDTH
        rect_h = slot["h"] * SPREAD_PAGE_HEIGHT
        rect_x = slot["x"] * SPREAD_PAGE_WIDTH
        # slot["y"] הוא שבר-מרחק-מהחלק-העליון (כמו CSS top/%) - origin של
        # reportlab הוא הפינה השמאלית-תחתונה, לכן הופכים.
        rect_y = SPREAD_PAGE_HEIGHT - (slot["y"] * SPREAD_PAGE_HEIGHT) - rect_h

        original_path = photo.get("original_path")
        if not original_path or not os.path.exists(original_path):
            # קובץ מקור חסר על הדיסק - לא קורס את כל הייצוא (מצב-קצה לא
            # צפוי: originals נכתבים פעם אחת ולא נמחקים, ראה storage.py).
            continue

        with Image.open(original_path) as img:
            rgb_img = img.convert("RGB")
            cropped = _center_crop_cover(rgb_img, rect_w, rect_h)
            buf = io.BytesIO()
            cropped.save(buf, format="JPEG", quality=90)
            buf.seek(0)
            c.drawImage(
                ImageReader(buf),
                rect_x,
                rect_y,
                width=rect_w,
                height=rect_h,
                preserveAspectRatio=False,
                mask=None,
            )

    c.showPage()


def _render_pdf(spreads: list[dict], pdf_path: Path) -> None:
    """עמוד PDF אחד = כפולה אחת (`spreads` ממוין כבר לפי `position` ע"י
    `_load_spreads_for_export`) - לא "2 עמודים לכל כפולה"."""
    c = pdf_canvas.Canvas(str(pdf_path), pagesize=(SPREAD_PAGE_WIDTH, SPREAD_PAGE_HEIGHT))
    for spread in spreads:
        _draw_spread_page(c, spread)
    c.save()


def _structure_dict(project_id: str, spreads: list[dict]) -> dict:
    """מבנה ה-JSON הסופי (`album_structure.json`) - **לא** אותו קובץ כמו
    `album_structure_prev.json` של `snapshot_structure` (זה ה"גיבוי לפני
    שינוי הרסני"; זה הפלט הסופי של ייצוא), אבל אותה צורת-JSON (עקבי עם
    התיעוד ב-INTERFACES.md: position/template_id/version/updated_at +
    photos עם slot_index/photo_id/is_dominant per spread)."""
    return {
        "project_id": project_id,
        "exported_at": _now_iso(),
        "spreads": [
            {
                "id": s["id"],
                "position": s["position"],
                "template_id": s["template_id"],
                "version": s["version"],
                "updated_at": s["updated_at"],
                "photos": [
                    {
                        "slot_index": p["slot_index"],
                        "photo_id": p["photo_id"],
                        "is_dominant": p["is_dominant"],
                    }
                    for p in s["photos"]
                ],
            }
            for s in spreads
        ],
    }


def export_album(project_id: str, db: sqlite3.Connection) -> ExportResult:
    """F09 (issue #10): מייצא את האלבום הסופי - PDF לתצוגה/ביקורת
    (`storage.export_pdf_path`) + קובץ מבנה האלבום הסופי ל-JSON
    (`storage.export_structure_path`).

    **PDF זהה ל-preview** (חובה קריטית, ראה .claude/agents/export-agent.md):
    אותן תמונות, אותו סדר (`spread_photos` ממוין לפי `slot_index`, כמו
    `GET /api/projects/<id>/spreads`), אותה פריסה (קואורדינטות השבר
    x/y/w/h של `layout_engine/templates.json` - אותו קטלוג תבניות בדיוק
    שה-UI/layout-engine קוראים) ואותו חיתוך (center-crop, שקול ל-CSS
    `object-fit: cover`). עמוד PDF אחד = כפולה אחת. **לא** מחשב מחדש
    פריסה בלוגיקה נפרדת - רק ממיר את הגיאומטריה הקיימת לנקודות PDF
    ומצייר (ראה הערת-המודול העליונה על WeasyPrint -> reportlab).

    **בדיקת-תקינות לפני ייצוא** (ראה `_check_album_selection_guard`):
    אם יש תמונות `flag='must'` שלא נכללות בפועל ב-`spread_photos` של
    הפרויקט, או אם סך התמונות הייחודיות המשובצות בפועל עולה על
    `MAX_PHOTOS_IN_ALBUM` (60) - **לא** נוצר PDF/JSON כלל; `processing_jobs`
    stage='export' מתעדכן ל-`failed` עם `error_reason`, ומוחזר
    `ExportResult(status="error", pdf_path=None, structure_path=None,
    message=...)`. זו הגנה על מצב-DB לא-תקין (סימן שסטטוס הבחירה/ה-layout
    לא תקין) - לא "תיקון" שקט של הבחירה.

    בהצלחה: `processing_jobs` stage='export' -> `done`, ומוחזר
    `ExportResult(status="ok", pdf_path=<נתיב>, structure_path=<נתיב>,
    message=None)` (אותה קונבנציית pending->running->done/failed בדיוק
    כמו image_pipeline/selection_engine/layout_engine, ראה `_set_job_status`).

    פרויקט בלי כפולות כלל (`spreads` ריק) **לא** נחשב כשל - נכתב PDF ריק
    (0 עמודים) ו-JSON עם `"spreads": []` (עקבי עם ההתנהגות המתועדת של
    `snapshot_structure` לאותו מקרה-קצה); אין תמונות `must` שלא נכללו
    ואין סך-תמונות>60 במצב הזה, כך שהבדיקה עוברת בלי לחסום.
    """
    _set_job_status(db, project_id, "export", "running")

    guard_message = _check_album_selection_guard(project_id, db)
    if guard_message is not None:
        _set_job_status(db, project_id, "export", "failed", guard_message)
        return ExportResult(
            status="error", pdf_path=None, structure_path=None, message=guard_message
        )

    spreads = _load_spreads_for_export(project_id, db)

    pdf_path = storage.export_pdf_path(project_id)
    _render_pdf(spreads, pdf_path)

    structure_path = storage.export_structure_path(project_id)
    structure = _structure_dict(project_id, spreads)
    structure_path.write_text(
        json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _set_job_status(db, project_id, "export", "done")

    return ExportResult(
        status="ok",
        pdf_path=str(pdf_path),
        structure_path=str(structure_path),
        message=None,
    )
