# SmartAlbum - INTERFACES.md

מסמך זה הוא חוזה-העל (contract) בין כל המודולים של הפרויקט. **כל agent אחר
קורא אותו לפני שהוא כותב קוד.** כל שינוי בסכמה או בממשקים עובר קודם
ה-architect ומתועד כאן - לא "כל אחד מתאים לעצמו".

קבצים נלווים (לא לשכפל את תוכנם כאן, רק להפנות):
- [`schema.sql`](./schema.sql) - מקור האמת לסכמת ה-DB.
- [`db.py`](./db.py) - `get_connection()`, `init_db()`, `delete_project()`.
- [`storage.py`](./storage.py) - פונקציות נתיב למבנה האחסון בדיסק.
- [`requirements.txt`](./requirements.txt) - תלויות Python.

---

## 1. Stack טכנולוגי

| שכבה | בחירה | הערות |
|---|---|---|
| שפה/ריצה | Python 3.11+ | כל המודולים (image-pipeline, selection-engine, layout-engine, export-agent) הם Python packages באותו תהליך/ריפו. |
| שרת API | **Flask + Werkzeug** (עודכן — ראה הערה מתחת לטבלה) | `frontend-rtl` מדבר עם השרת דרך HTTP JSON. |
| DB | SQLite, קובץ יחיד `storage/smartalbum.db` | ניגשים אליו **רק** דרך `db.get_connection()`. אין DB נפרד למודול. stdlib `sqlite3`, לא ORM - הסכמה פשוטה מספיק. |
| עיבוד תמונות | Pillow (EXIF, thumbnails, resize) + `imagehash` (perceptual hash לדמיון, F04) + Laplacian-variance/numpy לחדות | ראה `image-pipeline.md`. |
| Frontend | HTML/CSS/JS סטטי, `dir="rtl"`, ללא build step כבוי (אין React/Vue חובה; אפשר Vanilla JS או Alpine.js קל) | מוגש ע"י Flask (`static_folder`) או שרת סטטי נפרד בפיתוח. |
| ייצוא PDF | WeasyPrint, מרנדר את **אותו HTML/CSS** שה-preview ב-UI מציג | כדי להבטיח "PDF זהה ל-preview" (F09) - ראה `export-agent.md`. |
| טסטים | pytest (unit/integration), Flask `test_client()` (API), Cypress (E2E/UI - `qa-agent`) | |

> **הערה (architect, עדכון לאחר #1)**: הבחירה המקורית הייתה FastAPI+uvicorn+httpx.
> בפועל, בסביבת הפיתוח/CI האוטומטית של הפרויקט הזה אין גישת רשת להתקנת
> חבילות חדשות מ-PyPI (`pip install` ל-`fastapi`/`httpx` חסום ב-policy, לא
> תקלה זמנית), בזמן ש-`Flask`, `Werkzeug`, `starlette` ו-`uvicorn` **כבר
> מותקנים מראש**. לכן שכבת ה-API היחידה שאפשר לבנות ולהריץ-בפועל (כולל
> להריץ טסטים אמיתיים, לא רק syntax check) כאן היא **Flask**, לא FastAPI.
> Flask עונה על כל הדרישות (REST JSON, multipart upload, הגשת static/) ויש
> לו `app.test_client()` מובנה שמחליף את httpx.TestClient. **כל סוכן אחר
> שכותב endpoints (frontend-rtl ואילך) יכתוב Flask (`@app.route`,
> `request.files`, `jsonify`), לא FastAPI.** אם סביבת ריצה אחרת (למשל
> production רגיל עם גישת רשת) תרצה FastAPI בעתיד - זה requires עדכון נוסף
> של המסמך הזה לפני שינוי קוד, כרגיל.

### הרצה
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -c "import db; db.init_db()"   # יוצר storage/smartalbum.db מ-schema.sql
python3 main.py                         # מריץ Flask dev server (main.py יוקם ע"י סוכן שיבנה את שכבת ה-API)
```

### כללי תקשורת בין מודולים
- image-pipeline / selection-engine / layout-engine / export-agent הם **Python packages באותו תהליך** - תקשורת ביניהם היא **קריאת פונקציה ישירה** (import), לא HTTP בין-מודולי. ה-DB המשותף (SQLite) הוא "מקור האמת" בין שלבים, כך שכל שלב אפשר להריץ/לנסות-מחדש בנפרד (`processing_jobs`, F10).
- `frontend-rtl` הוא הצרכן היחיד שמדבר **HTTP** עם צד השרת (REST JSON דרך Flask). חתימות ה-endpoints אינן מפורטות כאן שורה-שורה (זה תפקיד סוכן ה-API/frontend לממש), אבל הן חייבות לעטוף 1:1 את הפונקציות המתועדות בסעיף 4 - לא ללוגיקה נוספת ב-route handler.
- כל הטיפוסים בסעיף 4 הם **JSON-serializable** (dict / dataclass עם `.to_dict()` / `TypedDict`) כדי שאפשר יהיה להחזיר אותם גם כ-HTTP response בלי שכבת תרגום.

---

## 2. סכמת DB - תקציר

DB יחיד: `storage/smartalbum.db`. טבלאות (פירוט מלא ועם הערות ב-`schema.sql`):

| טבלה | תפקיד | מי כותב |
|---|---|---|
| `projects` | פרויקט (אלבום) בודד | frontend-rtl (יצירה) |
| `photos` | כל תמונה + כל המידע שנצבר עליה לאורך השרשרת | image-pipeline (עמודות עיבוד), selection-engine (flag/event_stage/selected_for_album) |
| `spreads` | 15 הכפולות, `version` לכל כפולה | layout-engine |
| `spread_photos` | שיבוץ תמונה→סלוט בכפולה | layout-engine |
| `processing_jobs` | סטטוס per-stage per-project | image-pipeline, selection-engine, layout-engine, export-agent |

נקודות מפתח:
- **`photos.flag`** שדה יחיד (`none|must|exclude`) - לא שני בוליאנים, כך שלא ניתן טכנית שיהיה גם must וגם exclude בו-זמנית (F03).
- **`photos.selected_for_album` / `selection_order`** - זה הממשק שבין selection-engine ל-layout-engine.
- **`spreads.version`** - מקופץ בכל "יצירה מחדש" (F08).
- **`processing_jobs`** - שורה אחת per `(project_id, stage)`. stages: `upload, exif, thumbnail, grouping, tagging, selection, layout, export`.
- מחיקת פרויקט: **רק** דרך `db.delete_project(project_id)`.

---

## 3. מבנה אחסון בדיסק - תקציר

```
storage/
  smartalbum.db
  projects/<project_id>/
    originals/<photo_id>.<ext>        # F02
    thumbnails/<photo_id>.jpg
    exports/
      album.pdf                       # F09
      album_structure.json            # F09
      album_structure_prev.json       # גיבוי לפני יצירה-מחדש (F08)
```

גישה **רק** דרך פונקציות `storage.py`. `SMARTALBUM_STORAGE_ROOT` / `SMARTALBUM_DB_PATH` (env vars) מאפשרים להריץ טסטים מול storage/DB זמניים.

---

## 4. ממשקי מודול↔מודול

### 4.1 image-pipeline → (DB) → selection-engine

```python
# image_pipeline/api.py

@dataclass
class PhotoUploadResult:
    photo_id: str | None        # None אם הקובץ נדחה
    status: Literal["done", "failed"]
    error_reason: str | None    # F01

def process_upload(
    project_id: str,
    saved_file_path: str,
    original_filename: str,
    db: sqlite3.Connection,
) -> PhotoUploadResult:
    """F01/F02/F04: יוצר שורת photos, מעביר את הקובץ ל-storage.original_path
    (ללא שינוי בייט), מתקן כיוון ויוצר thumbnail, מחשב sharpness_score/
    exposure_score. כשל בקובץ בודד לא מעלה exception שעוצר קבצים אחרים.
    """

def group_similar_photos(project_id: str, db: sqlite3.Connection) -> None:
    """F04: מחשב similarity_group_id (perceptual hash) וכותב ל-photos.
    לא מוחק/פוסל תמונות - selection-engine מחליט על סמך זה (F06).
    """
```

**קונטרקט מול selection-engine**: אחרי `process_upload` + `group_similar_photos`, כל שורת `photos` רלוונטית מכילה `thumb_path`, `width/height`, `taken_at`, `sharpness_score`, `exposure_score`, `similarity_group_id` תקינים (או NULL אם לא ניתן לחשב).

### 4.2 selection-engine → (DB) → layout-engine

```python
# selection_engine/api.py

def set_flag(photo_id: str, flag: Literal["none", "must", "exclude"],
             db: sqlite3.Connection) -> None:
    """F03: כתיבת flag בודד."""

@dataclass
class MustCountCheck:
    must_count: int
    ok: bool                    # False אם must_count > 60

def check_must_limit(project_id: str, db: sqlite3.Connection) -> MustCountCheck:
    """F03: נקרא לפני run_selection."""

def tag_event_stages(project_id: str, db: sqlite3.Connection) -> None:
    """F05: מקבץ תמונות לפי taken_at. כתב photos.event_stage + event_stage_confidence.
    כש-taken_at חסר או confidence נמוך -> photos.needs_stage_review = 1.
    """

@dataclass
class SelectionResult:
    status: Literal["ok", "too_many_must", "too_few_photos"]
    selected_photo_ids: list[str]
    message: str | None

def run_selection(project_id: str, db: sqlite3.Connection) -> SelectionResult:
    """F06: כולל את כל ה-must, מכבד את כל ה-exclude, מצמצם כפילויות לפי similarity_group_id.
    status="too_many_must" אם >60 must. status="too_few_photos" אם סוך תמונות תקינות < 15.
    בהצלחה: כותב photos.selected_for_album=1 + selection_order לנבחרות.
    """
```

**קונטרקט מול layout-engine**: קורא `SELECT id FROM photos WHERE project_id=? AND selected_for_album=1 ORDER BY selection_order` - זו רשימת ה-photo_id הרשמית היחידה שהוא מקבל כקלט.

### 4.3 layout-engine → (DB) → export-agent

```python
# layout_engine/api.py

@dataclass
class LayoutResult:
    status: Literal["ok", "error"]
    spread_ids: list[str]
    message: str | None

def generate_layout(
    project_id: str,
    selected_photo_ids: list[str],
    db: sqlite3.Connection,
    explicit_duplicates: set[str] | None = None,
) -> LayoutResult:
    """F07: בונה בדיוק 15 שורות spreads (position 1..15) ו-spread_photos לפי אחת מ-6 התבניות.
    כל תמונה פעם אחת בכל האלבום אלא אם ב-explicit_duplicates.
    """

def regenerate_layout(project_id: str, db: sqlite3.Connection, **kwargs) -> LayoutResult:
    """F08: הקורא (frontend-rtl) חייב לקבל אישור מפורש ולקרא ל-export_agent.snapshot_structure לפני.
    """

def replace_photo_in_slot(spread_id: str, slot_index: int, new_photo_id: str,
                           db: sqlite3.Connection) -> bool:
    """F08 החלפת תמונה: new_photo_id חייב להיות מתוך תמונות הפרויקט שאינן flag='exclude'."""

def change_template(spread_id: str, new_template_id: str,
                     db: sqlite3.Connection) -> bool:
    """F08 שינוי תבנית."""
```

**קטלוג תבניות**: `template_id` מפתח למבנה קבוע (`layout_engine/templates.json`), שכל רשומה בו היא:

```jsonc
{
  "id": "4up-grid",
  "slot_count": 4,
  "slots": [
    {"index": 0, "x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5, "orientation": "any", "dominant": false}
  ]
}
```
`orientation` ∈ `"landscape" | "portrait" | "any"`. `dominant: true` מסמן סלוט מרכזי (F07). frontend-rtl ו-export-agent קוראים את אותו ה-JSON.

### 4.4 layout-engine → (DB) → export-agent

```python
# export_agent/api.py

@dataclass
class ExportResult:
    status: Literal["ok", "error"]
    pdf_path: str | None
    structure_path: str | None
    message: str | None

def export_album(project_id: str, db: sqlite3.Connection) -> ExportResult:
    """F09: מרנדר את אותו HTML/CSS של ה-preview ל-PDF (WeasyPrint), כותב גם album_structure.json.
    """

def snapshot_structure(project_id: str, db: sqlite3.Connection) -> str:
    """F08: כותב את מצב ה-spreads הנוכחי ל-storage.export_structure_prev_path, לפני regenerate_layout.
    """
```

---

## 5. זרימת F08 "יצירה מחדש" (regenerate) - חוזה מלא

1. frontend-rtl מציג modal אישור מפורש.
2. בעת אישור: frontend-rtl קורא `export_agent.snapshot_structure(project_id)`.
3. רק אז: frontend-rtl קורא `layout_engine.regenerate_layout(project_id, ...)`.
4. אין undo אוטומטי בגרסת הבסיס; השחזור ידני מתוך album_structure_prev.json.

---

## 6. כללי שינוי למסמך הזה

- כל שינוי בטיפוס קלט/פלט, בשם פונקציה, בטבלה או בעמודה **חייב** להתבצע כאן וב-`schema.sql`/`storage.py`/`db.py` **לפני** שסוכן אחר מממש/משנה קוד שתלוי בו.
- אם מודול מגלה שהוא צריך שדה/פונקציה שלא קיימת כאן - הוא לא "ממציא" ממשק משלו; הוא מבקש מה-architect להוסיף אותו כאן קודם.
- `product-manager` מתעד כל `scope-change` כ-issue מתויג בהתאם - גם זה עובר דרך architect אם משפיע על schema/interfaces.
