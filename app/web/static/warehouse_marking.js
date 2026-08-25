(function (global) {
  var gtinQuery = "";
  var gtinTimer = null;
  var scanItems = [];
  var scanning = false;
  var scanRefocusTimer = null;

  function esc(s) {
    return String(s == null ? "" : s)
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

  function fetchJson(url, options) {
    return shell().fetchJson(url, options);
  }

  function downloadBlob(blob, filename) {
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
  }

  function backLink() {
    return '<button type="button" class="wh-btn" id="whMarkingBack">&larr; К разделам</button>';
  }

  function renderHome(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML =
      '<div class="wh-marking-home">' +
      '<p class="wh-muted">Сначала привяжите GTIN к товарам, затем сканируйте Data Matrix — из кода берётся GTIN и подставляется артикул.</p>' +
      '<div class="wh-marking-actions">' +
      '<button type="button" class="wh-marking-action" id="whMarkingOpenGtin">' +
      "<strong>Внести GTIN</strong>" +
      "<span>Вручную по товарам или загрузкой Excel</span></button>" +
      '<button type="button" class="wh-marking-action" id="whMarkingOpenScan">' +
      "<strong>Сканирование</strong>" +
      "<span>Сканер или ввод, Enter — следующий код, затем Excel</span></button>" +
      "</div></div>";
    root.querySelector("#whMarkingOpenGtin").addEventListener("click", function () {
      renderGtin(tab, item);
    });
    root.querySelector("#whMarkingOpenScan").addEventListener("click", function () {
      renderScan(tab, item);
    });
  }

  function gtinChips(product) {
    return (product.gtins || [])
      .map(function (gtin) {
        return (
          '<span class="wh-marking-chip">' +
          esc(gtin) +
          '<button type="button" class="wh-marking-chip-x" data-product-id="' +
          esc(product.id) +
          '" data-gtin="' +
          esc(gtin) +
          '" title="Удалить">&times;</button></span>'
        );
      })
      .join("");
  }

  function renderGtinTable(products) {
    if (!products.length) {
      return '<p class="wh-msg">Товары не найдены.</p>';
    }
    return (
      '<div class="wh-table-wrap"><table class="wh-table"><thead><tr>' +
      "<th>Артикул</th><th>Название</th><th>GTIN</th><th></th>" +
      "</tr></thead><tbody>" +
      products
        .map(function (p) {
          return (
            '<tr data-product-id="' +
            esc(p.id) +
            '"><td>' +
            esc(p.sku) +
            "</td><td>" +
            esc(p.name) +
            '</td><td><div class="wh-marking-chips">' +
            gtinChips(p) +
            "</div></td><td>" +
            '<div class="wh-marking-add-gtin">' +
            '<input type="text" class="wh-marking-gtin-input" placeholder="GTIN" inputmode="numeric" />' +
            '<button type="button" class="wh-btn wh-btn-sm wh-marking-gtin-add">Добавить</button>' +
            "</div></td></tr>"
          );
        })
        .join("") +
      "</tbody></table></div>"
    );
  }

  function loadGtinRows(root) {
    var wrap = root.querySelector("#whMarkingGtinList");
    var msg = root.querySelector("#whMarkingGtinMsg");
    wrap.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    fetchJson("/api/warehouse/marking/gtins?q=" + encodeURIComponent(gtinQuery))
      .then(function (data) {
        wrap.innerHTML = renderGtinTable(data.products || []);
        bindGtinTable(root);
      })
      .catch(function (err) {
        wrap.innerHTML = "";
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Не удалось загрузить товары";
      });
  }

  function bindGtinTable(root) {
    var msg = root.querySelector("#whMarkingGtinMsg");
    root.querySelectorAll(".wh-marking-gtin-add").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var row = btn.closest("tr");
        var input = row.querySelector(".wh-marking-gtin-input");
        var productId = parseInt(row.getAttribute("data-product-id"), 10);
        var gtin = input.value.trim();
        msg.className = "wh-msg";
        msg.textContent = "";
        if (!gtin) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = "Введите GTIN.";
          input.focus();
          return;
        }
        btn.disabled = true;
        fetchJson("/api/warehouse/marking/gtins", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ product_id: productId, gtin: gtin }),
        })
          .then(function (data) {
            if (data.action === "exists") {
              msg.className = "wh-msg";
              msg.textContent = "Этот GTIN у товара уже есть.";
            } else {
              msg.className = "wh-msg wh-msg-ok";
              msg.textContent = "GTIN сохранён.";
              input.value = "";
            }
            loadGtinRows(root);
          })
          .catch(function (err) {
            msg.className = "wh-msg wh-msg-error";
            msg.textContent = err.message || "Не удалось сохранить GTIN";
          })
          .then(function () {
            btn.disabled = false;
          });
      });
    });
    root.querySelectorAll(".wh-marking-gtin-input").forEach(function (input) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          var btn = input.parentNode.querySelector(".wh-marking-gtin-add");
          if (btn) btn.click();
        }
      });
    });
    root.querySelectorAll(".wh-marking-chip-x").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var productId = parseInt(btn.getAttribute("data-product-id"), 10);
        var gtin = btn.getAttribute("data-gtin") || "";
        fetchJson("/api/warehouse/marking/gtins/remove", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ product_id: productId, gtin: gtin }),
        })
          .then(function () {
            loadGtinRows(root);
          })
          .catch(function (err) {
            msg.className = "wh-msg wh-msg-error";
            msg.textContent = err.message || "Не удалось удалить GTIN";
          });
      });
    });
  }

  function importGtinExcel(root) {
    var fileInput = root.querySelector("#whMarkingGtinFile");
    var msg = root.querySelector("#whMarkingGtinMsg");
    if (!fileInput.files || !fileInput.files.length) {
      msg.className = "wh-msg wh-msg-error";
      msg.textContent = "Выберите файл Excel (.xlsx).";
      return;
    }
    var formData = new FormData();
    formData.append("file", fileInput.files[0]);
    msg.className = "wh-msg";
    msg.textContent = "Загрузка Excel…";
    fetch("/api/warehouse/marking/gtins/import", {
      method: "POST",
      credentials: "include",
      body: formData,
    })
      .then(function (r) {
        var type = r.headers.get("content-type") || "";
        if (type.indexOf("spreadsheetml") >= 0) {
          return r.blob().then(function (blob) {
            downloadBlob(blob, "marking_gtin_import_errors.xlsx");
            var created = r.headers.get("X-Import-Created") || "0";
            var skipped = r.headers.get("X-Import-Skipped") || "0";
            var failed = r.headers.get("X-Import-Failed") || "0";
            throw new Error(
              "Часть строк не загрузилась. Добавлено: " +
                created +
                ", уже было: " +
                skipped +
                ", ошибок: " +
                failed +
                ". Скачан файл с ошибками."
            );
          });
        }
        if (!r.ok) {
          return r.text().then(function (text) {
            var detail = text;
            try {
              var json = JSON.parse(text);
              if (json && json.detail) detail = json.detail;
            } catch (e) {}
            throw new Error(typeof detail === "string" ? detail : "Не удалось загрузить Excel");
          });
        }
        return r.json();
      })
      .then(function (data) {
        msg.className = "wh-msg wh-msg-ok";
        msg.textContent =
          "Загружено GTIN: " +
          (data.created || 0) +
          (data.skipped ? ", уже было: " + data.skipped : "") +
          ".";
        fileInput.value = "";
        loadGtinRows(root);
      })
      .catch(function (err) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Не удалось загрузить Excel";
        loadGtinRows(root);
      });
  }

  function renderGtin(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML =
      '<div class="wh-route-card">' +
      '<div class="wh-crm-toolbar">' +
      backLink() +
      '<input type="search" id="whMarkingGtinSearch" class="wh-crm-search" placeholder="Поиск по названию, артикулу, коду…" value="' +
      esc(gtinQuery) +
      '" />' +
      '<button type="button" class="wh-btn" id="whMarkingGtinTemplate">Скачать шаблон Excel</button>' +
      '<label class="wh-btn wh-marking-file-btn">Загрузить Excel<input type="file" id="whMarkingGtinFile" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" /></label>' +
      "</div>" +
      '<p class="wh-muted">GTIN из Data Matrix будет сопоставлен с этими значениями и даст артикул.</p>' +
      '<p class="wh-msg" id="whMarkingGtinMsg"></p>' +
      '<div id="whMarkingGtinList"></div></div>';
    root.querySelector("#whMarkingBack").addEventListener("click", function () {
      renderHome(tab, item);
    });
    root.querySelector("#whMarkingGtinSearch").addEventListener("input", function (e) {
      gtinQuery = e.target.value.trim();
      clearTimeout(gtinTimer);
      gtinTimer = setTimeout(function () {
        loadGtinRows(root);
      }, 250);
    });
    root.querySelector("#whMarkingGtinTemplate").addEventListener("click", function () {
      fetch("/api/warehouse/marking/gtins/template", { credentials: "include" })
        .then(function (r) {
          if (!r.ok) throw new Error("Не удалось скачать шаблон");
          return r.blob();
        })
        .then(function (blob) {
          downloadBlob(blob, "marking_gtin_template.xlsx");
        })
        .catch(function (err) {
          var msg = root.querySelector("#whMarkingGtinMsg");
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || "Ошибка шаблона";
        });
    });
    root.querySelector("#whMarkingGtinFile").addEventListener("change", function () {
      importGtinExcel(root);
    });
    loadGtinRows(root);
  }

  function scanKey(raw) {
    return String(raw || "")
      .trim()
      .replace(/\u001d/g, "<GS>");
  }

  function showScanCode(raw) {
    return scanKey(raw);
  }

  function renderScanList(root) {
    var list = root.querySelector("#whMarkingScanList");
    var count = root.querySelector("#whMarkingScanCount");
    if (count) count.textContent = String(scanItems.length);
    if (!scanItems.length) {
      list.innerHTML = '<p class="wh-muted">Пока пусто — отсканируйте код и нажмите Enter.</p>';
      return;
    }
    list.innerHTML =
      '<ul class="wh-marking-scan-list">' +
      scanItems
        .map(function (item, idx) {
          return (
            '<li class="wh-marking-scan-item">' +
            '<span class="wh-marking-scan-idx">' +
            (idx + 1) +
            ".</span>" +
            '<span class="wh-marking-scan-code">' +
            esc(showScanCode(item.raw)) +
            "</span>" +
            '<button type="button" class="wh-btn wh-btn-sm wh-marking-scan-x" data-idx="' +
            idx +
            '" title="Удалить">&times;</button></li>'
          );
        })
        .join("") +
      "</ul>";
    list.querySelectorAll(".wh-marking-scan-x").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var idx = parseInt(btn.getAttribute("data-idx"), 10);
        if (idx >= 0 && idx < scanItems.length) {
          scanItems.splice(idx, 1);
          renderScanList(root);
        }
      });
    });
  }

  function setScanning(root, on) {
    scanning = !!on;
    var input = root.querySelector("#whMarkingScanInput");
    var stopBtn = root.querySelector("#whMarkingScanStop");
    var resumeBtn = root.querySelector("#whMarkingScanResume");
    var status = root.querySelector("#whMarkingScanStatus");
    input.disabled = !scanning;
    stopBtn.hidden = !scanning;
    resumeBtn.hidden = scanning;
    status.textContent = scanning
      ? "Сканирование включено: ввод → Enter → следующий код."
      : "Сканирование остановлено.";
    if (scanning) {
      setTimeout(function () {
        input.focus();
      }, 0);
    } else {
      input.blur();
    }
  }

  function addScannedCode(root, raw) {
    var msg = root.querySelector("#whMarkingScanMsg");
    var value = String(raw || "").trim();
    msg.className = "wh-msg";
    msg.textContent = "";
    if (!value) return;
    var key = scanKey(value);
    for (var i = 0; i < scanItems.length; i++) {
      if (scanItems[i].key === key) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Этот штрихкод уже внесён.";
        return;
      }
    }
    scanItems.push({ raw: value, key: key });
    renderScanList(root);
  }

  function finishScan(root) {
    var msg = root.querySelector("#whMarkingScanMsg");
    var btn = root.querySelector("#whMarkingScanFinish");
    msg.className = "wh-msg";
    msg.textContent = "";
    if (!scanItems.length) {
      msg.className = "wh-msg wh-msg-error";
      msg.textContent = "Список пуст — сначала отсканируйте коды.";
      return;
    }
    btn.disabled = true;
    msg.textContent = "Формируем Excel…";
    fetch("/api/warehouse/marking/codes/export", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        codes: scanItems.map(function (item) {
          return item.raw;
        }),
      }),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.text().then(function (text) {
            var detail = text;
            try {
              var json = JSON.parse(text);
              if (json && json.detail) detail = json.detail;
            } catch (e) {}
            throw new Error(typeof detail === "string" ? detail : "Не удалось сформировать файл");
          });
        }
        return r.blob();
      })
      .then(function (blob) {
        downloadBlob(blob, "marking_codes.xlsx");
        msg.className = "wh-msg wh-msg-ok";
        msg.textContent = "Файл скачан: артикул, GTIN, Data Matrix.";
      })
      .catch(function (err) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Не удалось сформировать файл";
      })
      .then(function () {
        btn.disabled = false;
      });
  }

  function renderScan(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML =
      '<div class="wh-route-card">' +
      '<div class="wh-crm-toolbar">' +
      backLink() +
      '<span class="wh-muted" id="whMarkingScanStatus"></span>' +
      "</div>" +
      '<label class="wh-marking-scan-label">Data Matrix' +
      '<input type="text" id="whMarkingScanInput" class="wh-marking-scan-input" autocomplete="off" spellcheck="false" placeholder="Отсканируйте или введите код и нажмите Enter" />' +
      "</label>" +
      '<div class="wh-tools-actions">' +
      '<button type="button" class="wh-btn" id="whMarkingScanStop">Остановиться</button>' +
      '<button type="button" class="wh-btn" id="whMarkingScanResume" hidden>Продолжить</button>' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whMarkingScanFinish">Завершить сканирование</button>' +
      "</div>" +
      '<p class="wh-msg" id="whMarkingScanMsg"></p>' +
      '<h4 class="wh-crm-section-title">Отсканировано: <span id="whMarkingScanCount">0</span></h4>' +
      '<div id="whMarkingScanList"></div></div>';
    root.querySelector("#whMarkingBack").addEventListener("click", function () {
      scanning = false;
      renderHome(tab, item);
    });
    var input = root.querySelector("#whMarkingScanInput");
    input.addEventListener("keydown", function (e) {
      if (e.key !== "Enter") return;
      e.preventDefault();
      addScannedCode(root, input.value);
      input.value = "";
    });
    input.addEventListener("blur", function () {
      if (!scanning) return;
      clearTimeout(scanRefocusTimer);
      scanRefocusTimer = setTimeout(function () {
        if (scanning) input.focus();
      }, 50);
    });
    root.querySelector("#whMarkingScanStop").addEventListener("click", function () {
      setScanning(root, false);
    });
    root.querySelector("#whMarkingScanResume").addEventListener("click", function () {
      setScanning(root, true);
    });
    root.querySelector("#whMarkingScanFinish").addEventListener("click", function () {
      finishScan(root);
    });
    renderScanList(root);
    setScanning(root, true);
  }

  function render(tab, item) {
    renderHome(tab, item);
  }

  global.WhMarking = { render: render };
})(window);
