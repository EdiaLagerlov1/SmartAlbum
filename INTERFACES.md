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
- **`photos.flag`** שדה יחיד (`none|must|exclude`) - לא שני בוליאניים, כך שלא ניתן טכנית שיהיה גם must וגם exclude בו-זמנית (F03).
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

> **הערה (image-pipeline, implementation note - issue #5)**: בדיוק כמו הערת
> ה-architect מעל על FastAPI/httpx (סעיף 1) - בסביבת הפיתוח/CI הנוכחית
> PyPI חסום ו-`imagehash` (רשום ב-`requirements.txt`) **לא מותקן בפועל**.
> `group_similar_photos` מנסה `import imagehash` ברכה (best-effort); אם
> זה נכשל, נופל בחזרה למימוש "average hash" עצמאי עם Pillow+numpy בלבד
> (הקטנה ל-8x8 grayscale, השוואה לממוצע -> 64 ביט, Hamming distance),
> מתועד ב-`image_pipeline/api.py` (`_compute_photo_hash`/`_AverageHash`).
> TODO: אם `imagehash` יותקן בעתיד (סביבה עם גישת רשת) - אפשר/רצוי להסיר
> את ה-fallback ולהשתמש תמיד ב-`imagehash.phash` (מדויק יותר).

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

> **עדכון (selection-engine, issue #5 / F03)**: `selection_engine/api.py` מומש בפועל
> (`set_flag`, `MustCountCheck`, `check_must_limit` - חתימות זהות למה שתועד מעל,
> אין שינוי). נוספו שני endpoints ב-`main.py` שהם **עטיפת HTTP דקה 1:1** מעל
> הפונקציות האלה (לפי הכלל בסעיף 1 - אין לוגיקה נוספת ב-route handler):
>
> - `PATCH /api/projects/<project_id>/photos/<photo_id>` עם body `{"flag": "none"|"must"|"exclude"}`.
>   קורא ל-`set_flag` ואז ל-`check_must_limit`, מחזיר
>   `{"photo_id", "flag", "must_count", "must_count_exceeds_limit"}`.
>   ערך `flag` שאינו אחד משלושת הערכים -> `400` עם הודעת שגיאה בעברית
>   (`error_reason: "invalid_flag"`), **לא** מגיע ל-DB (ה-`CHECK` constraint
>   ב-schema.sql הוא הגנת-עומק, לא התגובה הראשית למשתמשת).
>   עדכון flag קיים (למשל `must` -> `exclude`) הוא **דריסה** של הערך הקודם -
>   אין "שני flags" שנשארים, כי `flag` הוא עמודה יחידה (ראה סעיף 2).
> - `GET /api/projects/<project_id>/must-count` - עטיפה דקה מעל
>   `check_must_limit`, מחזיר `{"must_count", "must_count_exceeds_limit"}`
>   בלי לשנות כלום (שימושי ל-UI כדי להציג אזהרה בלי לחכות לעדכון flag הבא).
> - `GET /api/projects/<project_id>/photos` (קיים, F01) הורחב עם פרמטר query
>   אופציונל `?flag=none|must|exclude` לסינון; בלי הפרמטר הוא מחזיר הכל
>   כמקודם (לא שובר התנהגות קיימת/טסטים קיימים). `flag` כבר הוחזר
>   עשורה שורה מאז F01 (ללא שינוי).
>
> **כלל חסימת-יצירה (F03 <-> F06, קריטי ל-layout-engine)**: "יצירה" = כל קריאה
> ל-`run_selection` (F06) ובהמשך ל-`generate_layout`/`regenerate_layout`
> (F07/F08) - **אסור** שתתבצע כל עוד `check_must_limit(project_id, db).ok is
> False` (כלומר `must_count > 60`). זו לא חסימה על הסימון עצמו - המשתמשת
> יכולה לסמן יותר מ-60 תמונות `must` ולהמשיך לראות אותן ב-UI; האזהרה
> (`must_count_exceeds_limit=true`) חייבת להיות גלויה וה"יצירה" חסומה עד
> שהמשתמשת מתקנת בחירה (מורידה `must` מתמונות, חוזרת ל-<=60). `run_selection`
> (כשיתממש) **חייב** לקרוא ל-`check_must_limit` כצעד ראשון ולהחזיר
> `status="too_many_must"` בלי לכתוב `selected_for_album` כלל אם לא תקין -
> לא להסתמך רק על frontend-rtl שיחסום; זו אכיפה כפולה (UI + שרת), כנדרש
> ב-F03 ("דחה פעולה כזו ב-UI/API layer, לא רק בלוגיקה").

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

## 5. חוזה F10 - סטטוס עיבוד/כשל, ניסיון חוזר, מחיקת פרויקט

סעיף זה הוא החוזה המחייב ש-`frontend-rtl` מממש עליו (issue #11). **אין
צורך בטבלה/עמודה חדשה ב-DB עבור F10** - כל מה שדרוש כבר קיים ב-`schema.sql`
(טבלת `processing_jobs`), ב-`db.py` (`delete_project`) וב-`image_pipeline/api.py`
(`process_upload`). `frontend-rtl` עוטף את אלה ב-endpoints HTTP - לא ממציא
לוגיקה/טבלה/פונקציית pipeline חדשה.

### 5.1 חוזה קריאת סטטוס (status read)

יש **שתי שכבות סטטוס**, לא אחת - אל תמזגו אותן:

- **`projects.status`** (`draft|selecting|generated|exported`) - מצב-על
  גס של הפרויקט בשלבי העבודה (העלאה/בחירה/פריסה נוצרה/יוצא). **נשאר
  כמו שהוא** - F10 לא מוסיף לו ערכים/לוגיקת-חישוב-אוטומטי. סוכן שמקדם
  אותו (למשל ל-`exported` אחרי `export_album`) עושה זאת כחלק מה-issue
  הרלוונטי לאותו שלב, לא כאן.
- **`processing_jobs`** - המצב המפורט **per-stage** (`upload, exif,
  thumbnail, grouping, tagging, selection, layout, export`), שורה אחת
  per `(project_id, stage)` עם `status` (`pending|running|done|failed`)
  ו-`error_reason`. זה מקור האמת ל"מה נכשל ובאיזה שלב" שה-UI מציג.

חוזה ה-endpoint (frontend-rtl מממש, לדוגמה `GET /api/projects/<id>/status`):
- קלט: `project_id` בנתיב. 404 אם הפרויקט לא קיים (אותו תבנית `_json_error`
  שכבר קיימת ב-`main.py`).
- שליפה: `SELECT * FROM processing_jobs WHERE project_id = ?` - **בלי**
  שאילתה/טבלה חדשה. אם לפרויקט אין שורות בכלל לשלב מסוים (לדוגמה
  `grouping` לפני שהמודול קיים) - ה-endpoint לא ממציא ערך, מחזיר את השלב
  כ-`"not_started"` בתשובת ה-JSON (מצב נגזר בצד ה-API, לא ערך DB חדש).
- פלט מוצע (JSON-serializable dict, התאם לסגנון שאר ה-endpoints ב-`main.py`):
  ```jsonc
  {
    "project_id": "...",
    "status": "draft",                 // projects.status - גס
    "stages": {                        // מכל שורת processing_jobs הקיימת
      "upload":    {"status": "done",   "error_reason": null, "updated_at": "..."},
      "exif":      {"status": "done",   "error_reason": null, "updated_at": "..."},
      "thumbnail": {"status": "failed", "error_reason": "...", "updated_at": "..."},
      "grouping":  {"status": "not_started", "error_reason": null, "updated_at": null}
      // ... tagging/selection/layout/export - "not_started" עד שהמודול הרלוונטי קיים/רץ
    },
    "photos_failed_count": 2,          // נוחות ל-UI: COUNT(*) FROM photos WHERE project_id=? AND error_reason IS NOT NULL
    "photos_total_count": 40
  }
  ```
- `processing_jobs` לפי `schema.sql` היא "שורה אחת per `(project_id, stage)`
  מתעדכנת" (לא היסטוריה) - אז "מצב עיבוד" הוא תמיד התמונה העדכנית, אין
  timeline להציג מעבר לזה בגרסת הבסיס.

### 5.2 חוזה ניסיון חוזר (retry)

**מה שקיים כבר עובד ולא דורש שינוי**: `image_pipeline.process_upload`
(סעיף 4.1) הוא **idempotent** - אם נקרא שוב לאותו `(project_id,
original_filename)` שכבר הצליח (יש לו `thumb_path`), הוא מחזיר את
`photo_id` הקיים כ-`status="done"` בלי לעבד מחדש. קובץ שנכשל/לא טופל
עדיין מעובד מחדש כרגיל. **כך "ניסיון חוזר ממשיך משלבים שהושלמו" מתקיים
כבר היום** לשלבי `upload/exif/thumbnail` - אין צורך בלוגיקת retry
נפרדת, flag חדש, או פונקציית pipeline חדשה לשלבים האלה.

חוזה ה-endpoint (frontend-rtl מממש, לדוגמה `POST /api/projects/<id>/retry`):
- **מה הוא כן עושה**: לכל קובץ שה-UI מציג כ"נכשל" (שורת `photos` עם
  `error_reason IS NOT NULL`, או קובץ שהועלה אך `process_upload` לא נקרא
  עליו בהצלחה) - קורא שוב ל-`image_pipeline.process_upload(project_id,
  saved_file_path, original_filename, db)` עם אותו קובץ מקור. חובה לשמור
  את הקובץ הגולמי שהמשתמשת העלתה (temp path/upload cache בצד frontend-rtl)
  כדי שיהיה מה להעביר ל-retry - `process_upload` לא "זוכר" קבצים שנכשלו
  לפני שנכתבו ל-`originals/` (F02: original_path נכתב רק בהצלחה).
- **מה הוא לא עושה**: לא קורא `process_upload` על תמונות עם `status="done"`
  (אם קורא בטעות - `process_upload` עצמו חוסם כפילות דרך ה-dedup, אז זה
  לא-מזיק, אבל מיותר - frontend-rtl צריך לסנן מראש כדי לא לבזבז I/O).
  לא יוצר endpoint/פונקציה חדשה ב-`image_pipeline` - "retry" הוא רק
  re-submit לאותה `process_upload`.
- פלט מוצע: רשימת `PhotoUploadResult` (כמו תשובת upload רגילה) per קובץ
  שנוסה מחדש, כדי שה-UI יראה מה הצליח/נכשל שוב.
- **שלבים שעדיין לא קיימים** (`grouping` ואילך - `grouping` ממומש
  ב-`image_pipeline/api.py` אבל כרגע **לא** נוגעים בו/בענף שלו כחלק
  מ-issue זה; `tagging/selection/layout/export` עדיין לא ממומשים כלל):
  כשהם ייכתבו, ה"retry" שלהם יעקוב **אותה תבנית** - בדיקת שורת
  `processing_jobs` לשלב הרלוונטי, קריאה חוזרת לפונקציית ה-API של אותו
  מודול (`group_similar_photos`, וכו') רק על מה שלא `done`, בלי טבלה/שדה
  חדש. frontend-rtl לא צריך לחכות לאלה כדי לממש את ה-endpoint של היום -
  מספיק לכתוב אותו עבור upload/exif/thumbnail ולהרחיב בעתיד.

### 5.3 חוזה מחיקת פרויקט (delete)

- מחיקה היא **קריאה יחידה**: `db.delete_project(project_id)`. הפונקציה
  הזו (ראה `db.py`) **כבר מוחקת גם את כל שורות ה-DB (projects, photos,
  spreads, spread_photos, processing_jobs) וגם את כל עץ הקבצים בדיסק**
  (`storage/projects/<id>/` כולל originals/thumbnails/exports) - שום
  endpoint/מודול אחר מורשה למחוק שורה/קובץ של פרויקט בנפרד.
- חוזה ה-endpoint (frontend-rtl מממש, `DELETE /api/projects/<id>`):
  קורא רק ל-`db.delete_project(project_id)` אחרי 404-check שהפרויקט קיים,
  ומחזיר 200/204. **אין** flag/פרמטר "confirm=true" חדש ב-backend - זו
  פעולה בלתי-הפיכה (irreversible), אז האישור המפורש הוא **אחריות ה-UI**
  (modal "בטוח/י שברצונך למחוק?" לפני שליחת ה-request), בדיוק כמו שכבר
  מתועד לזרימת F08 regenerate בסעיף 6 למטה - אין צורך בשדה DB נוסף.

### 5.4 סיכום: אין שינוי סכמה נדרש ל-issue זה

נבדק בפועל (לא רק "כרגיל לא צריך"): `schema.sql` (טבלת `processing_jobs`
ו-cascade-ים), `db.py` (`delete_project` - כבר מוחק גם DB וגם קבצים,
ראה `tests/test_db.py`), ו-`image_pipeline/api.py` (`process_upload` -
כבר idempotent/retry-safe) מספקים את כל מה ש-frontend-rtl צריך. לא נוספו
עמודות/טבלאות/פונקציות pipeline חדשות עבור F10 - רק endpoints ב-`main.py`
שעוטפים את הקיים, לפי 5.1-5.3 מעלה.

---

## 6. זרימת F08 "יצירה מחדש" (regenerate) - חוזה מלא

1. frontend-rtl מציג modal אישור מפורש.
2. בעת אישור: frontend-rtl קורא `export_agent.snapshot_structure(project_id)`.
3. רק אז: frontend-rtl קורא `layout_engine.regenerate_layout(project_id, ...)`.
4. אין undo אוטומטי בגרסת הבסיס; השחזור ידני מתוך album_structure_prev.json.

---

## 7. כללי שינוי למסמך הזה

- כל שינוי בטיפוס קלט/פלט, בשם פונקציה, בטבלה או בעמודה **חייב** להתבצע כאן וב-`schema.sql`/`storage.py`/`db.py` **לפני** שסוכן אחר מממש/משנה קוד שתלוי בו.
- אם מודול מגלה שהוא צריך שדה/פונקציה שלא קיימת כאן - הוא לא "ממציא" ממשק משלו; הוא מבקש מה-architect להוסיף אותו כאן קודם.
- `product-manager` מתעד כל `scope-change` כ-issue מתויג בהתאם - גם זה עובר דרך architect אם משפיע על schema/interfaces.
