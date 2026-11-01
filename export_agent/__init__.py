"""SmartAlbum - export-agent package.

ראה export_agent/api.py לממשק המחייב (INTERFACES.md סעיף 4.4).

**היקף המימוש הנוכחי (issue #9 / F08) - תת-קבוצה מכוונת, לא המודול המלא**:
רק `snapshot_structure` ממומשת כאן - תלות קדימה הכרחית ל-F08 (ראה
docstring ב-`api.py`). `export_album` (F09, ייצוא PDF עם WeasyPrint) *לא*
ממומשת - עדיין חסומה/issue #10, מחוץ להיקף הזה בכוונה.
"""
