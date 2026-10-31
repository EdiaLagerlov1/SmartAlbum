/* SmartAlbum - F01 frontend logic.
 *
 * אחריות הקובץ הזה: קריאה/כתיבה ל-API של Flask (main.py) בלבד - *אין* כאן
 * לוגיקת בחירה/פריסה (זו אחריות selection-engine / layout-engine בצד שרת).
 *
 * העלאה היא per-file: כל קובץ = בקשת XHR נפרדת, עם התקדמות (progress event)
 * ו-fetch/await שלא חוסם קבצים אחרים - כל הקבצים "ממריאים" במקביל דרך
 * Promise.allSettled, כך שקובץ שנכשל לא עוצר את שאר ההעלאה.
 */

(() => {
  "use strict";

  const state = {
    projectId: null,
    projectName: null,
  };

  const el = {
    projectNameInput: document.getElementById("project-name"),
    createProjectBtn: document.getElementById("create-project-btn"),
    projectError: document.getElementById("project-error"),
    projectInfo: document.getElementById("project-info"),
    projectInfoName: document.getElementById("project-info-name"),
    projectInfoId: document.getElementById("project-info-id"),
    uploadSection: document.getElementById("upload-section"),
    dropzone: document.getElementById("dropzone"),
    browseBtn: document.getElementById("browse-btn"),
    fileInput: document.getElementById("file-input"),
    uploadList: document.getElementById("upload-list"),
    uploadSummary: document.getElementById("upload-summary"),
    uploadSummaryText: document.getElementById("upload-summary-text"),
    photosSection: document.getElementById("photos-section"),
    photosGrid: document.getElementById("photos-grid"),
    refreshPhotosBtn: document.getElementById("refresh-photos-btn"),
    // F10 (issue #11): מצב עיבוד / ניסיון חוזר / מחיקת פרויקט.
    statusSection: document.getElementById("status-section"),
    refreshStatusBtn: document.getElementById("refresh-status-btn"),
    retryBtn: document.getElementById("retry-btn"),
    stagesList: document.getElementById("stages-list"),
    photosFailedSummary: document.getElementById("photos-failed-summary"),
    retryResult: document.getElementById("retry-result"),
    deleteProjectBtn: document.getElementById("delete-project-btn"),
    deleteModalOverlay: document.getElementById("delete-modal-overlay"),
    deleteModalProjectName: document.getElementById("delete-modal-project-name"),
    deleteConfirmInput: document.getElementById("delete-confirm-input"),
    deleteModalError: document.getElementById("delete-modal-error"),
    deleteConfirmBtn: document.getElementById("delete-confirm-btn"),
    deleteCancelBtn: document.getElementById("delete-cancel-btn"),
  };

  // מילת האישור ל-type-to-confirm במודל המחיקה (F10) - קבועה, לא תלויה
  // ב-locale של הדפדפן.
  const DELETE_CONFIRM_WORD = "מחיקה";

  // --------------------------------------------------------------------
  // יצירת פרויקט
  // --------------------------------------------------------------------
  async function createProject() {
    const name = el.projectNameInput.value.trim();
    el.projectError.hidden = true;

    if (!name) {
      el.projectError.textContent = "יש להזין שם פרויקט";
      el.projectError.hidden = false;
      return;
    }

    el.createProjectBtn.disabled = true;
    try {
      const resp = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const body = await resp.json();

      if (!resp.ok) {
        el.projectError.textContent = body.message || "שגיאה ביצירת הפרויקט";
        el.projectError.hidden = false;
        return;
      }

      state.projectId = body.project_id;
      state.projectName = body.name;
      el.projectInfoName.textContent = body.name;
      el.projectInfoId.textContent = body.project_id;
      el.projectInfo.hidden = false;
      el.uploadSection.hidden = false;
      el.photosSection.hidden = false;
      el.statusSection.hidden = false;
      await loadPhotos();
      await loadStatus();
    } catch (err) {
      el.projectError.textContent = "שגיאת תקשורת עם השרת: " + err.message;
      el.projectError.hidden = false;
    } finally {
      el.createProjectBtn.disabled = false;
    }
  }

  // --------------------------------------------------------------------
  // בחירת/גרירת קבצים
  // --------------------------------------------------------------------
  function isJpegFile(file) {
    const name = (file.name || "").toLowerCase();
    const okExt = name.endsWith(".jpg") || name.endsWith(".jpeg");
    const okType = !file.type || file.type === "image/jpeg" || file.type === "image/jpg";
    return okExt && okType;
  }

  function handleFiles(fileList) {
    const files = Array.from(fileList || []);
    if (files.length === 0) return;
    uploadFiles(files);
  }

  el.browseBtn.addEventListener("click", () => el.fileInput.click());
  el.fileInput.addEventListener("change", (e) => {
    handleFiles(e.target.files);
    e.target.value = ""; // מאפשר לבחור אותם קבצים שוב אם צריך
  });

  ["dragenter", "dragover"].forEach((evtName) => {
    el.dropzone.addEventListener(evtName, (e) => {
      e.preventDefault();
      el.dropzone.classList.add("drag-over");
    });
  });
  ["dragleave", "drop"].forEach((evtName) => {
    el.dropzone.addEventListener(evtName, (e) => {
      e.preventDefault();
      el.dropzone.classList.remove("drag-over");
    });
  });
  el.dropzone.addEventListener("drop", (e) => {
    const dt = e.dataTransfer;
    if (dt && dt.files) handleFiles(dt.files);
  });

  // --------------------------------------------------------------------
  // העלאה per-file עם חיווי התקדמות, בלי לעצור על קובץ כושל.
  // --------------------------------------------------------------------
  function createUploadRow(file) {
    const li = document.createElement("li");
    li.className = "upload-item";

    const nameSpan = document.createElement("span");
    nameSpan.className = "file-name";
    nameSpan.textContent = file.name;

    const progress = document.createElement("progress");
    progress.max = 100;
    progress.value = 0;

    const badge = document.createElement("span");
    badge.className = "status-badge pending";
    badge.textContent = "מחכה";

    const errorP = document.createElement("p");
    errorP.className = "item-error";
    errorP.hidden = true;

    li.appendChild(nameSpan);
    li.appendChild(progress);
    li.appendChild(badge);
    li.appendChild(errorP);
    el.uploadList.appendChild(li);

    return {
      setStatus(status, text) {
        badge.className = "status-badge " + status;
        badge.textContent = text;
      },
      setProgress(pct) {
        progress.value = pct;
      },
      setError(message) {
        errorP.textContent = message;
        errorP.hidden = !message;
      },
    };
  }

  function uploadOneFile(file) {
    const row = createUploadRow(file);

    // ולידציה בסיסית בצד לקוח - עדיין נשלח לשרת (מקור האמת לולידציה),
    // אבל כך המשתמשת מקבלת חיווי מהיר יותר שהקובץ חשוד.
    if (!isJpegFile(file)) {
      row.setStatus("failed", "נדחה");
      row.setError("רק קבצי JPEG נתמכים");
    }

    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/projects/${state.projectId}/photos`);

      row.setStatus("uploading", "מעלה...");

      xhr.upload.addEventListener("progress", (evt) => {
        if (evt.lengthComputable) {
          const pct = Math.round((evt.loaded / evt.total) * 100);
          row.setProgress(pct);
        }
      });

      xhr.onload = () => {
        row.setProgress(100);
        let body = null;
        try {
          body = JSON.parse(xhr.responseText);
        } catch (_e) {
          body = null;
        }

        if (xhr.status >= 200 && xhr.status < 300 && body && body.status === "done") {
          row.setStatus("done", "הועלה בהצלחה");
          row.setError("");
          resolve({ file, ok: true, body });
        } else {
          const reason = (body && body.error_reason) || (body && body.message) || "העלאה נכשלה";
          row.setStatus("failed", "נכשל");
          row.setError(reason);
          resolve({ file, ok: false, body, error: reason });
        }
      };

      xhr.onerror = () => {
        row.setStatus("failed", "נכשל");
        row.setError("שגיאת תקשורת עם השרת");
        resolve({ file, ok: false, error: "network error" });
      };

      const formData = new FormData();
      formData.append("file", file, file.name);
      xhr.send(formData);
    });
  }

  async function uploadFiles(files) {
    if (!state.projectId) return;

    el.uploadSummary.hidden = false;
    el.uploadSummaryText.textContent = `מעלה ${files.length} קבצים...`;

    // כל הקבצים מועלים במקביל (per-file requests) - Promise.allSettled
    // מבטיח שקובץ שנכשל לא "עוצר" את הבקשות האחרות ולא מפיל await סדרתי.
    const results = await Promise.allSettled(files.map((file) => uploadOneFile(file)));

    const succeeded = results.filter(
      (r) => r.status === "fulfilled" && r.value && r.value.ok
    ).length;
    const failed = results.length - succeeded;
    el.uploadSummaryText.textContent = `הועלו ${succeeded} מתוך ${results.length} קבצים` +
      (failed > 0 ? ` (${failed} נכשלו - פירוט בשורות למעלה)` : "");

    await loadPhotos();
    await loadStatus();
  }

  // --------------------------------------------------------------------
  // רשימת תמונות שהועלו בפועל (GET /api/projects/<id>/photos)
  // --------------------------------------------------------------------
  async function loadPhotos() {
    if (!state.projectId) return;
    try {
      const resp = await fetch(`/api/projects/${state.projectId}/photos`);
      if (!resp.ok) return;
      const photos = await resp.json();
      renderPhotos(photos);
    } catch (_err) {
      // שקט - רשימת התמונות היא נוחות בלבד, לא קריטית לזרימת ההעלאה.
    }
  }

  function renderPhotos(photos) {
    el.photosGrid.innerHTML = "";
    photos.forEach((photo) => {
      const card = document.createElement("div");
      card.className = "photo-card";

      const img = document.createElement("img");
      img.src = photo.thumbnail_url || "";
      img.alt = photo.original_filename;
      img.loading = "lazy";

      const meta = document.createElement("div");
      meta.className = "photo-meta";
      meta.textContent = photo.original_filename;

      card.appendChild(img);
      card.appendChild(meta);
      el.photosGrid.appendChild(card);
    });
  }

  // --------------------------------------------------------------------
  // F10 (issue #11): מצב עיבוד per-stage, ניסיון חוזר, מחיקת פרויקט.
  // --------------------------------------------------------------------
  const STAGE_LABELS = {
    upload: "העלאה",
    exif: "תיקון כיוון (EXIF)",
    thumbnail: "יצירת תמונות מוקטנות",
    grouping: "קיבוץ תמונות דומות",
    tagging: "תיוג שלבי אירוע",
    selection: "בחירת תמונות לאלבום",
    layout: "פריסת כפולות",
    export: "ייצוא PDF",
  };

  const STAGE_ORDER = [
    "upload",
    "exif",
    "thumbnail",
    "grouping",
    "tagging",
    "selection",
    "layout",
    "export",
  ];

  const STATUS_LABELS = {
    not_started: "לא התחיל",
    pending: "מחכה",
    running: "בתהליך",
    done: "הושלם",
    failed: "נכשל",
  };

  function renderStages(statusBody) {
    el.stagesList.innerHTML = "";
    let anyFailed = false;

    STAGE_ORDER.forEach((stage) => {
      const info = (statusBody.stages && statusBody.stages[stage]) || {
        status: "not_started",
        error_reason: null,
      };
      if (info.status === "failed") anyFailed = true;

      const li = document.createElement("li");
      li.className = "stage-item";

      const nameSpan = document.createElement("span");
      nameSpan.className = "stage-name";
      nameSpan.textContent = STAGE_LABELS[stage] || stage;

      const badge = document.createElement("span");
      badge.className = "status-badge " + info.status;
      badge.textContent = STATUS_LABELS[info.status] || info.status;

      li.appendChild(nameSpan);
      li.appendChild(badge);

      if (info.status === "failed" && info.error_reason) {
        const errorP = document.createElement("p");
        errorP.className = "stage-error";
        errorP.textContent = info.error_reason;
        li.appendChild(errorP);
      }

      el.stagesList.appendChild(li);
    });

    el.retryBtn.hidden = !anyFailed;

    if (typeof statusBody.photos_total_count === "number") {
      el.photosFailedSummary.hidden = false;
      el.photosFailedSummary.textContent =
        `תמונות שנכשלו: ${statusBody.photos_failed_count} מתוך ${statusBody.photos_total_count}`;
    } else {
      el.photosFailedSummary.hidden = true;
    }
  }

  async function loadStatus() {
    if (!state.projectId) return;
    try {
      const resp = await fetch(`/api/projects/${state.projectId}/status`);
      if (!resp.ok) return;
      const body = await resp.json();
      renderStages(body);
    } catch (_err) {
      // שקט - סטטוס הוא נוחות ל-UI, לא קריטי לזרימת ההעלאה עצמה.
    }
  }

  async function retryProcessing() {
    if (!state.projectId) return;
    el.retryBtn.disabled = true;
    el.retryResult.hidden = true;
    try {
      const resp = await fetch(`/api/projects/${state.projectId}/retry`, { method: "POST" });
      const body = await resp.json();
      if (!resp.ok) {
        el.retryResult.hidden = false;
        el.retryResult.textContent = (body && body.message) || "ניסיון חוזר נכשל";
        return;
      }

      const retried = body.retried || [];
      const succeeded = retried.filter((r) => r.status === "done").length;
      el.retryResult.hidden = false;
      el.retryResult.textContent = retried.length === 0
        ? "אין קבצים שממתינים לניסיון חוזר."
        : `ניסיון חוזר הושלם: ${succeeded} מתוך ${retried.length} הצליחו.`;

      await loadPhotos();
      await loadStatus();
    } catch (err) {
      el.retryResult.hidden = false;
      el.retryResult.textContent = "שגיאת תקשורת עם השרת: " + err.message;
    } finally {
      el.retryBtn.disabled = false;
    }
  }

  // --------------------------------------------------------------------
  // F10: מחיקת פרויקט - אישור מפורש (type-to-confirm) לפני ה-request,
  // כי זו פעולה בלתי-הפיכה (מוחקת גם מקורות/תוצרים, ראה INTERFACES.md 5.3).
  // --------------------------------------------------------------------
  function openDeleteModal() {
    el.deleteModalProjectName.textContent = state.projectName || "";
    el.deleteConfirmInput.value = "";
    el.deleteModalError.hidden = true;
    el.deleteConfirmBtn.disabled = true;
    el.deleteModalOverlay.hidden = false;
    el.deleteConfirmInput.focus();
  }

  function closeDeleteModal() {
    el.deleteModalOverlay.hidden = true;
  }

  function resetProjectUi() {
    state.projectId = null;
    state.projectName = null;
    el.projectInfo.hidden = true;
    el.uploadSection.hidden = true;
    el.photosSection.hidden = true;
    el.statusSection.hidden = true;
    el.uploadList.innerHTML = "";
    el.uploadSummary.hidden = true;
    el.photosGrid.innerHTML = "";
    el.stagesList.innerHTML = "";
    el.retryBtn.hidden = true;
    el.retryResult.hidden = true;
    el.photosFailedSummary.hidden = true;
    el.projectNameInput.value = "";
  }

  async function confirmDeleteProject() {
    if (!state.projectId) return;
    if (el.deleteConfirmInput.value.trim() !== DELETE_CONFIRM_WORD) {
      el.deleteModalError.hidden = false;
      el.deleteModalError.textContent = `יש להקליד בדיוק "${DELETE_CONFIRM_WORD}" כדי לאשר מחיקה`;
      return;
    }

    el.deleteConfirmBtn.disabled = true;
    try {
      const resp = await fetch(`/api/projects/${state.projectId}`, { method: "DELETE" });
      if (!resp.ok) {
        const body = await resp.json().catch(() => null);
        el.deleteModalError.hidden = false;
        el.deleteModalError.textContent = (body && body.message) || "מחיקת הפרויקט נכשלה";
        return;
      }

      closeDeleteModal();
      resetProjectUi();
    } catch (err) {
      el.deleteModalError.hidden = false;
      el.deleteModalError.textContent = "שגיאת תקשורת עם השרת: " + err.message;
    } finally {
      el.deleteConfirmBtn.disabled = false;
    }
  }

  el.createProjectBtn.addEventListener("click", createProject);
  el.refreshPhotosBtn.addEventListener("click", loadPhotos);
  el.refreshStatusBtn.addEventListener("click", loadStatus);
  el.retryBtn.addEventListener("click", retryProcessing);
  el.deleteProjectBtn.addEventListener("click", openDeleteModal);
  el.deleteCancelBtn.addEventListener("click", closeDeleteModal);
  el.deleteConfirmBtn.addEventListener("click", confirmDeleteProject);
  el.deleteConfirmInput.addEventListener("input", () => {
    el.deleteConfirmBtn.disabled = el.deleteConfirmInput.value.trim() !== DELETE_CONFIRM_WORD;
  });
  // סגירה בלחיצה מחוץ לתוכן ה-modal (לא רק כפתור ביטול).
  el.deleteModalOverlay.addEventListener("click", (e) => {
    if (e.target === el.deleteModalOverlay) closeDeleteModal();
  });
})();
