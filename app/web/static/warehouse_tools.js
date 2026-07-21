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
    return global.WhTaskAttach.parsePdfResponse(response, "merged.pdf").then(function (result) {
      global.WhTaskAttach.downloadPdfResult(result);
      return result;
    });
  }

  function renderPdfMerge(tab, item) {
    fileQueue = [];
    var lastPdfResult = null;
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
      '<button type="button" class="wh-btn" id="whPdfMergeAttachBtn" disabled>Привязать к задаче</button>' +
      '<span id="whPdfMergeMsg" class="wh-msg"></span>' +
      "</div>" +
      "</div>";
    var input = root.querySelector("#whPdfMergeInput");
    var clearBtn = root.querySelector("#whPdfMergeClear");
    var mergeBtn = root.querySelector("#whPdfMergeBtn");
    var attachBtn = root.querySelector("#whPdfMergeAttachBtn");
    var msg = root.querySelector("#whPdfMergeMsg");

    function syncClearBtn() {
      if (clearBtn) clearBtn.disabled = !fileQueue.length;
    }

    function syncAttachBtn() {
      if (attachBtn) attachBtn.disabled = !lastPdfResult;
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
      lastPdfResult = null;
      renderFileList(root);
      syncClearBtn();
      syncAttachBtn();
      if (msg) {
        msg.className = "wh-msg";
        msg.textContent = "";
      }
    });

    if (attachBtn) {
      attachBtn.addEventListener("click", function () {
        if (!lastPdfResult) return;
        global.WhTaskAttach.openModal({
          pdfBlob: lastPdfResult.blob,
          defaultFilename: lastPdfResult.filename,
          defaultKind: "a4",
          onSuccess: function () {
            msg.className = "wh-msg wh-msg-ok";
            msg.textContent = "PDF прикреплён к задаче.";
          },
        });
      });
    }

    mergeBtn.addEventListener("click", function () {
      if (!fileQueue.length) return;
      msg.className = "wh-msg";
      msg.textContent = "Объединяем PDF...";
      mergeBtn.disabled = true;
      if (attachBtn) attachBtn.disabled = true;
      lastPdfResult = null;
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
        .then(function (result) {
          lastPdfResult = result;
          syncAttachBtn();
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = "PDF сформирован и скачан. Можно привязать к задаче.";
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
    syncAttachBtn();
  }

  function isExcelFile(file) {
    if (!file) return false;
    var name = String(file.name || "").toLowerCase();
    return (
      name.endsWith(".xlsx") ||
      name.endsWith(".xlsm") ||
      file.type === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    );
  }

  function renderExcelToPdf(tab, item) {
    var selectedFile = null;
    var lastPdfResult = null;
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML =
      '<div class="wh-tools-card">' +
      '<p class="wh-muted">Загрузите Excel (.xlsx) и выберите тип преобразования. PDF формируется с переносом таблицы на страницы.</p>' +
      '<div class="wh-excel-pdf-form">' +
      '<label><span>Тип</span><select id="whExcelPdfProfile"></select></label>' +
      '<label class="wh-import-file-label">Файл Excel<input type="file" id="whExcelPdfInput" accept=".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" /></label>' +
      '<p id="whExcelPdfFileName" class="wh-muted">Файл не выбран.</p>' +
      "</div>" +
      '<div class="wh-tools-actions">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whExcelPdfBtn" disabled>Сформировать PDF</button>' +
      '<button type="button" class="wh-btn" id="whExcelPdfAttachBtn" disabled>Привязать к задаче</button>' +
      '<span id="whExcelPdfMsg" class="wh-msg"></span>' +
      "</div>" +
      "</div>";

    var profileSelect = root.querySelector("#whExcelPdfProfile");
    var input = root.querySelector("#whExcelPdfInput");
    var fileNameEl = root.querySelector("#whExcelPdfFileName");
    var convertBtn = root.querySelector("#whExcelPdfBtn");
    var attachBtn = root.querySelector("#whExcelPdfAttachBtn");
    var msg = root.querySelector("#whExcelPdfMsg");

    function syncAttachBtn() {
      if (attachBtn) attachBtn.disabled = !lastPdfResult;
    }

    function syncConvertBtn() {
      if (convertBtn) convertBtn.disabled = !selectedFile;
    }

    function setFile(file) {
      selectedFile = file || null;
      lastPdfResult = null;
      syncAttachBtn();
      syncConvertBtn();
      if (!fileNameEl) return;
      if (!selectedFile) {
        fileNameEl.textContent = "Файл не выбран.";
        return;
      }
      fileNameEl.textContent = selectedFile.name + " (" + formatSize(selectedFile.size) + ")";
    }

    jsonRequest("/api/warehouse/tools/excel-to-pdf/profiles")
      .then(function (data) {
        var profiles = data.profiles || [];
        profileSelect.innerHTML = profiles
          .map(function (profile) {
            var label = profile.title || profile.id;
            if (profile.description) label += " — " + profile.description;
            return (
              '<option value="' +
              esc(profile.id) +
              '">' +
              esc(label) +
              "</option>"
            );
          })
          .join("");
      })
      .catch(function () {
        profileSelect.innerHTML =
          '<option value="free">Свободный</option>' +
          '<option value="vseinstrumenti">ВсеИнструменты</option>';
      });

    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) {
        setFile(null);
        return;
      }
      if (!isExcelFile(file)) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Можно загружать только Excel (.xlsx).";
        input.value = "";
        setFile(null);
        return;
      }
      msg.className = "wh-msg";
      msg.textContent = "";
      setFile(file);
    });

    if (attachBtn) {
      attachBtn.addEventListener("click", function () {
        if (!lastPdfResult) return;
        global.WhTaskAttach.openModal({
          pdfBlob: lastPdfResult.blob,
          defaultFilename: lastPdfResult.filename,
          defaultKind: "a4",
          onSuccess: function () {
            msg.className = "wh-msg wh-msg-ok";
            msg.textContent = "PDF прикреплён к задаче.";
          },
        });
      });
    }

    convertBtn.addEventListener("click", function () {
      if (!selectedFile) return;
      msg.className = "wh-msg";
      msg.textContent = "Формируем PDF...";
      convertBtn.disabled = true;
      if (attachBtn) attachBtn.disabled = true;
      lastPdfResult = null;
      var formData = new FormData();
      formData.append("file", selectedFile, selectedFile.name);
      formData.append("profile", profileSelect.value || "free");
      fetch("/api/warehouse/tools/excel-to-pdf", {
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
        .then(function (result) {
          lastPdfResult = result;
          syncAttachBtn();
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = "PDF сформирован и скачан. Можно привязать к задаче.";
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || String(err);
        })
        .finally(function () {
          syncConvertBtn();
        });
    });

    setFile(null);
  }

  var cargoState = { types: [], result: null };

  function jsonRequest(url, options) {
    var opts = options || {};
    opts.credentials = "include";
    return fetch(url, opts).then(function (response) {
      if (response.ok) return response.json();
      return response.text().then(function (text) {
        var detail = text || "HTTP " + response.status;
        try {
          var parsed = JSON.parse(text);
          if (parsed && parsed.detail) detail = parsed.detail;
        } catch (e) {}
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      });
    });
  }

  function cargoNumber(value, digits) {
    if (value === null || value === undefined || value === "") return "—";
    return Number(value).toLocaleString("ru-RU", {
      maximumFractionDigits: digits === undefined ? 3 : digits,
    });
  }

  function cargoOptions(selectedId) {
    return cargoState.types
      .map(function (type) {
        return (
          '<option value="' +
          type.id +
          '"' +
          (String(type.id) === String(selectedId || "") ? " selected" : "") +
          ">" +
          esc(type.name) +
          " (" +
          cargoNumber(type.volume_liters, 2) +
          " л)</option>"
        );
      })
      .join("");
  }

  function recalculateCargoResult(result) {
    var totalVolume = 0;
    var totalWeight = 0;
    var volumeComplete = true;
    var weightComplete = true;
    (result.rows || []).forEach(function (row) {
      if (!row.product_id) {
        volumeComplete = false;
        weightComplete = false;
        return;
      }
      var l = Number(row.length_mm);
      var w = Number(row.width_mm);
      var h = Number(row.height_mm);
      var weight = Number(row.unit_weight_kg);
      if (l > 0 && w > 0 && h > 0) {
        row.unit_volume_liters = (l * w * h) / 1000000;
        row.total_volume_liters = row.unit_volume_liters * row.quantity;
        totalVolume += row.total_volume_liters;
      } else {
        volumeComplete = false;
        row.unit_volume_liters = null;
        row.total_volume_liters = null;
      }
      if (weight > 0) {
        row.total_weight_kg = weight * row.quantity;
        totalWeight += row.total_weight_kg;
      } else {
        weightComplete = false;
        row.total_weight_kg = null;
      }
    });
    result.total_volume_liters = totalVolume;
    result.total_weight_kg = totalWeight;
    result.volume_complete = volumeComplete;
    result.weight_complete = weightComplete;
    var cargoVolume = Number(result.cargo_place_type.volume_liters);
    result.cargo_place_count =
      volumeComplete && cargoVolume > 0 ? Math.ceil(totalVolume / cargoVolume) : null;
  }

  function renderCargoResults(root) {
    var target = root.querySelector("#whCargoResults");
    var result = cargoState.result;
    if (!target || !result) {
      if (target) target.innerHTML = "";
      return;
    }
    var rows = (result.rows || [])
      .map(function (row) {
        var missing = row.missing_fields || [];
        var canEdit = !!row.product_id && missing.length > 0;
        function metricCell(field, value) {
          if (!canEdit) return esc(value || "—");
          return (
            '<input class="wh-cargo-metric" type="number" min="0.001" step="any" data-field="' +
            field +
            '" value="' +
            esc(value) +
            '" />'
          );
        }
        return (
          '<tr data-product-id="' +
          esc(row.product_id) +
          '">' +
          "<td>" +
          esc(row.sku) +
          "</td><td>" +
          esc(row.name || "—") +
          "</td><td>" +
          row.quantity +
          "</td><td>" +
          metricCell("length_mm", row.length_mm) +
          "</td><td>" +
          metricCell("width_mm", row.width_mm) +
          "</td><td>" +
          metricCell("height_mm", row.height_mm) +
          "</td><td>" +
          metricCell("weight_kg", row.unit_weight_kg) +
          "</td><td>" +
          cargoNumber(row.total_volume_liters) +
          "</td><td>" +
          cargoNumber(row.total_weight_kg) +
          "</td><td class=\"" +
          (row.error ? "wh-cargo-error" : "") +
          "\">" +
          esc(row.error || "Готово") +
          (canEdit
            ? '<br><button type="button" class="wh-btn wh-btn-sm wh-cargo-save-metrics">Сохранить в каталог</button>'
            : "") +
          "</td></tr>"
        );
      })
      .join("");
    var volumeText = cargoNumber(result.total_volume_liters);
    if (!result.volume_complete) volumeText += " (без строк с ошибками)";
    var weightText = cargoNumber(result.total_weight_kg);
    if (!result.weight_complete) weightText += " (без строк с ошибками)";
    target.innerHTML =
      '<div class="wh-cargo-summary">' +
      "<div><span>Единиц товара</span><strong>" +
      result.total_quantity +
      "</strong></div><div><span>Общий объём</span><strong>" +
      volumeText +
      " л</strong></div><div><span>Общий вес</span><strong>" +
      weightText +
      " кг</strong></div><div><span>Грузомест</span><strong>" +
      (result.cargo_place_count === null ? "Нужны все ОВХ" : result.cargo_place_count) +
      "</strong></div></div>" +
      '<div class="wh-table-wrap"><table class="wh-table wh-cargo-table"><thead><tr>' +
      "<th>Артикул</th><th>Товар</th><th>Кол-во</th><th>Длина, мм</th><th>Ширина, мм</th><th>Высота, мм</th><th>Вес, кг</th><th>Объём, л</th><th>Вес всего, кг</th><th>Статус</th>" +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table></div>";
    target.querySelectorAll(".wh-cargo-save-metrics").forEach(function (button) {
      button.addEventListener("click", function () {
        var tr = button.closest("tr");
        var productId = Number(tr.getAttribute("data-product-id"));
        var row = result.rows.find(function (candidate) {
          return Number(candidate.product_id) === productId;
        });
        var payload = {};
        tr.querySelectorAll(".wh-cargo-metric").forEach(function (input) {
          payload[input.getAttribute("data-field")] = input.value;
        });
        button.disabled = true;
        jsonRequest("/api/warehouse/tools/cargo-places/products/" + productId + "/metrics", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
          .then(function (data) {
            var saved = data.item;
            row.length_mm = saved.length_mm;
            row.width_mm = saved.width_mm;
            row.height_mm = saved.height_mm;
            row.unit_weight_kg = saved.weight_kg;
            row.missing_fields = [];
            row.error = "";
            recalculateCargoResult(result);
            renderCargoResults(root);
            var msg = root.querySelector("#whCargoMsg");
            msg.className = "wh-msg wh-msg-ok";
            msg.textContent = "ОВХ и вес товара сохранены в каталоге.";
          })
          .catch(function (err) {
            button.disabled = false;
            var msg = root.querySelector("#whCargoMsg");
            msg.className = "wh-msg wh-msg-error";
            msg.textContent = err.message || String(err);
          });
      });
    });
  }

  function openCargoTypeEditor(root) {
    var editor = root.querySelector("#whCargoTypeEditor");
    editor.hidden = false;
    editor.innerHTML =
      '<h3>Виды грузомест</h3><p class="wh-muted">Все размеры указываются в миллиметрах.</p>' +
      '<div id="whCargoTypeRows"></div><div class="wh-tools-actions">' +
      '<button type="button" class="wh-btn" id="whCargoTypeAdd">Добавить</button>' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whCargoTypeSave">Сохранить</button>' +
      '<button type="button" class="wh-btn" id="whCargoTypeCancel">Закрыть</button></div>';
    var rowsEl = editor.querySelector("#whCargoTypeRows");
    var draft = cargoState.types.map(function (item) {
      return Object.assign({}, item);
    });
    function draw() {
      rowsEl.innerHTML = draft
        .map(function (item, index) {
          return (
            '<div class="wh-cargo-type-row" data-index="' +
            index +
            '"><input data-field="name" placeholder="Название" value="' +
            esc(item.name) +
            '"><input data-field="length_mm" type="number" min="0.001" step="any" placeholder="Длина" value="' +
            esc(item.length_mm) +
            '"><input data-field="width_mm" type="number" min="0.001" step="any" placeholder="Ширина" value="' +
            esc(item.width_mm) +
            '"><input data-field="height_mm" type="number" min="0.001" step="any" placeholder="Высота" value="' +
            esc(item.height_mm) +
            '"><input data-field="comment" placeholder="Комментарий" value="' +
            esc(item.comment) +
            '"><button type="button" class="wh-btn wh-btn-sm" data-remove="' +
            index +
            '">Удалить</button></div>'
          );
        })
        .join("");
      rowsEl.querySelectorAll("[data-remove]").forEach(function (button) {
        button.addEventListener("click", function () {
          draft.splice(Number(button.getAttribute("data-remove")), 1);
          draw();
        });
      });
    }
    editor.querySelector("#whCargoTypeAdd").addEventListener("click", function () {
      draft.push({ name: "", length_mm: "", width_mm: "", height_mm: "", comment: "" });
      draw();
    });
    editor.querySelector("#whCargoTypeCancel").addEventListener("click", function () {
      editor.hidden = true;
    });
    editor.querySelector("#whCargoTypeSave").addEventListener("click", function () {
      rowsEl.querySelectorAll(".wh-cargo-type-row").forEach(function (rowEl) {
        var item = draft[Number(rowEl.getAttribute("data-index"))];
        rowEl.querySelectorAll("[data-field]").forEach(function (input) {
          item[input.getAttribute("data-field")] = input.value;
        });
      });
      jsonRequest("/api/warehouse/tools/cargo-places/types", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: draft }),
      })
        .then(function (data) {
          cargoState.types = data.items || [];
          root.querySelector("#whCargoType").innerHTML = cargoOptions(
            cargoState.types[0] && cargoState.types[0].id
          );
          editor.hidden = true;
          var msg = root.querySelector("#whCargoMsg");
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = "Справочник грузомест сохранён.";
        })
        .catch(function (err) {
          var msg = root.querySelector("#whCargoMsg");
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || String(err);
        });
    });
    draw();
  }

  function renderCargoPlaces(tab, item) {
    cargoState = { types: [], result: null };
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML =
      '<div class="wh-tools-card wh-cargo-card"><p class="wh-muted">Загрузите Excel с колонками «Артикул» и «Количество». ОВХ и вес берутся из каталога. Расчёт учитывает суммарный объём, но не физическую укладку.</p>' +
      '<div class="wh-cargo-form"><label>Вид грузоместа<select id="whCargoType"><option>Загрузка...</option></select></label>' +
      '<button type="button" class="wh-btn" id="whCargoConfigure">Настроить…</button>' +
      '<a class="wh-btn" href="/api/warehouse/tools/cargo-places/template">Скачать шаблон</a>' +
      '<label class="wh-import-file-label">Выбрать Excel<input type="file" id="whCargoFile" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"></label>' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whCargoCalculate">Рассчитать</button></div>' +
      '<div id="whCargoTypeEditor" class="wh-cargo-type-editor" hidden></div><span id="whCargoMsg" class="wh-msg"></span></div>' +
      '<div id="whCargoResults"></div>';
    var select = root.querySelector("#whCargoType");
    var calculateButton = root.querySelector("#whCargoCalculate");
    jsonRequest("/api/warehouse/tools/cargo-places/types")
      .then(function (data) {
        cargoState.types = data.items || [];
        select.innerHTML = cargoState.types.length
          ? cargoOptions(cargoState.types[0].id)
          : '<option value="">Сначала добавьте вид грузоместа</option>';
      })
      .catch(function (err) {
        root.querySelector("#whCargoMsg").textContent = err.message || String(err);
      });
    root.querySelector("#whCargoConfigure").addEventListener("click", function () {
      openCargoTypeEditor(root);
    });
    calculateButton.addEventListener("click", function () {
      var file = root.querySelector("#whCargoFile").files[0];
      var msg = root.querySelector("#whCargoMsg");
      if (!select.value) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите или создайте вид грузоместа.";
        return;
      }
      if (!file) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите Excel-файл.";
        return;
      }
      var form = new FormData();
      form.append("file", file, file.name);
      form.append("cargo_place_type_id", select.value);
      calculateButton.disabled = true;
      msg.className = "wh-msg";
      msg.textContent = "Выполняется расчёт...";
      jsonRequest("/api/warehouse/tools/cargo-places/calculate", {
        method: "POST",
        body: form,
      })
        .then(function (data) {
          cargoState.result = data;
          renderCargoResults(root);
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = data.volume_complete && data.weight_complete
            ? "Расчёт готов."
            : "Расчёт выполнен частично: заполните недостающие данные.";
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || String(err);
        })
        .finally(function () {
          calculateButton.disabled = false;
        });
    });
  }

  function parseHeaderJson(name, headers) {
    var raw = headers.get(name);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }

  function renderYandexLabelSort(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML =
      '<div class="wh-tools-card">' +
      '<p class="wh-muted">Вставьте ссылку на Google Таблицу с нужной вкладкой FBS Яндекс (в URL должен быть <code>#gid=...</code>) ' +
      "и загрузите PDF ярлыков. Страницы будут выстроены по номерам заказов и дробям грузомест из листа. " +
      "Лишние ярлыки исключаются; отсутствующие показываются предупреждением.</p>" +
      '<div class="wh-label-sort-form">' +
      '<label class="wh-label-sort-url"><span>Ссылка на Google Таблицу</span>' +
      '<input type="url" id="whLabelSortUrl" placeholder="https://docs.google.com/spreadsheets/d/.../edit#gid=..." /></label>' +
      '<label class="wh-import-file-label">PDF ярлыков<input type="file" id="whLabelSortFile" accept=".pdf,application/pdf" /></label>' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whLabelSortBtn">Отсортировать и скачать</button>' +
      "</div>" +
      '<span id="whLabelSortMsg" class="wh-msg"></span>' +
      '<div id="whLabelSortStats" class="wh-label-sort-stats" hidden></div>' +
      '<ul id="whLabelSortWarnings" class="wh-label-sort-warnings" hidden></ul>' +
      "</div>";

    var urlInput = root.querySelector("#whLabelSortUrl");
    var fileInput = root.querySelector("#whLabelSortFile");
    var btn = root.querySelector("#whLabelSortBtn");
    var msg = root.querySelector("#whLabelSortMsg");
    var statsEl = root.querySelector("#whLabelSortStats");
    var warnEl = root.querySelector("#whLabelSortWarnings");

    function clearResult() {
      statsEl.hidden = true;
      statsEl.innerHTML = "";
      warnEl.hidden = true;
      warnEl.innerHTML = "";
    }

    function renderStats(stats, warningsPayload) {
      var sheetKeys = stats && stats.sheet_keys != null ? stats.sheet_keys : "—";
      var matched = stats && stats.matched != null ? stats.matched : "—";
      var missing = stats && stats.missing != null ? stats.missing : "—";
      var extras = stats && stats.extras_dropped != null ? stats.extras_dropped : "—";
      var output = stats && stats.output_pages != null ? stats.output_pages : "—";
      var title = stats && stats.sheet_title ? String(stats.sheet_title) : "";
      statsEl.hidden = false;
      statsEl.innerHTML =
        (title ? '<div class="wh-muted">Лист: ' + esc(title) + "</div>" : "") +
        "<div>В листе: <strong>" +
        esc(sheetKeys) +
        "</strong> · совпало: <strong>" +
        esc(matched) +
        "</strong> · нет ярлыка: <strong>" +
        esc(missing) +
        "</strong> · лишних исключено: <strong>" +
        esc(extras) +
        "</strong> · в PDF: <strong>" +
        esc(output) +
        "</strong></div>";

      var warnings = (warningsPayload && warningsPayload.warnings) || [];
      var total =
        warningsPayload && warningsPayload.total != null
          ? warningsPayload.total
          : warnings.length;
      if (!warnings.length) {
        warnEl.hidden = true;
        warnEl.innerHTML = "";
        return;
      }
      warnEl.hidden = false;
      warnEl.innerHTML = warnings
        .map(function (w) {
          return "<li>" + esc(w) + "</li>";
        })
        .join("");
      if (total > warnings.length) {
        warnEl.innerHTML +=
          '<li class="wh-muted">… и ещё ' + esc(total - warnings.length) + "</li>";
      }
    }

    btn.addEventListener("click", function () {
      var url = String(urlInput.value || "").trim();
      var file = fileInput.files && fileInput.files[0];
      clearResult();
      if (!url) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Укажите ссылку на Google Таблицу.";
        return;
      }
      if (!file) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите PDF с ярлыками.";
        return;
      }
      var form = new FormData();
      form.append("spreadsheet_url", url);
      form.append("file", file, file.name);
      btn.disabled = true;
      msg.className = "wh-msg";
      msg.textContent = "Сортируем ярлыки...";
      fetch("/api/warehouse/tools/yandex-label-sort", {
        method: "POST",
        credentials: "include",
        body: form,
      })
        .then(function (r) {
          if (r.ok) {
            var stats = parseHeaderJson("X-Label-Sort-Stats", r.headers);
            var warnings = parseHeaderJson("X-Label-Sort-Warnings", r.headers);
            return downloadBlob(r).then(function () {
              return { stats: stats, warnings: warnings };
            });
          }
          return r.text().then(function (text) {
            var detail = text || "HTTP " + r.status;
            try {
              var parsed = JSON.parse(text);
              if (parsed && parsed.detail) {
                detail =
                  typeof parsed.detail === "string"
                    ? parsed.detail
                    : String(parsed.detail);
              }
            } catch (e) {}
            throw new Error(detail);
          });
        })
        .then(function (payload) {
          renderStats(payload.stats, payload.warnings);
          var missing = payload.stats && Number(payload.stats.missing || 0);
          var extras = payload.stats && Number(payload.stats.extras_dropped || 0);
          if (missing || extras || (payload.warnings && payload.warnings.total)) {
            msg.className = "wh-msg wh-msg-warn";
            msg.textContent = "PDF скачан с предупреждениями.";
          } else {
            msg.className = "wh-msg wh-msg-ok";
            msg.textContent = "PDF отсортирован и скачан.";
          }
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || String(err);
        })
        .finally(function () {
          btn.disabled = false;
        });
    });
  }

  function render(tab, item) {
    if (item.id === "pdf-merge") {
      renderPdfMerge(tab, item);
      return;
    }
    if (item.id === "excel-to-pdf") {
      renderExcelToPdf(tab, item);
      return;
    }
    if (item.id === "cargo-places") {
      renderCargoPlaces(tab, item);
      return;
    }
    if (item.id === "yandex-label-sort") {
      renderYandexLabelSort(tab, item);
      return;
    }
    preparePanel(tab, item);
    panelEl().hidden = false;
    shell().contentPlaceholderEl.hidden = false;
    shell().contentPlaceholderEl.textContent = "Раздел в разработке.";
  }

  global.WhTools = { render: render };
})(window);
