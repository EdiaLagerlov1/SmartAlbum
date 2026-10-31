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
  };

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
      el.projectInfoName.textContent = body.name;
      el.projectInfoId.textContent = body.project_id;
      el.projectInfo.hidden = false;
      el.uploadSection.hidden = false;
      el.photosSection.hidden = false;
      await loadPhotos();
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

  el.createProjectBtn.addEventListener("click", createProject);
  el.refreshPhotosBtn.addEventListener("click", loadPhotos);
})();
