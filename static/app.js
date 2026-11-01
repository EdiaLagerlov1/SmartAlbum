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
    // F08 (issue #9): כפולות האלבום הנוכחיות - נטען תמיד טרי מה-שרת
    // (GET /spreads), לא נשמר/נשחזר מ-localStorage; זו הדרך שבה "פתיחה
    // מחדש" משחזרת את הגרסה האחרונה שנשמרה (ראה INTERFACES.md סעיף 4.3).
    spreads: [],
    // קטלוג 6 התבניות הקבוע (GET /api/templates) - נטען פעם אחת (lazy),
    // משמש גם לציור גיאומטריית הסלוטים של כל כפולה וגם לבורר התבניות.
    templatesById: null,
    photoPicker: { spreadId: null, oldPhotoId: null },
    templatePicker: { spreadId: null, photoCount: null, currentTemplateId: null },
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
    // F08 (issue #9): תצוגת כפולות, החלפת תמונה ושינוי תבנית.
    spreadsSection: document.getElementById("spreads-section"),
    refreshSpreadsBtn: document.getElementById("refresh-spreads-btn"),
    bootstrapLayoutBtn: document.getElementById("bootstrap-layout-btn"),
    spreadsEmptyMsg: document.getElementById("spreads-empty-msg"),
    spreadsError: document.getElementById("spreads-error"),
    spreadsList: document.getElementById("spreads-list"),
    photoPickerOverlay: document.getElementById("photo-picker-overlay"),
    photoPickerGrid: document.getElementById("photo-picker-grid"),
    photoPickerError: document.getElementById("photo-picker-error"),
    photoPickerCancelBtn: document.getElementById("photo-picker-cancel-btn"),
    templatePickerOverlay: document.getElementById("template-picker-overlay"),
    templatePickerGrid: document.getElementById("template-picker-grid"),
    templatePickerError: document.getElementById("template-picker-error"),
    templatePickerCancelBtn: document.getElementById("template-picker-cancel-btn"),
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
      el.spreadsSection.hidden = false;
      el.statusSection.hidden = false;
      await loadPhotos();
      await loadStatus();
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
    el.spreadsSection.hidden = true;
    el.statusSection.hidden = true;
    el.uploadList.innerHTML = "";
    el.uploadSummary.hidden = true;
    el.photosGrid.innerHTML = "";
    el.stagesList.innerHTML = "";
    el.retryBtn.hidden = true;
    el.retryResult.hidden = true;
    el.photosFailedSummary.hidden = true;
    state.spreads = [];
    el.spreadsList.innerHTML = "";
    el.spreadsEmptyMsg.hidden = true;
    el.bootstrapLayoutBtn.hidden = true;
    el.spreadsError.hidden = true;
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

  // --------------------------------------------------------------------
  // F08 (issue #9): תצוגת 15 הכפולות, החלפת תמונה בסלוט ושינוי תבנית.
  //
  // מודל save/restore: אין endpoint "save" נפרד ואין state מקומי ששורד
  // מעבר לבקשה הנוכחית - כל קריאה מצליחה ל-replace-photo/change-template
  // נשמרת (commit) מיידית בשרת, ו"פתיחה מחדש" ("restore") היא בדיוק
  // GET טרי ל-/spreads (loadSpreads) - ראה INTERFACES.md סעיף 4.3.
  // state.spreads הוא תמיד תוצאת ה-GET האחרון, לעולם לא "דראפט" מקומי
  // שממשיך להתקיים אחרי שהבקשה חוזרת.
  // --------------------------------------------------------------------

  const REPLACE_PHOTO_ERROR_MESSAGES = {
    spread_not_found: "הכפולה המבוקשת לא נמצאה - נסו לרענן את הדף",
    old_photo_not_in_spread: "התמונה המוחלפת אינה משובצת בכפולה הזו יותר - נסו לרענן את הדף",
    new_photo_not_found: "התמונה שנבחרה לא נמצאה בתמונות הפרויקט",
    new_photo_excluded: "לא ניתן לבחור תמונה שהוחרגה (exclude) מהאלבום",
    new_photo_already_in_spread: "התמונה הזו כבר משובצת בכפולה הזו בסלוט אחר",
    new_photo_already_in_album: "התמונה הזו כבר משובצת בכפולה אחרת באלבום",
  };

  const CHANGE_TEMPLATE_ERROR_MESSAGES = {
    spread_not_found: "הכפולה המבוקשת לא נמצאה - נסו לרענן את הדף",
    template_not_found: "התבנית שנבחרה לא קיימת בקטלוג",
    slot_count_mismatch: "מספר הסלוטים בתבנית שנבחרה לא תואם למספר התמונות בכפולה הזו",
  };

  // קטלוג התבניות (GET /api/templates) - נדרש גם לציור גיאומטריית
  // הסלוטים של כל כפולה (x/y/w/h) וגם לבורר-התבניות. נטען פעם אחת (lazy)
  // כי זה קטלוג קבוע (6 תבניות, templates.json) - לא דורש רענון per-project.
  async function ensureTemplatesLoaded() {
    if (state.templatesById) return state.templatesById;
    try {
      const resp = await fetch("/api/templates");
      if (!resp.ok) return {};
      const body = await resp.json();
      const map = {};
      (body.templates || []).forEach((tpl) => {
        map[tpl.id] = tpl;
      });
      state.templatesById = map;
      return map;
    } catch (_err) {
      return {};
    }
  }

  async function loadSpreads() {
    if (!state.projectId) return;
    el.spreadsError.hidden = true;
    try {
      await ensureTemplatesLoaded();
      const resp = await fetch(`/api/projects/${state.projectId}/spreads`);
      const body = await resp.json().catch(() => null);
      if (!resp.ok) {
        el.spreadsError.hidden = false;
        el.spreadsError.textContent = (body && body.message) || "שגיאה בטעינת כפולות האלבום";
        return;
      }
      state.spreads = (body && body.spreads) || [];
      renderSpreads();
    } catch (err) {
      el.spreadsError.hidden = false;
      el.spreadsError.textContent = "שגיאת תקשורת עם השרת: " + err.message;
    }
  }

  function renderSpreads() {
    el.spreadsList.innerHTML = "";
    const hasSpreads = state.spreads.length > 0;
    el.spreadsEmptyMsg.hidden = hasSpreads;
    el.bootstrapLayoutBtn.hidden = hasSpreads;
    if (!hasSpreads) return;

    state.spreads.forEach((spread) => {
      el.spreadsList.appendChild(renderSpreadCard(spread));
    });
  }

  function renderSpreadCard(spread) {
    const card = document.createElement("div");
    card.className = "spread-card";

    const header = document.createElement("div");
    header.className = "spread-card-header";

    const title = document.createElement("span");
    title.className = "spread-title";
    title.textContent = `כפולה ${spread.position}`;

    const templateName = document.createElement("span");
    templateName.className = "spread-template-name";
    templateName.textContent = spread.template_id;

    const titleWrap = document.createElement("div");
    titleWrap.appendChild(title);
    titleWrap.appendChild(document.createTextNode(" - תבנית: "));
    titleWrap.appendChild(templateName);

    const changeTemplateBtn = document.createElement("button");
    changeTemplateBtn.type = "button";
    changeTemplateBtn.className = "btn btn-secondary";
    changeTemplateBtn.textContent = "שינוי תבנית";
    changeTemplateBtn.addEventListener("click", () => openTemplatePicker(spread));

    header.appendChild(titleWrap);
    header.appendChild(changeTemplateBtn);
    card.appendChild(header);

    const visual = document.createElement("div");
    visual.className = "spread-visual";

    const template = (state.templatesById || {})[spread.template_id];
    const slotGeomByIndex = {};
    if (template) {
      template.slots.forEach((s) => {
        slotGeomByIndex[s.index] = s;
      });
    }

    spread.photos.forEach((photo) => {
      const geom = slotGeomByIndex[photo.slot_index];
      const slotDiv = document.createElement("div");
      slotDiv.className = "spread-slot" + (photo.is_dominant ? " slot-dominant" : "");
      // מיקום/גודל הסלוט לפי x/y/w/h מ-templates.json - ערכים פיסיים
      // (left/top/width/height), לא מתהפכים תחת RTL (ראה הערה ב-style.css).
      if (geom) {
        slotDiv.style.left = geom.x * 100 + "%";
        slotDiv.style.top = geom.y * 100 + "%";
        slotDiv.style.width = geom.w * 100 + "%";
        slotDiv.style.height = geom.h * 100 + "%";
      }

      const img = document.createElement("img");
      img.src = photo.thumbnail_url || "";
      img.alt = photo.is_dominant ? "תמונה דומיננטית בכפולה" : "תמונה בכפולה";
      img.loading = "lazy";
      slotDiv.appendChild(img);

      if (photo.is_dominant) {
        const badge = document.createElement("span");
        badge.className = "slot-dominant-badge";
        badge.textContent = "דומיננטית";
        slotDiv.appendChild(badge);
      }

      const swapBtn = document.createElement("button");
      swapBtn.type = "button";
      swapBtn.className = "btn btn-secondary slot-swap-btn";
      swapBtn.textContent = "החלפת תמונה";
      swapBtn.addEventListener("click", () => openPhotoPicker(spread.spread_id, photo.photo_id));
      slotDiv.appendChild(swapBtn);

      visual.appendChild(slotDiv);
    });

    card.appendChild(visual);
    return card;
  }

  // נוחות בלבד (לא חלק מ-F08 עצמו): אין עדיין UI ל-F06 (run-selection)
  // ו-F07 (generate-layout) בפרויקט הזה, אז בלי הכפתור הזה לא תהיה שום
  // כפולה להציג/לבדוק מולה F08. קורא רק ל-endpoints קיימים - בלי לוגיקת
  // בחירה/פריסה כאן.
  async function bootstrapLayout() {
    if (!state.projectId) return;
    el.bootstrapLayoutBtn.disabled = true;
    el.spreadsError.hidden = true;
    try {
      const selResp = await fetch(`/api/projects/${state.projectId}/run-selection`, { method: "POST" });
      const selBody = await selResp.json();
      if (!selResp.ok || selBody.status !== "ok") {
        el.spreadsError.hidden = false;
        el.spreadsError.textContent = (selBody && selBody.message) || "הרצת בחירת התמונות נכשלה";
        return;
      }

      const layoutResp = await fetch(`/api/projects/${state.projectId}/generate-layout`, { method: "POST" });
      const layoutBody = await layoutResp.json();
      if (!layoutResp.ok || layoutBody.status !== "ok") {
        el.spreadsError.hidden = false;
        el.spreadsError.textContent = (layoutBody && layoutBody.message) || "יצירת הכפולות נכשלה";
        return;
      }

      await loadSpreads();
    } catch (err) {
      el.spreadsError.hidden = false;
      el.spreadsError.textContent = "שגיאת תקשורת עם השרת: " + err.message;
    } finally {
      el.bootstrapLayoutBtn.disabled = false;
    }
  }

  // --------------------------------------------------------------------
  // F08: בורר תמונה להחלפה - גריד thumbnails מתוך תמונות הפרויקט שלא
  // הוחרגו (flag !== 'exclude'); השרת אוכף את זה שוב (new_photo_excluded)
  // כהגנת-עומק, זה לא רק ולידציה בצד לקוח.
  // --------------------------------------------------------------------
  async function openPhotoPicker(spreadId, oldPhotoId) {
    state.photoPicker = { spreadId, oldPhotoId };
    el.photoPickerError.hidden = true;
    el.photoPickerGrid.innerHTML = '<p class="muted">טוען תמונות...</p>';
    el.photoPickerOverlay.hidden = false;

    try {
      const resp = await fetch(`/api/projects/${state.projectId}/photos`);
      if (!resp.ok) {
        el.photoPickerGrid.innerHTML = "";
        el.photoPickerError.hidden = false;
        el.photoPickerError.textContent = "שגיאה בטעינת תמונות הפרויקט";
        return;
      }
      const photos = await resp.json();
      const selectable = photos.filter((p) => p.flag !== "exclude" && p.status === "done");
      renderPhotoPickerGrid(selectable);
    } catch (err) {
      el.photoPickerGrid.innerHTML = "";
      el.photoPickerError.hidden = false;
      el.photoPickerError.textContent = "שגיאת תקשורת עם השרת: " + err.message;
    }
  }

  function renderPhotoPickerGrid(photos) {
    el.photoPickerGrid.innerHTML = "";
    if (photos.length === 0) {
      el.photoPickerGrid.innerHTML =
        '<p class="muted">אין תמונות זמינות להחלפה (כל התמונות הוחרגו, או שעיבודן עדיין לא הושלם).</p>';
      return;
    }

    photos.forEach((photo) => {
      const card = document.createElement("div");
      card.className = "photo-card picker-thumb";
      if (photo.photo_id === state.photoPicker.oldPhotoId) {
        card.classList.add("picker-thumb-current");
        card.title = "זו התמונה הנוכחית בסלוט";
      }

      const img = document.createElement("img");
      img.src = photo.thumbnail_url || "";
      img.alt = photo.original_filename;
      img.loading = "lazy";

      const meta = document.createElement("div");
      meta.className = "photo-meta";
      meta.textContent = photo.original_filename;

      card.appendChild(img);
      card.appendChild(meta);
      card.addEventListener("click", () => doReplacePhoto(photo.photo_id));
      el.photoPickerGrid.appendChild(card);
    });
  }

  async function doReplacePhoto(newPhotoId) {
    const { spreadId, oldPhotoId } = state.photoPicker;
    if (!spreadId || !oldPhotoId) return;

    el.photoPickerError.hidden = true;
    try {
      const resp = await fetch(`/api/projects/${state.projectId}/spreads/${spreadId}/replace-photo`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_photo_id: oldPhotoId, new_photo_id: newPhotoId }),
      });
      const body = await resp.json();
      if (!resp.ok || body.status !== "ok") {
        el.photoPickerError.hidden = false;
        el.photoPickerError.textContent =
          body.message || REPLACE_PHOTO_ERROR_MESSAGES[body.error_reason] || "החלפת התמונה נכשלה";
        return;
      }

      closePhotoPicker();
      await loadSpreads(); // "שמירה" כבר בוצעה בשרת - זו רק רענון התצוגה.
    } catch (err) {
      el.photoPickerError.hidden = false;
      el.photoPickerError.textContent = "שגיאת תקשורת עם השרת: " + err.message;
    }
  }

  function closePhotoPicker() {
    el.photoPickerOverlay.hidden = true;
    state.photoPicker = { spreadId: null, oldPhotoId: null };
  }

  // --------------------------------------------------------------------
  // F08: בורר תבנית - מציג את כל 6 התבניות, מנטרל (עם tooltip) תבניות
  // שמספר הסלוטים שלהן לא תואם למספר התמונות הנוכחי בכפולה (לפי
  // slot_count_mismatch שהשרת אוכף; כאן רק UX מקדים, לא ולידציה יחידה).
  // --------------------------------------------------------------------
  async function openTemplatePicker(spread) {
    state.templatePicker = {
      spreadId: spread.spread_id,
      photoCount: spread.photos.length,
      currentTemplateId: spread.template_id,
    };
    el.templatePickerError.hidden = true;
    el.templatePickerGrid.innerHTML = '<p class="muted">טוען תבניות...</p>';
    el.templatePickerOverlay.hidden = false;

    const templatesById = await ensureTemplatesLoaded();
    renderTemplatePickerGrid(Object.values(templatesById));
  }

  function renderTemplatePickerGrid(templates) {
    el.templatePickerGrid.innerHTML = "";
    const { photoCount, currentTemplateId } = state.templatePicker;

    templates.forEach((tpl) => {
      const compatible = tpl.slot_count === photoCount;
      const card = document.createElement("div");
      card.className =
        "template-picker-card" +
        (compatible ? "" : " disabled") +
        (tpl.id === currentTemplateId ? " template-picker-card-current" : "");
      if (!compatible) {
        card.title = `תבנית זו מתאימה ל-${tpl.slot_count} תמונות, ולכפולה הזו יש ${photoCount} תמונות`;
      } else if (tpl.id === currentTemplateId) {
        card.title = "זו התבנית הנוכחית של הכפולה";
      }

      const preview = document.createElement("div");
      preview.className = "template-preview";
      (tpl.slots || []).forEach((slot) => {
        const slotDiv = document.createElement("div");
        slotDiv.className = "template-preview-slot" + (slot.dominant ? " dominant" : "");
        slotDiv.style.left = slot.x * 100 + "%";
        slotDiv.style.top = slot.y * 100 + "%";
        slotDiv.style.width = slot.w * 100 + "%";
        slotDiv.style.height = slot.h * 100 + "%";
        preview.appendChild(slotDiv);
      });

      const label = document.createElement("div");
      label.className = "template-picker-label";
      label.textContent = `${tpl.id} (${tpl.slot_count})`;

      card.appendChild(preview);
      card.appendChild(label);

      if (compatible) {
        card.addEventListener("click", () => doChangeTemplate(tpl.id));
      }

      el.templatePickerGrid.appendChild(card);
    });
  }

  async function doChangeTemplate(templateId) {
    const { spreadId } = state.templatePicker;
    if (!spreadId) return;

    el.templatePickerError.hidden = true;
    try {
      const resp = await fetch(`/api/projects/${state.projectId}/spreads/${spreadId}/change-template`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template_id: templateId }),
      });
      const body = await resp.json();
      if (!resp.ok || body.status !== "ok") {
        el.templatePickerError.hidden = false;
        el.templatePickerError.textContent =
          body.message || CHANGE_TEMPLATE_ERROR_MESSAGES[body.error_reason] || "שינוי התבנית נכשל";
        return;
      }

      closeTemplatePicker();
      await loadSpreads(); // "שמירה" כבר בוצעה בשרת - זו רק רענון התצוגה.
    } catch (err) {
      el.templatePickerError.hidden = false;
      el.templatePickerError.textContent = "שגיאת תקשורת עם השרת: " + err.message;
    }
  }

  function closeTemplatePicker() {
    el.templatePickerOverlay.hidden = true;
    state.templatePicker = { spreadId: null, photoCount: null, currentTemplateId: null };
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

  // F08 (issue #9): רענון כפולות, בורר-תמונה, בורר-תבנית.
  el.refreshSpreadsBtn.addEventListener("click", loadSpreads);
  el.bootstrapLayoutBtn.addEventListener("click", bootstrapLayout);
  el.photoPickerCancelBtn.addEventListener("click", closePhotoPicker);
  el.photoPickerOverlay.addEventListener("click", (e) => {
    if (e.target === el.photoPickerOverlay) closePhotoPicker();
  });
  el.templatePickerCancelBtn.addEventListener("click", closeTemplatePicker);
  el.templatePickerOverlay.addEventListener("click", (e) => {
    if (e.target === el.templatePickerOverlay) closeTemplatePicker();
  });
})();
