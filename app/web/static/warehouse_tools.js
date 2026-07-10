(function (global) {
  var fileQueue = [];

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

  function panelEl() {
    return shell().contentPanelEl;
  }

  function preparePanel(tab, item) {
    shell().contentTitleEl.textContent = item.title;
    shell().contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    shell().contentPlaceholderEl.hidden = true;
    panelEl().hidden = false;
    var card = document.querySelector(".wh-content-card");
    if (card) card.classList.add("wh-content-card--wide");
  }

  function formatSize(bytes) {
    var n = Number(bytes) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function isPdfFile(file) {
    if (!file) return false;
    var name = String(file.name || "").toLowerCase();
    return file.type === "application/pdf" || name.endsWith(".pdf");
  }

  function renderFileList(root) {
    var listEl = root.querySelector("#whPdfMergeList");
    var mergeBtn = root.querySelector("#whPdfMergeBtn");
    if (!listEl) return;
    if (!fileQueue.length) {
      listEl.innerHTML = '<p class="wh-muted">Файлы пока не выбраны.</p>';
      if (mergeBtn) mergeBtn.disabled = true;
      return;
    }
    listEl.innerHTML =
      '<ol class="wh-pdf-merge-list">' +
      fileQueue
        .map(function (file, idx) {
          return (
            '<li class="wh-pdf-merge-item">' +
            '<span class="wh-pdf-merge-index">' +
            (idx + 1) +
            ".</span>" +
            '<span class="wh-pdf-merge-name" title="' +
            esc(file.name) +
            '">' +
            esc(file.name) +
            "</span>" +
            '<span class="wh-pdf-merge-size">' +
            esc(formatSize(file.size)) +
            "</span>" +
            '<button type="button" class="wh-btn wh-btn-sm wh-pdf-merge-remove" data-idx="' +
            idx +
            '">Убрать</button>' +
            "</li>"
          );
        })
        .join("") +
      "</ol>";
    if (mergeBtn) mergeBtn.disabled = false;
    listEl.querySelectorAll(".wh-pdf-merge-remove").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var idx = parseInt(btn.getAttribute("data-idx") || "", 10);
        if (idx >= 0 && idx < fileQueue.length) {
          fileQueue.splice(idx, 1);
          renderFileList(root);
        }
      });
    });
  }

  function addFiles(fileList, root) {
    var added = 0;
    Array.prototype.forEach.call(fileList || [], function (file) {
      if (!isPdfFile(file)) return;
      fileQueue.push(file);
      added += 1;
    });
    if (fileList && fileList.length && !added) {
      var msg = root.querySelector("#whPdfMergeMsg");
      if (msg) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Можно загружать только PDF-файлы.";
      }
      return;
    }
    renderFileList(root);
  }

  function downloadBlob(response) {
    return response.blob().then(function (blob) {
      var dispo = response.headers.get("Content-Disposition") || "";
      var filename = "merged.pdf";
      var m = dispo.match(/filename="([^"]+)"/);
      if (m) filename = m[1];
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () {
        URL.revokeObjectURL(url);
      }, 500);
    });
  }

  function renderPdfMerge(tab, item) {
    fileQueue = [];
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML =
      '<div class="wh-tools-card">' +
      '<p class="wh-muted">Загрузите один или несколько PDF. Страницы объединятся в том порядке, в котором файлы добавлены в список.</p>' +
      '<div class="wh-pdf-merge-actions">' +
      '<label class="wh-import-file-label">Добавить PDF<input type="file" id="whPdfMergeInput" accept=".pdf,application/pdf" multiple /></label>' +
      '<button type="button" class="wh-btn" id="whPdfMergeClear" disabled>Очистить список</button>' +
      "</div>" +
      '<div id="whPdfMergeList" class="wh-pdf-merge-list-wrap"><p class="wh-muted">Файлы пока не выбраны.</p></div>' +
      '<div class="wh-tools-actions">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whPdfMergeBtn" disabled>Объединить PDF</button>' +
      '<span id="whPdfMergeMsg" class="wh-msg"></span>' +
      "</div>" +
      "</div>";
    var input = root.querySelector("#whPdfMergeInput");
    var clearBtn = root.querySelector("#whPdfMergeClear");
    var mergeBtn = root.querySelector("#whPdfMergeBtn");
    var msg = root.querySelector("#whPdfMergeMsg");

    function syncClearBtn() {
      if (clearBtn) clearBtn.disabled = !fileQueue.length;
    }

    input.addEventListener("change", function () {
      addFiles(input.files, root);
      input.value = "";
      syncClearBtn();
      if (msg) {
        msg.className = "wh-msg";
        msg.textContent = "";
      }
    });

    clearBtn.addEventListener("click", function () {
      fileQueue = [];
      renderFileList(root);
      syncClearBtn();
      if (msg) {
        msg.className = "wh-msg";
        msg.textContent = "";
      }
    });

    mergeBtn.addEventListener("click", function () {
      if (!fileQueue.length) return;
      msg.className = "wh-msg";
      msg.textContent = "Объединяем PDF...";
      mergeBtn.disabled = true;
      var formData = new FormData();
      fileQueue.forEach(function (file) {
        formData.append("files", file, file.name);
      });
      fetch("/api/warehouse/tools/pdf-merge", {
        method: "POST",
        credentials: "include",
        body: formData,
      })
        .then(function (r) {
          if (r.ok) return downloadBlob(r);
          return r.text().then(function (text) {
            var detail = text || "HTTP " + r.status;
            try {
              var json = JSON.parse(text);
              if (json && json.detail) {
                detail = typeof json.detail === "string" ? json.detail : String(json.detail);
              }
            } catch (e) {}
            throw new Error(detail);
          });
        })
        .then(function () {
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = "PDF сформирован и скачан.";
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || String(err);
        })
        .finally(function () {
          mergeBtn.disabled = !fileQueue.length;
        });
    });

    renderFileList(root);
    syncClearBtn();
  }

  function render(tab, item) {
    if (item.id === "pdf-merge") {
      renderPdfMerge(tab, item);
      return;
    }
    preparePanel(tab, item);
    panelEl().hidden = false;
    shell().contentPlaceholderEl.hidden = false;
    shell().contentPlaceholderEl.textContent = "Раздел в разработке.";
  }

  global.WhTools = { render: render };
})(window);
