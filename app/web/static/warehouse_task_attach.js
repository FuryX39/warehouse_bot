(function (global) {
  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function shell() {
    return global.WH_SHELL || {};
  }

  function fetchJson(url, options) {
    return shell().fetchJson(url, options);
  }

  function ensurePdfFilename(name) {
    var base = String(name || "file.pdf").trim() || "file.pdf";
    if (!base.toLowerCase().endsWith(".pdf")) base += ".pdf";
    return base.replace(/[\\/:*?"<>|]+/g, "_").slice(0, 200);
  }

  function filenameFromDisposition(dispo, fallback) {
    var filename = fallback || "file.pdf";
    var m = String(dispo || "").match(/filename="([^"]+)"/);
    if (m) filename = m[1];
    return ensurePdfFilename(filename);
  }

  function parsePdfResponse(response, fallbackName) {
    var dispo = response.headers.get("Content-Disposition") || "";
    var filename = filenameFromDisposition(dispo, fallbackName);
    return response.blob().then(function (blob) {
      return { blob: blob, filename: filename };
    });
  }

  function downloadPdfResult(result) {
    if (!result || !result.blob) return;
    var url = URL.createObjectURL(result.blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = ensurePdfFilename(result.filename);
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 500);
  }

  function formatTaskLabel(task) {
    var parts = ["#" + task.id];
    if (task.counterparty_name) parts.push(task.counterparty_name);
    if (task.start_date) parts.push("сборка " + task.start_date);
    if (task.end_date) parts.push("отгрузка " + task.end_date);
    if (task.task_type_name) parts.push(task.task_type_name);
    return parts.join(" · ");
  }

  function searchTasks(query) {
    var params = new URLSearchParams();
    params.set("limit", "30");
    params.set("sort_by", "start_date");
    params.set("sort_dir", "desc");
    if (query) params.set("q", query);
    return fetchJson("/api/warehouse/tasks?" + params.toString()).then(function (data) {
      return data.tasks || [];
    });
  }

  function uploadAttachment(taskId, kind, filename, blob) {
    var formData = new FormData();
    formData.append("kind", kind);
    formData.append("file", blob, ensurePdfFilename(filename));
    return fetch("/api/warehouse/tasks/" + taskId + "/attachments", {
      method: "POST",
      credentials: "include",
      body: formData,
    }).then(function (response) {
      if (response.ok) return response.json();
      return response.text().then(function (text) {
        var detail = text || "HTTP " + response.status;
        try {
          var json = JSON.parse(text);
          if (json && json.detail) {
            detail = typeof json.detail === "string" ? json.detail : String(json.detail);
          }
        } catch (e) {}
        throw new Error(detail);
      });
    });
  }

  function openModal(options) {
    var pdfBlob = options && options.pdfBlob;
    var defaultFilename = ensurePdfFilename((options && options.defaultFilename) || "file.pdf");
    var defaultKind = (options && options.defaultKind) || "a4";
    var onSuccess = options && options.onSuccess;
    if (!pdfBlob) {
      alert("PDF не найден — сначала сформируйте файл.");
      return;
    }

    var selectedTaskId = null;
    var backdrop = document.createElement("div");
    backdrop.className = "wh-modal-backdrop";
    backdrop.innerHTML =
      '<div class="wh-modal wh-modal-wide" role="dialog">' +
      '<div class="wh-modal-header"><h3>Привязать PDF к задаче</h3>' +
      '<button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body">' +
      '<p class="wh-muted">Выберите задачу и укажите имя файла — PDF будет прикреплён для печати упаковщиком.</p>' +
      '<label class="wh-modal-row"><span>Поиск задачи</span>' +
      '<input type="search" id="whTaskAttachSearch" placeholder="Контрагент, тип, комментарий, № задачи…" autocomplete="off" /></label>' +
      '<div class="wh-task-attach-actions">' +
      '<button type="button" class="wh-btn wh-btn-sm" id="whTaskAttachSearchBtn">Найти</button>' +
      "</div>" +
      '<div id="whTaskAttachResults" class="wh-rc-search-results wh-task-attach-results">' +
      '<p class="wh-muted">Введите запрос и нажмите «Найти», или оставьте поле пустым для последних задач.</p>' +
      "</div>" +
      '<label class="wh-modal-row"><span>Имя файла</span>' +
      '<input type="text" id="whTaskAttachFilename" value="' +
      esc(defaultFilename) +
      '" maxlength="200" /></label>' +
      '<label class="wh-modal-row"><span>Тип печати</span>' +
      '<select id="whTaskAttachKind">' +
      '<option value="a4"' +
      (defaultKind === "a4" ? " selected" : "") +
      ">А4</option>" +
      '<option value="label"' +
      (defaultKind === "label" ? " selected" : "") +
      ">Этикетки</option>" +
      "</select></label>" +
      '<p id="whTaskAttachMsg" class="wh-msg"></p>' +
      "</div>" +
      '<div class="wh-modal-footer">' +
      '<button type="button" class="wh-btn wh-btn-primary wh-modal-save">Сохранить</button>' +
      '<button type="button" class="wh-btn wh-modal-cancel">Отмена</button>' +
      "</div></div>";
    document.body.appendChild(backdrop);

    var searchInput = backdrop.querySelector("#whTaskAttachSearch");
    var resultsEl = backdrop.querySelector("#whTaskAttachResults");
    var msgEl = backdrop.querySelector("#whTaskAttachMsg");
    var saveBtn = backdrop.querySelector(".wh-modal-save");

    function close() {
      backdrop.remove();
    }

    function setMsg(text, isError) {
      if (!msgEl) return;
      msgEl.className = isError ? "wh-msg wh-msg-error" : "wh-msg wh-msg-ok";
      msgEl.textContent = text || "";
    }

    function renderResults(tasks) {
      if (!tasks.length) {
        resultsEl.innerHTML = '<p class="wh-muted">Задачи не найдены.</p>';
        return;
      }
      resultsEl.innerHTML = tasks
        .map(function (task) {
          var active = selectedTaskId === task.id ? " wh-task-attach-pick--active" : "";
          return (
            '<button type="button" class="wh-rc-search-item wh-task-attach-pick' +
            active +
            '" data-id="' +
            esc(task.id) +
            '">' +
            '<span class="wh-rc-search-text">' +
            esc(formatTaskLabel(task)) +
            "</span></button>"
          );
        })
        .join("");
      resultsEl.querySelectorAll(".wh-task-attach-pick").forEach(function (btn) {
        btn.addEventListener("click", function () {
          selectedTaskId = parseInt(btn.getAttribute("data-id") || "", 10) || null;
          renderResults(tasks);
          setMsg("", false);
        });
      });
    }

    function runSearch() {
      setMsg("", false);
      resultsEl.innerHTML = '<p class="wh-muted">Поиск...</p>';
      searchTasks(searchInput.value.trim())
        .then(renderResults)
        .catch(function (err) {
          resultsEl.innerHTML =
            '<p class="wh-msg wh-msg-error">' + esc(err.message || String(err)) + "</p>";
        });
    }

    backdrop.querySelector(".wh-modal-close").addEventListener("click", close);
    backdrop.querySelector(".wh-modal-cancel").addEventListener("click", close);
    backdrop.addEventListener("click", function (e) {
      if (e.target === backdrop) close();
    });
    backdrop.querySelector("#whTaskAttachSearchBtn").addEventListener("click", runSearch);
    searchInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        runSearch();
      }
    });

    saveBtn.addEventListener("click", function () {
      if (!selectedTaskId) {
        setMsg("Выберите задачу из списка.", true);
        return;
      }
      var filename = backdrop.querySelector("#whTaskAttachFilename").value.trim();
      if (!filename) {
        setMsg("Укажите имя файла.", true);
        return;
      }
      var kind = backdrop.querySelector("#whTaskAttachKind").value || "a4";
      saveBtn.disabled = true;
      setMsg("Сохраняем...", false);
      uploadAttachment(selectedTaskId, kind, filename, pdfBlob)
        .then(function (data) {
          close();
          if (onSuccess) onSuccess(data, selectedTaskId);
        })
        .catch(function (err) {
          saveBtn.disabled = false;
          setMsg(err.message || String(err), true);
        });
    });

    runSearch();
    searchInput.focus();
  }

  global.WhTaskAttach = {
    ensurePdfFilename: ensurePdfFilename,
    parsePdfResponse: parsePdfResponse,
    downloadPdfResult: downloadPdfResult,
    openModal: openModal,
  };
})(window);
