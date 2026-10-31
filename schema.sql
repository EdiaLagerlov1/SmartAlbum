-- SmartAlbum - סכמת SQLite משותפת לכל המודולים (architect-owned)
--
-- קובץ זה הוא מקור האמת היחיד למבנה הנתונים. כל שינוי בו עובר דרך ה-architect
-- *לפני* שסוכן אחר מממש עליו קוד. נטען ע"י db.init_db().
--
-- כל הטבלאות משותפות ל-DB יחיד (storage/smartalbum.db כברירת מחדל) -
-- אין DB נפרד למודול.

PRAGMA foreign_keys = ON;

-- ============================================================
-- projects
-- ============================================================
CREATE TABLE IF NOT EXISTS projects (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL,              -- ISO-8601 UTC
  status     TEXT NOT NULL DEFAULT 'draft'
             CHECK (status IN ('draft', 'selecting', 'generated', 'exported'))
);

-- ============================================================
-- photos
-- כל תמונה שהועלתה לפרויקט, לאורך כל שרשרת העיבוד (image-pipeline
-- -> selection-engine -> layout-engine). קובץ אחד = שורה אחת.
-- ============================================================
CREATE TABLE IF NOT EXISTS photos (
  id                     TEXT PRIMARY KEY,
  project_id             TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

  -- מקור (F01, F02) -- original_path אסור להשתנות או להיכתב-עליו לעולם
  original_filename      TEXT NOT NULL,   -- השם שהועלה ע"י המשתמשת
  original_path          TEXT NOT NULL,   -- storage/projects/<id>/originals/<photo_id>.jpg
  file_size_bytes        INTEGER,
  uploaded_at            TEXT NOT NULL,   -- ISO-8601 UTC

  -- image-pipeline output (F02, F04, F05 prerequisites)
  thumb_path             TEXT,            -- storage/projects/<id>/thumbnails/<photo_id>.jpg
  exif_orientation       INTEGER,         -- ערך EXIF גולמי (1..8), ליישום בזמן יצירת thumbnail בלבד
  width                  INTEGER,         -- פיקסלים, אחרי תיקון כיוון (מה-thumbnail/המקור)
  height                 INTEGER,
  taken_at               TEXT,            -- EXIF DateTimeOriginal, ISO-8601, nullable (F05)
  sharpness_score        REAL,            -- המלצה בלבד (F04) - לעולם לא עילה למחיקה אוטומטית
  exposure_score         REAL,
  similarity_group_id    TEXT,            -- F04: תמונות "דומות" מקובצות לאותו מזהה

  -- selection-engine input/output (F03, F05, F06)
  flag                   TEXT NOT NULL DEFAULT 'none'
                         CHECK (flag IN ('none', 'must', 'exclude')),
                         -- F03: לא ולידי שיהיה must וגם exclude - flag הוא שדה יחיד, לא שני בוליאנים
  event_stage            TEXT,            -- למשל 'getting_ready' | 'ceremony' | 'party'...  nullable (F05)
  event_stage_confidence REAL,            -- 0..1. ביטחון נמוך -> UI מציג "לבדיקה", לא כעובדה
  needs_stage_review     INTEGER NOT NULL DEFAULT 0 CHECK (needs_stage_review IN (0, 1)),
                         -- F05: timestamp חסר / confidence נמוך -> 1

  -- פלט F06 (בחירה) - selection-engine כותב, layout-engine קורא
  selected_for_album     INTEGER NOT NULL DEFAULT 0 CHECK (selected_for_album IN (0, 1)),
  selection_order        INTEGER,         -- סדר מוצע (כרונולוגי/שלב אירוע) בתוך הנבחרות; nullable אם לא נבחרה

  error_reason           TEXT             -- F01: אם קובץ נדחה/נכשל בעיבוד, הסיבה מוצגת כאן בנוסף ל-processing_jobs
);

CREATE INDEX IF NOT EXISTS idx_photos_project ON photos(project_id);
CREATE INDEX IF NOT EXISTS idx_photos_flag ON photos(project_id, flag);
CREATE INDEX IF NOT EXISTS idx_photos_similarity ON photos(similarity_group_id);
CREATE INDEX IF NOT EXISTS idx_photos_selected ON photos(project_id, selected_for_album);

-- ============================================================
-- spreads
-- 15 כפולות לאלבום (F07). version מקדם כל "יצירה מחדש" (F08) -
-- נשמרת רק הגרסה הנוכחית (לא טבלת היסטוריה מלאה).
-- ============================================================
CREATE TABLE IF NOT EXISTS spreads (
  id          TEXT PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  position    INTEGER NOT NULL,          -- 1..15
  template_id TEXT NOT NULL,             -- מפתח לקטלוג התבניות של layout-engine (ראה INTERFACES.md)
  version     INTEGER NOT NULL DEFAULT 1,
  updated_at  TEXT NOT NULL,             -- ISO-8601 UTC, לכל עדכון (יצירה/swap/שינוי תבנית)

  UNIQUE (project_id, position)
);

CREATE INDEX IF NOT EXISTS idx_spreads_project ON spreads(project_id);

-- ============================================================
-- spread_photos
-- שיבוץ תמונה -> סלוט בתוך כפולה (F07, F08)
-- ============================================================
CREATE TABLE IF NOT EXISTS spread_photos (
  spread_id  TEXT NOT NULL REFERENCES spreads(id) ON DELETE CASCADE,
  photo_id   TEXT NOT NULL REFERENCES photos(id) ON DELETE RESTRICT,
  slot_index INTEGER NOT NULL,           -- אינדקס סלוט בתוך התבנית (0-based)
  is_dominant INTEGER NOT NULL DEFAULT 0 CHECK (is_dominant IN (0, 1)),
                                          -- "תמונה מרכזית" של הכפולה (F07)

  PRIMARY KEY (spread_id, slot_index),
  UNIQUE (spread_id, photo_id)           -- תמונה לא חוזרת פעמיים באותה כפולה
);

CREATE INDEX IF NOT EXISTS idx_spread_photos_photo ON spread_photos(photo_id);

-- ============================================================
-- processing_jobs
-- "job queue" פשוט - סטטוס לכל שלב עיבוד לכל פרויקט (F01, F10).
-- שורה אחת per (project_id, stage): ניסיון חוזר מעדכן את השורה
-- הקיימת, לא מצרף שורות היסטוריה.
-- ============================================================
CREATE TABLE IF NOT EXISTS processing_jobs (
  id           TEXT PRIMARY KEY,
  project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stage        TEXT NOT NULL
               CHECK (stage IN ('upload', 'exif', 'thumbnail', 'grouping',
                                 'tagging', 'selection', 'layout', 'export')),
  status       TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'running', 'done', 'failed')),
  error_reason TEXT,                     -- F01, F10: סיבת כשל, לא עוצר שלבים/קבצים אחרים
  updated_at   TEXT NOT NULL,            -- ISO-8601 UTC

  UNIQUE (project_id, stage)
);

CREATE INDEX IF NOT EXISTS idx_jobs_project ON processing_jobs(project_id);
