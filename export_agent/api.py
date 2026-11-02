"""
SmartAlbum - export-agent: F08 (`snapshot_structure`, issue #9) + F09
(`export_album`/`ExportResult`, issue #10).

ממשק מחייב מוגדר ב-INTERFACES.md סעיף 4.4 - אין להמציא חתימות אחרות.

היקף המודול כאן:
  - `snapshot_structure` (F08, issue #9) - כותבת dump JSON טהור של מצב
    ה-`spreads`/`spread_photos` הנוכחי, *לפני* `layout_engine.regenerate_layout`
    (ראה INTERFACES.md סעיף 6). אין תלות ב-WeasyPrint/רינדור HTML בכלל -
    נותר ללא שינוי מהמימוש שנכתב ב-issue #9.
  - `export_album` (F09, issue #10) - מרנדר PDF זהה ל-preview ב-UI
    (WeasyPrint, ראה INTERFACES.md סעיף 1/4.4) + כותב `album_structure.json`.

** הערת-סביבה קריטית (ראה גם INTERFACES.md עדכון F09) **: WeasyPrint
(רשום ב-`requirements.txt`, הבחירה המחייבת לפי ה-architect) **אינו מותקן
בסביבת ה-CI/הפיתוח האוטומטית הזו** - PyPI חסום (אותה מגבלה בדיוק כמו
`imagehash` ב-F04/image-pipeline - ראה INTERFACES.md סעיף 4.1). **בשונה
מ-F04, אין כאן מימוש-fallback עם ספרייה אחרת** - WeasyPrint הוא הבחירה
המחייבת בחוזה (INTERFACES.md סעיף 1: "ייצוא PDF: WeasyPrint, מרנדר את אותו
HTML/CSS שה-preview ב-UI מציג") ולא ניתן להחליף אותה ללא אישור architect
מפורש (שינוי ממשק/תלות). לכן:
  - ה-import מוגן (`try/except ImportError`) כך שכל שאר המודול (ולידציה,
    בניית HTML/CSS, JSON, endpoint HTTP) עובד/נטען/נבדק *בלי* WeasyPrint
    מותקן בפועל.
  - קריאת ה-render עצמה מבודדת בפונקציה פרטית אחת, `_render_pdf_bytes` -
    הנקודה היחידה במודול שבאמת קוראת ל-`weasyprint.HTML(...).write_pdf(...)`.
  - אם `export_album` נקראת כש-WeasyPrint לא מותקן - היא מחזירה
    `ExportResult(status="error", ...)` עם הודעה מפורשת שמבדילה בין "לא
    נוצרה פריסה" (`layout_not_generated`) ל"מנוע ה-PDF לא מותקן בסביבה הזו"
    (`weasyprint_unavailable`) - **לא** exception/traceback גולמי, עקבי עם
    קונבנציית ה"status object" הקיימת בקודבייס הזה (ראה
    `selection_engine.run_selection`/`layout_engine.generate_layout`).
  - **לא נבדק בפועל בסביבה הזו** שה-PDF-bytes שמיוצרים ע"י WeasyPrint
    נראים/נפתחים נכון (אין התקנה כאן כדי להריץ את זה) - ראה טסטים עם
    `@pytest.mark.skipif(weasyprint is None, ...)` ב-`tests/test_export_agent.py`,
    והערה מפורשת ב-PR/INTERFACES.md. כל שאר הלוגיקה (ולידציה, בניית
    HTML/CSS, מבנה JSON, נתיבים, processing_jobs, ה-endpoint ב-main.py)
    *נבדקה בפועל* בלי להזדקק ל-WeasyPrint.

** הערה קריטית שנייה (זהות ל-preview, לא "התאמות" בזמן ייצוא) **: תצוגת
ה-preview בפועל ב-UI (`static/app.js` `renderSpreadCard` + `static/style.css`
`.spread-*`, F08) **אינה** משתמשת בגיאומטריית ה-`x/y/w/h` המתועדת ב-
`layout_engine/templates.json` לפריסה אבסולוטית לפי-תבנית - היא מציגה כל
כפולה כשורת תיבות-סלוט (flex-wrap) ממוינות לפי `slot_index`, כל תיבה בגודל
קבוע (רוחב 160px, תמונה 90px, `object-fit: cover`), עם תווית מיוחדת
(`.dominant`) לסלוט הדומיננטי. ה-HTML/CSS שנבנה כאן (`_build_album_html`)
**משכפל את זה בדיוק** (לא את הגיאומטריה המתוכננת-מראש של `templates.json`
שלא מיושמת בפועל במסך) - כדי שהדרישה הקריטית "PDF זהה ל-preview" תהיה
נכונה *בפועל*, לא רק מוצהרת. אם/כש-F08 ה-UI ישתנה ליישם את גיאומטריית
ה-slots בפועל - המודול הזה (ו-ה-docstring הזה) יצטרכו עדכון מתואם.
"""

from __future__ import annotations

import html as _html_module
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import storage
from layout_engine import api as layout_engine

try:
    import weasyprint  # type: ignore
except ImportError:  # PyPI חסום בסביבה הזו - ראה docstring המודול מעלה.
    weasyprint = None  # type: ignore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# F08 (issue #9): snapshot_structure - ללא שינוי מהמימוש הקיים.
# ===========================================================================
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
# F09 (issue #10): export_album - ייצוא PDF זהה ל-preview + album_structure.json.
# ===========================================================================

# תואם בדיוק את layout_engine.MAX_SELECTED_FOR_LAYOUT (60 = 15 כפולות * עד 4
# תמונות) - שימוש-חזרה באותו קבוע קיים, לא המצאת ערך כפול שעלול לסטות ממנו.
MAX_EXPORT_PHOTOS = layout_engine.MAX_SELECTED_FOR_LAYOUT

_PROCESSING_STAGE = "export"


def _set_job_status(
    db: sqlite3.Connection,
    project_id: str,
    stage: str,
    status: Literal["pending", "running", "done", "failed"],
    error_reason: str | None = None,
) -> None:
    """Upsert שורת processing_jobs - אותה תבנית בדיוק כמו image_pipeline/
    selection_engine/layout_engine (מוחזק בנפרד, לא מיובא - מודולים
    עצמאיים, ראה INTERFACES.md סעיף 1). דורש ש-`project_id` כבר קיים
    בטבלת `projects` (FK) - לא נקרא לפני בדיקת-קיום הפרויקט."""
    import uuid

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
class ExportResult:
    status: Literal["ok", "error"]
    pdf_path: str | None
    structure_path: str | None
    message: str | None


def _project_exists(project_id: str, db: sqlite3.Connection) -> bool:
    row = db.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row is not None


def _fetch_spreads_with_photos(project_id: str, db: sqlite3.Connection) -> list[dict]:
    """אותה שאילתה/צורת-נתונים בדיוק כמו `GET /api/projects/<id>/spreads`
    ב-`main.py` (`list_spreads`, F08) - כדי שאין סיכון ש-PDF/structure.json
    יציגו משהו שונה ממה שה-preview מציג. ההבדל היחיד: מחזיר `thumb_path`
    (נתיב קובץ מוחלט בדיסק, נדרש ל-WeasyPrint) ו-`width`/`height` במקום
    `thumbnail_url` (נתיב HTTP, רלוונטי רק לדפדפן) - שתי השאילתות קוראות את
    אותן שורות DB באותו סדר בדיוק (`ORDER BY position` / `ORDER BY slot_index`).
    """
    spread_rows = db.execute(
        """SELECT id, position, template_id, version, updated_at
           FROM spreads WHERE project_id = ? ORDER BY position""",
        (project_id,),
    ).fetchall()

    spreads: list[dict] = []
    for row in spread_rows:
        template = layout_engine.TEMPLATES.get(row["template_id"]) or {}
        photo_rows = db.execute(
            """SELECT sp.slot_index, sp.photo_id, sp.is_dominant,
                      p.original_filename, p.thumb_path, p.width, p.height
               FROM spread_photos sp JOIN photos p ON sp.photo_id = p.id
               WHERE sp.spread_id = ? ORDER BY sp.slot_index""",
            (row["id"],),
        ).fetchall()
        spreads.append(
            {
                "spread_id": row["id"],
                "position": row["position"],
                "template_id": row["template_id"],
                "slot_count": template.get("slot_count"),
                "version": row["version"],
                "updated_at": row["updated_at"],
                "photos": [
                    {
                        "slot_index": p["slot_index"],
                        "photo_id": p["photo_id"],
                        "is_dominant": bool(p["is_dominant"]),
                        "original_filename": p["original_filename"],
                        "thumb_path": p["thumb_path"],
                        "width": p["width"],
                        "height": p["height"],
                    }
                    for p in photo_rows
                ],
            }
        )
    return spreads


def _validate_export_readiness(
    project_id: str, db: sqlite3.Connection, spreads: list[dict]
) -> tuple[str, str] | None:
    """בדיקת-שפיות על מצב הבחירה/הפריסה *לפני* ייצוא - לא "התאמות" בזמן
    ייצוא, אלא סימן שמשהו נכשל בשלבים קודמים (F06/F07/F08) שצריך לתקן שם,
    לא לייצא קובץ שגוי. מחזירה `(error_reason_code, hebrew_message)` או
    `None` אם תקין:

    - סך התמונות (distinct) המשובצות בכל 15 הכפולות חורג מ-60
      (`MAX_EXPORT_PHOTOS`, תואם `layout_engine.MAX_SELECTED_FOR_LAYOUT`).
    - תמונה המסומנת `flag='must'` בפרויקט שאינה כלולה באף כפולה (סימן
      שה-layout לא כולל את כל ה-must, או שנמחקה מסלוט ב-F08 replace/change
      בלי שהוחלפה ל-must אחרת).
    """
    included_photo_ids = {p["photo_id"] for spread in spreads for p in spread["photos"]}

    if len(included_photo_ids) > MAX_EXPORT_PHOTOS:
        return (
            "too_many_photos_in_album",
            f"מספר התמונות הכולל באלבום ({len(included_photo_ids)}) חורג "
            f"מ-{MAX_EXPORT_PHOTOS} - מצב הבחירה/הפריסה אינו תקין, לא ניתן לייצא PDF",
        )

    must_rows = db.execute(
        "SELECT id FROM photos WHERE project_id = ? AND flag = 'must'", (project_id,)
    ).fetchall()
    missing_must = [row["id"] for row in must_rows if row["id"] not in included_photo_ids]
    if missing_must:
        return (
            "must_photos_missing_from_album",
            f"{len(missing_must)} תמונות המסומנות 'חובה' אינן כלולות באלבום - "
            "מצב הבחירה/הפריסה אינו תקין, לא ניתן לייצא PDF",
        )

    return None


def _escape(text: str | None) -> str:
    return _html_module.escape(text or "")


def _photo_image_src(thumb_path: str | None) -> str:
    """`file://` URI לתמונת ה-thumbnail (אותו קובץ שה-preview ב-UI מציג
    דרך `/api/projects/<id>/photos/<photo_id>/thumbnail` - ראה הערה
    ב-docstring המודול על "למה thumbnail ולא original"). מחזיר מחרוזת ריקה
    אם אין thumb_path (למשל שורת-טסט בלי קובץ אמיתי) - `<img>` עם src ריק,
    לא קורס."""
    if not thumb_path:
        return ""
    try:
        return Path(thumb_path).resolve().as_uri()
    except (ValueError, OSError):
        return ""


_ALBUM_CSS = """
@page { size: A4 landscape; margin: 14mm; }
body { font-family: Arial, Helvetica, sans-serif; direction: rtl; color: #111827; }
h1 { font-size: 20px; margin-bottom: 4px; }
.spread-card { border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 14px;
               margin-bottom: 14px; page-break-inside: avoid; }
.spread-card-header { display: flex; flex-direction: row; align-items: center; gap: 12px;
                       font-weight: 600; font-size: 13px; }
.spread-card-header .muted { font-weight: 400; color: #4b5563; }
.spread-photos { display: flex; flex-direction: row; flex-wrap: wrap; gap: 10px; margin: 10px 0; }
.spread-photo-slot { border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px; width: 160px;
                      background: #fafafa; }
.spread-photo-slot img { width: 100%; height: 90px; object-fit: cover; border-radius: 4px;
                          background: #eee; display: block; }
.slot-label { font-size: 11px; color: #4b5563; display: block; margin-top: 5px; }
.slot-label.dominant { color: #2c3e6b; font-weight: 600; }
"""


def _build_album_html(project_id: str, spreads: list[dict]) -> tuple[str, str]:
    """בונה HTML+CSS שמשכפלים *בדיוק* את מודל-התצוגה בפועל של ה-preview
    ב-UI (`static/app.js` `renderSpreadCard` + `static/style.css` `.spread-*`,
    F08) - ראה הערה מפורטת ב-docstring המודול על למה *לא* נעשה שימוש
    בגיאומטריית ה-x/y/w/h המתוכננת-מראש של `templates.json`. מחזיר
    `(html, css)` - שניהם strings, לא תלוי ב-WeasyPrint בכלל (ניתן לבדוק
    בלי WeasyPrint מותקן)."""
    parts: list[str] = [
        "<html><head><meta charset='utf-8' /></head><body>",
        f"<h1>אלבום החתונה - פרויקט {_escape(project_id)}</h1>",
    ]
    for spread in spreads:
        parts.append('<div class="spread-card">')
        parts.append(
            '<div class="spread-card-header">'
            f'<span>כפולה {spread["position"]}</span>'
            f'<span class="muted">תבנית נוכחית: {_escape(spread["template_id"])}</span>'
            f'<span class="muted">גרסה: {spread["version"]}</span>'
            "</div>"
        )
        parts.append('<div class="spread-photos">')
        for photo in spread["photos"]:
            dominant_cls = " dominant" if photo["is_dominant"] else ""
            dominant_txt = " - דומיננטי" if photo["is_dominant"] else ""
            img_src = _photo_image_src(photo["thumb_path"])
            parts.append(
                '<div class="spread-photo-slot">'
                f'<img src="{_escape(img_src)}" alt="{_escape(photo["original_filename"])}" />'
                f'<span class="slot-label{dominant_cls}">סלוט {photo["slot_index"]}{dominant_txt}</span>'
                "</div>"
            )
        parts.append("</div></div>")
    parts.append("</body></html>")
    return "".join(parts), _ALBUM_CSS


def _build_structure_payload(project_id: str, spreads: list[dict]) -> dict:
    """תוכן `album_structure.json` (F09) - כל המידע הדרוש לשחזור מלא של
    האלבום: סדר כפולות (`position`), `template_id`/`version` לכל כפולה,
    `photo_id`/`slot_index`/`is_dominant` לכל slot. אותו פורמט-מפתחות בדיוק
    כמו `snapshot_structure` (`album_structure_prev.json`, F08) - עקביות
    בין שני קבצי-ה-JSON ב-`exports/`, גם אם הסמנטיקה שונה (גיבוי-לפני-שינוי
    מול תוצר-ייצוא-סופי)."""
    return {
        "project_id": project_id,
        "exported_at": _now_iso(),
        "spreads": [
            {
                "id": spread["spread_id"],
                "position": spread["position"],
                "template_id": spread["template_id"],
                "version": spread["version"],
                "updated_at": spread["updated_at"],
                "photos": [
                    {
                        "slot_index": p["slot_index"],
                        "photo_id": p["photo_id"],
                        "is_dominant": p["is_dominant"],
                        "original_filename": p["original_filename"],
                    }
                    for p in spread["photos"]
                ],
            }
            for spread in spreads
        ],
    }


def _render_pdf_bytes(html: str, css: str) -> bytes:
    """העטיפה היחידה במודול מעל `weasyprint.HTML(...).write_pdf(...)` -
    מבודדת בכוונה (ראה docstring המודול) כדי שכל שאר `export_album`
    (ולידציה, בניית HTML/CSS, JSON, processing_jobs) יהיה טסטבילי/ניתן
    לייבוא בלי WeasyPrint מותקן. מעלה `RuntimeError` (לא ImportError גולם)
    אם WeasyPrint לא מותקן - `export_album` תופס את זה ומחזיר
    `ExportResult(status="error", ...)` נקי, לא traceback. **לא נבדק בפועל**
    בסביבה הזו - ראה הערת-סביבה בראש הקובץ."""
    if weasyprint is None:
        raise RuntimeError(
            "weasyprint is not installed in this environment - cannot render PDF bytes"
        )
    html_obj = weasyprint.HTML(string=html)  # type: ignore[union-attr]
    css_obj = weasyprint.CSS(string=css)  # type: ignore[union-attr]
    return html_obj.write_pdf(stylesheets=[css_obj])


def export_album(project_id: str, db: sqlite3.Connection) -> ExportResult:
    """F09 (issue #10): מרנדר את אותו HTML/CSS של ה-preview ל-PDF
    (WeasyPrint, ב-`storage.export_pdf_path(project_id)`), וכותב גם
    `album_structure.json` (ב-`storage.export_structure_path(project_id)`).

    זרימה (כל שלב "כשל" כותב `processing_jobs` שלב `'export'` כ-`failed` +
    `error_reason` קצר, ומחזיר `ExportResult(status="error", ...)` בלי
    לכתוב אף קובץ - "all or nothing", עקבי עם `run_selection`/`generate_layout`):

      1. פרויקט לא קיים -> `status="error"` מיידי, **לא** נוגע ב-
         `processing_jobs` כלל (אין שורת `projects` שה-FK מאפשר לקשור
         אליה - לא "משהו נכשל בעיבוד", זה input לא תקין).
      2. `processing_jobs` שלב `'export'` -> `running`.
      3. אין עדיין `spreads` לפרויקט (layout לא נוצר) -> `status="error"`,
         `error_reason="layout_not_generated"`.
      4. ולידציית-שפיות (`_validate_export_readiness`): סך תמונות > 60,
         או תמונת `must` שחסרה מהאלבום -> `status="error"`,
         `error_reason` לפי הבדיקה שנכשלה (למשל `"must_photos_missing_from_album"`).
      5. WeasyPrint לא מותקן בסביבה -> `status="error"`,
         `error_reason="weasyprint_unavailable"` - הודעה *נפרדת ומפורשת*
         מ-(3)/(4) (מגבלת-סביבה, לא בעיית-נתונים).
      6. רינדור ה-PDF נכשל (כל exception אחר מ-WeasyPrint עצמו) ->
         `status="error"`, `error_reason="pdf_render_error"`.
      7. הצלחה: נכתבים `album.pdf` + `album_structure.json`,
         `processing_jobs` -> `done`, `ExportResult(status="ok", ...)`.

    **לא נוגע ב-`projects.status`** - תואם בדיוק את ההחלטה הקיימת ב-
    `run_selection`/`generate_layout`/`regenerate_layout` (כולן *לא* נוגעות
    ב-`projects.status`, ראה INTERFACES.md סעיף 5.1: "סוכן שמקדם אותו...
    עושה זאת כחלק מה-issue הרלוונטי" - לא הוחלט כאן שזה issue #10, אין
    תקדים אחר שמקדם את השדה הזה, אז לא מומצא כאן בלי אישור architect).
    """
    if not _project_exists(project_id, db):
        return ExportResult(
            status="error",
            pdf_path=None,
            structure_path=None,
            message=f"פרויקט לא נמצא: {project_id}",
        )

    _set_job_status(db, project_id, _PROCESSING_STAGE, "running")

    spreads = _fetch_spreads_with_photos(project_id, db)
    if not spreads:
        message = "לא נוצרה עדיין פריסה לאלבום (יש להריץ יצירת כפולות לפני ייצוא)"
        _set_job_status(db, project_id, _PROCESSING_STAGE, "failed", "layout_not_generated")
        return ExportResult(status="error", pdf_path=None, structure_path=None, message=message)

    validation_error = _validate_export_readiness(project_id, db, spreads)
    if validation_error is not None:
        error_reason, message = validation_error
        _set_job_status(db, project_id, _PROCESSING_STAGE, "failed", error_reason)
        return ExportResult(status="error", pdf_path=None, structure_path=None, message=message)

    if weasyprint is None:
        message = (
            "מנוע ה-PDF (WeasyPrint) אינו מותקן בסביבה הנוכחית - לא ניתן לייצא PDF כרגע. "
            "זו מגבלת-סביבה (לא בעיה בנתוני האלבום) - נסו שוב בסביבה שבה WeasyPrint מותקן."
        )
        _set_job_status(db, project_id, _PROCESSING_STAGE, "failed", "weasyprint_unavailable")
        return ExportResult(status="error", pdf_path=None, structure_path=None, message=message)

    html, css = _build_album_html(project_id, spreads)
    try:
        pdf_bytes = _render_pdf_bytes(html, css)
    except Exception as exc:  # pragma: no cover - WeasyPrint לא מותקן בסביבה הזו, לא נבדק בפועל
        message = f"כשל ברינדור ה-PDF: {exc}"
        _set_job_status(db, project_id, _PROCESSING_STAGE, "failed", "pdf_render_error")
        return ExportResult(status="error", pdf_path=None, structure_path=None, message=message)

    pdf_path = storage.export_pdf_path(project_id)
    pdf_path.write_bytes(pdf_bytes)

    structure = _build_structure_payload(project_id, spreads)
    structure_path = storage.export_structure_path(project_id)
    structure_path.write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")

    _set_job_status(db, project_id, _PROCESSING_STAGE, "done")

    return ExportResult(
        status="ok", pdf_path=str(pdf_path), structure_path=str(structure_path), message=None
    )
