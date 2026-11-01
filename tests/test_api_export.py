"""
בדיקות API ל-F09 (issue #10): endpoints ב-main.py שעוטפים
export_agent.api.export_album, לפי החוזה ב-INTERFACES.md סעיף 4.4.

משתמש ב-Flask `app.test_client()` (לא httpx) - אותו דפוס בדיוק כמו
tests/test_api_f08.py. בודק רק את שכבת ה-HTTP (עטיפות דקות) - האלגוריתם/
קונבנציות הכשל עצמם מכוסים ב-tests/test_export_agent.py.

** הערת-סביבה (ראה tests/test_export_agent.py ל-docstring מלא) **:
WeasyPrint אינו מותקן בסביבה הזו - הטסטים כאן בודקים את מסלולי-השגיאה
(פרויקט לא קיים, אין spreads, weasyprint לא מותקן) ואת ה-endpoint של
ה-download *כש-אין* PDF - לא את תוכן ה-PDF שה-endpoint מגיש בהצלחה (אין
איך לייצר PDF אמיתי בלי weasyprint). הנתיב המוצלח-עם-PDF-בפועל מוזכר
ב-docstring אבל אינו ניתן להרצה כאן.

מכסה:
  (א) POST /export - 404 פרויקט לא קיים; status="error" (200) אין spreads
      עדיין; status="error" (200) weasyprint לא מותקן (גם כש-spreads קיימים).
  (ב) GET /export/download - 404 פרויקט לא קיים; 404 אין PDF עדיין.
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    db_path = storage_root / "smartalbum.db"
    monkeypatch.setenv("SMARTALBUM_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("SMARTALBUM_DB_PATH", str(db_path))

    for mod_name in (
        "storage",
        "db",
        "image_pipeline",
        "image_pipeline.api",
        "selection_engine",
        "selection_engine.api",
        "layout_engine",
        "layout_engine.api",
        "export_agent",
        "export_agent.api",
        "main",
    ):
        sys.modules.pop(mod_name, None)

    import main as main_mod

    main_mod.app.config["TESTING"] = True
    with main_mod.app.test_client() as test_client:
        yield test_client, main_mod


def _create_project(client, name="חתונה של דנה ויוסי") -> str:
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["project_id"]


def _insert_raw_photo(
    main_mod,
    project_id: str,
    *,
    flag: str = "none",
    selected_for_album: int = 0,
    selection_order: int | None = None,
) -> str:
    import db as db_mod

    photo_id = str(uuid.uuid4())
    conn = db_mod.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO photos (id, project_id, original_filename, original_path,
                                 uploaded_at, flag, selected_for_album, selection_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                photo_id,
                project_id,
                f"{photo_id}.jpg",
                f"/tmp/{photo_id}.jpg",
                "2024-01-01T00:00:00+00:00",
                flag,
                selected_for_album,
                selection_order,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return photo_id


def _make_selected_photos(main_mod, project_id, count) -> list[str]:
    return [
        _insert_raw_photo(main_mod, project_id, selected_for_album=1, selection_order=i + 1)
        for i in range(count)
    ]


def _generate_layout(test_client, project_id):
    resp = test_client.post(f"/api/projects/{project_id}/generate-layout")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok", body
    return body


# ---------------------------------------------------------------------------
# (א) POST /api/projects/<id>/export
# ---------------------------------------------------------------------------
def test_export_route_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.post("/api/projects/does-not-exist/export")
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


def test_export_route_no_layout_yet_returns_status_error_200(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)

    resp = test_client.post(f"/api/projects/{project_id}/export")
    assert resp.status_code == 200  # אותה קונבנציה כמו generate-layout/run-selection
    body = resp.get_json()
    assert body["project_id"] == project_id
    assert body["status"] == "error"
    assert body["pdf_path"] is None
    assert body["structure_path"] is None
    assert body["message"]


def test_export_route_weasyprint_unavailable_returns_status_error_200(client):
    """המצב האמיתי בסביבה הזו: spreads קיימים, מצב-בחירה תקין, אבל
    weasyprint לא מותקן - 200 עם status="error" נקי, לא 500."""
    test_client, main_mod = client
    project_id = _create_project(test_client)
    _make_selected_photos(main_mod, project_id, 20)
    _generate_layout(test_client, project_id)

    import export_agent.api as ea_mod

    assert ea_mod.weasyprint is None

    resp = test_client.post(f"/api/projects/{project_id}/export")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["pdf_path"] is None
    assert body["structure_path"] is None
    assert "PDF" in body["message"] or "WeasyPrint" in body["message"]

    # processing_jobs שלב export מעודכן ל-failed עם error_reason ייעודי.
    import db as db_mod

    conn = db_mod.get_connection()
    try:
        job = conn.execute(
            "SELECT status, error_reason FROM processing_jobs WHERE project_id = ? AND stage = 'export'",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    assert job["status"] == "failed"
    assert job["error_reason"] == "weasyprint_unavailable"


# ---------------------------------------------------------------------------
# (ב) GET /api/projects/<id>/export/download
# ---------------------------------------------------------------------------
def test_download_route_nonexistent_project_returns_404(client):
    test_client, _main_mod = client
    resp = test_client.get("/api/projects/does-not-exist/export/download")
    assert resp.status_code == 404
    assert resp.get_json().get("error") is True


def test_download_route_no_pdf_yet_returns_404(client):
    test_client, _main_mod = client
    project_id = _create_project(test_client)

    resp = test_client.get(f"/api/projects/{project_id}/export/download")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body.get("error") is True
    assert body.get("error_reason") == "pdf_not_found"


# ---------------------------------------------------------------------------
# NOTE (honesty, see module/PR docstring): a "successful export -> 200 with
# a real PDF download" test is intentionally NOT included here - it would
# require weasyprint to actually produce album.pdf, which is unavailable in
# this sandbox (PyPI unreachable). See tests/test_export_agent.py's two
# @pytest.mark.skipif(weasyprint is None, ...) tests for the narrow piece
# that exercises real PDF-byte generation whenever weasyprint IS installed.
# ---------------------------------------------------------------------------
