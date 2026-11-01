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
    // F08 (issue #9): תמונות הפרויקט (מ-loadPhotos) - נדרש כדי לבנות את
    // רשימת "התמונות המועמדות להחלפה" (רק flag !== 'exclude', ראה החוזה
    // המחייב ב-layout_engine.api.replace_photo_in_slot/INTERFACES.md 4.3),
    // ו-templates (מ-/api/templates) - נדרש לתפריט "שינוי תבנית".
    photos: [],
    templates: [],
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
    // F08 (issue #9): כפולות האלבום.
    spreadsSection: document.getElementById("spreads-section"),
    refreshSpreadsBtn: document.getElementById("refresh-spreads-btn"),
    regenerateLayoutBtn: document.getElementById("regenerate-layout-btn"),
    showPrevSnapshotBtn: document.getElementById("show-prev-snapshot-btn"),
    spreadsEmptyMsg: document.getElementById("spreads-empty-msg"),
    regenerateResult: document.getElementById("regenerate-result"),
    prevSnapshotBox: document.getElementById("prev-snapshot-box"),
    prevSnapshotContent: document.getElementById("prev-snapshot-content"),
    spreadsList: document.getElementById("spreads-list"),
    regenerateModalOverlay: document.getElementById("regenerate-modal-overlay"),
    regenerateModalError: document.getElementById("regenerate-modal-error"),
    regenerateConfirmBtn: document.getElementById("regenerate-confirm-btn"),
    regenerateCancelBtn: document.getElementById("regenerate-cancel-btn"),
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
      el.spreadsSection.hidden = false;
      el.statusSection.hidden = false;
      await loadPhotos();
      await loadStatus();
      await loadTemplatesCatalog();
      // F08: פתיחה מחדש של פרויקט משחזרת את הגרסה האחרונה שנשמרה -
      // loadSpreads() קוראת ישירות מה-DB (GET /spreads), כך שתמיד משקפת
      // את מה שנשמר אחרון (אין "draft" מקומי שצריך לשחזר בנפרד).
      await loadSpreads();
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
    state.photos = photos; // F08: נדרש לבניית רשימת המועמדות ל"החלפת תמונה"
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
    state.photos = [];
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
    // F08 (issue #9): איפוס מצב הכפולות.
    el.spreadsSection.hidden = true;
    el.spreadsList.innerHTML = "";
    el.spreadsEmptyMsg.hidden = true;
    el.regenerateResult.hidden = true;
    el.prevSnapshotBox.hidden = true;
    el.prevSnapshotContent.textContent = "";
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

  // --------------------------------------------------------------------
  // F08 (issue #9): כפולות האלבום - החלפת תמונה, שינוי תבנית, יצירה מחדש
  // (עם modal אישור מפורש לפי INTERFACES.md סעיף 6) ושחזור-ידני (קריאה
  // בלבד) של הגיבוי האחרון.
  //
  // כל פעולת עריכה כאן (החלפה/שינוי תבנית/יצירה מחדש) היא שמירה מפורשת -
  // נכתבת ל-DB עם לחיצת הכפתור הרלוונטית בלבד, בלי auto-save ברקע.
  // פתיחה מחדש של הפרויקט (createProject -> loadSpreads) קוראת תמיד
  // ישירות מה-DB, כך שמשקפת את "הגרסה האחרונה שנשמרה" (אין state מקומי
  // שצריך לשחזר בנפרד ב-localStorage/sessionStorage).
  // --------------------------------------------------------------------
  async function loadTemplatesCatalog() {
    try {
      const resp = await fetch("/api/templates");
      if (!resp.ok) return;
      const body = await resp.json();
      state.templates = body.templates || [];
    } catch (_err) {
      // שקט - נוחות ל-UI (תפריט "שינוי תבנית"), לא קריטי לשאר הזרימה.
    }
  }

  function nonExcludedPhotoOptions(selectedPhotoId) {
    // "רק מתוך תמונות הפרויקט שלא הוחרגו" - ולידציית-צד-לקוח, השרת הוא
    // מקור האמת (layout_engine.api.replace_photo_in_slot דוחה exclude בכל
    // מקרה, ראה INTERFACES.md סעיף 4.3) - כאן רק לא מציעים אותן בתפריט.
    return state.photos
      .filter((p) => p.flag !== "exclude")
      .map((p) => {
        const opt = document.createElement("option");
        opt.value = p.photo_id;
        opt.textContent = p.original_filename + (p.photo_id === selectedPhotoId ? " (נוכחית)" : "");
        if (p.photo_id === selectedPhotoId) opt.selected = true;
        return opt;
      });
  }

  function templateOptions(selectedTemplateId) {
    return state.templates.map((tpl) => {
      const opt = document.createElement("option");
      opt.value = tpl.id;
      opt.textContent = `${tpl.id} (${tpl.slot_count} תמונות)`;
      if (tpl.id === selectedTemplateId) opt.selected = true;
      return opt;
    });
  }

  function renderSpreadCard(spread) {
    const card = document.createElement("div");
    card.className = "spread-card";

    const header = document.createElement("div");
    header.className = "spread-card-header";
    header.innerHTML =
      `<span>כפולה ${spread.position}</span>` +
      `<span class="muted">תבנית נוכחית: ${spread.template_id}</span>` +
      `<span class="muted">גרסה: ${spread.version}</span>`;
    card.appendChild(header);

    const photosRow = document.createElement("div");
    photosRow.className = "spread-photos";

    spread.photos.forEach((photo) => {
      const slotBox = document.createElement("div");
      slotBox.className = "spread-photo-slot";

      const img = document.createElement("img");
      img.src = photo.thumbnail_url || "";
      img.alt = photo.original_filename || "";
      img.loading = "lazy";

      const label = document.createElement("span");
      label.className = "slot-label" + (photo.is_dominant ? " dominant" : "");
      label.textContent = `סלוט ${photo.slot_index}` + (photo.is_dominant ? " - דומיננטי" : "");

      const select = document.createElement("select");
      nonExcludedPhotoOptions(photo.photo_id).forEach((opt) => select.appendChild(opt));

      const replaceBtn = document.createElement("button");
      replaceBtn.className = "btn btn-secondary";
      replaceBtn.textContent = "החלפת תמונה בסלוט זה";
      replaceBtn.addEventListener("click", () =>
        replacePhotoInSlot(spread.spread_id, photo.slot_index, select.value, slotResultP)
      );

      const slotResultP = document.createElement("p");
      slotResultP.className = "spread-action-result muted";
      slotResultP.hidden = true;

      slotBox.appendChild(img);
      slotBox.appendChild(label);
      slotBox.appendChild(select);
      slotBox.appendChild(replaceBtn);
      slotBox.appendChild(slotResultP);
      photosRow.appendChild(slotBox);
    });

    card.appendChild(photosRow);

    const templateRow = document.createElement("div");
    templateRow.className = "spread-actions-row";

    const templateLabel = document.createElement("label");
    templateLabel.textContent = "שינוי תבנית:";

    const templateSelect = document.createElement("select");
    templateOptions(spread.template_id).forEach((opt) => templateSelect.appendChild(opt));

    const templateBtn = document.createElement("button");
    templateBtn.className = "btn btn-secondary";
    templateBtn.textContent = "החלת תבנית";

    const templateResultP = document.createElement("p");
    templateResultP.className = "spread-action-result muted";
    templateResultP.hidden = true;

    templateBtn.addEventListener("click", () =>
      changeSpreadTemplate(spread.spread_id, templateSelect.value, templateResultP)
    );

    templateRow.appendChild(templateLabel);
    templateRow.appendChild(templateSelect);
    templateRow.appendChild(templateBtn);
    card.appendChild(templateRow);
    card.appendChild(templateResultP);

    return card;
  }

  function renderSpreads(spreads) {
    el.spreadsList.innerHTML = "";
    el.spreadsEmptyMsg.hidden = spreads.length !== 0;
    spreads.forEach((spread) => el.spreadsList.appendChild(renderSpreadCard(spread)));
  }

  async function loadSpreads() {
    if (!state.projectId) return;
    try {
      const resp = await fetch(`/api/projects/${state.projectId}/spreads`);
      if (!resp.ok) return;
      const body = await resp.json();
      renderSpreads(body.spreads || []);
    } catch (_err) {
      // שקט - כמו loadPhotos/loadStatus מעלה.
    }
  }

  async function replacePhotoInSlot(spreadId, slotIndex, newPhotoId, resultEl) {
    resultEl.hidden = true;
    try {
      const resp = await fetch(
        `/api/projects/${state.projectId}/spreads/${spreadId}/replace-photo`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ slot_index: slotIndex, new_photo_id: newPhotoId }),
        }
      );
      const body = await resp.json();
      const ok = resp.ok && body.status === "ok";
      resultEl.hidden = false;
      resultEl.textContent = ok ? "התמונה הוחלפה בהצלחה." : (body.message || "ההחלפה נכשלה");
      resultEl.style.color = ok ? "" : "#b42318";
      if (ok) await loadSpreads();
    } catch (err) {
      resultEl.hidden = false;
      resultEl.textContent = "שגיאת תקשורת עם השרת: " + err.message;
      resultEl.style.color = "#b42318";
    }
  }

  async function changeSpreadTemplate(spreadId, newTemplateId, resultEl) {
    resultEl.hidden = true;
    try {
      const resp = await fetch(
        `/api/projects/${state.projectId}/spreads/${spreadId}/change-template`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ new_template_id: newTemplateId }),
        }
      );
      const body = await resp.json();
      const ok = resp.ok && body.status === "ok";
      resultEl.hidden = false;
      resultEl.textContent = ok ? "התבנית עודכנה בהצלחה." : (body.message || "שינוי התבנית נכשל");
      resultEl.style.color = ok ? "" : "#b42318";
      if (ok) await loadSpreads();
    } catch (err) {
      resultEl.hidden = false;
      resultEl.textContent = "שגיאת תקשורת עם השרת: " + err.message;
      resultEl.style.color = "#b42318";
    }
  }

  // F08: "יצירה מחדש" - modal אישור מפורש (INTERFACES.md סעיף 6, שלב 1)
  // *לפני* הקריאה ל-endpoint. ה-endpoint עצמו (main.py) מריץ בצד השרת,
  // באותה בקשה, קודם export_agent.snapshot_structure ורק אז
  // layout_engine.regenerate_layout (שלבים 2-3) - אין קריאת HTTP נפרדת
  // מכאן ל-snapshot, כדי שלא יהיה מצב-ביניים שבו regenerate רץ בלי גיבוי.
  function openRegenerateModal() {
    el.regenerateModalError.hidden = true;
    el.regenerateModalOverlay.hidden = false;
  }

  function closeRegenerateModal() {
    el.regenerateModalOverlay.hidden = true;
  }

  async function confirmRegenerateLayout() {
    if (!state.projectId) return;
    el.regenerateConfirmBtn.disabled = true;
    try {
      const resp = await fetch(`/api/projects/${state.projectId}/regenerate-layout`, {
        method: "POST",
      });
      const body = await resp.json();
      if (!resp.ok) {
        el.regenerateModalError.hidden = false;
        el.regenerateModalError.textContent = (body && body.message) || "יצירה מחדש נכשלה";
        return;
      }

      closeRegenerateModal();
      el.regenerateResult.hidden = false;
      el.regenerateResult.textContent =
        body.status === "ok"
          ? `נוצרו מחדש ${body.spread_count} כפולות. הגרסה הקודמת נשמרה לגיבוי לפני המחיקה (שחזור ידני בלבד).`
          : (body.message || "יצירה מחדש נכשלה");

      await loadSpreads();
    } catch (err) {
      el.regenerateModalError.hidden = false;
      el.regenerateModalError.textContent = "שגיאת תקשורת עם השרת: " + err.message;
    } finally {
      el.regenerateConfirmBtn.disabled = false;
    }
  }

  // F08 (סעיף 6, שלב 4): "אין undo אוטומטי בגרסת הבסיס; השחזור ידני מתוך
  // album_structure_prev.json" - הכפתור הזה רק *מציג* את תוכן קובץ הגיבוי
  // (קריאה בלבד) כדי שאפשר יהיה לעיין בו/לשחזר ממנו ידנית; אין כאן כפתור
  // "שחזור" אוטומטי בכוונה.
  async function showPrevSnapshot() {
    if (!state.projectId) return;
    try {
      const resp = await fetch(`/api/projects/${state.projectId}/structure-prev`);
      const body = await resp.json();
      el.prevSnapshotBox.hidden = false;
      if (!resp.ok) {
        el.prevSnapshotContent.textContent = (body && body.message) || "אין גיבוי שמור";
        return;
      }
      el.prevSnapshotContent.textContent = JSON.stringify(body, null, 2);
    } catch (err) {
      el.prevSnapshotBox.hidden = false;
      el.prevSnapshotContent.textContent = "שגיאת תקשורת עם השרת: " + err.message;
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

  // F08 (issue #9): כפולות האלבום.
  el.refreshSpreadsBtn.addEventListener("click", loadSpreads);
  el.regenerateLayoutBtn.addEventListener("click", openRegenerateModal);
  el.regenerateCancelBtn.addEventListener("click", closeRegenerateModal);
  el.regenerateConfirmBtn.addEventListener("click", confirmRegenerateLayout);
  el.showPrevSnapshotBtn.addEventListener("click", showPrevSnapshot);
  el.regenerateModalOverlay.addEventListener("click", (e) => {
    if (e.target === el.regenerateModalOverlay) closeRegenerateModal();
  });
})();
