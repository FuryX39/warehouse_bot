(function (global) {
  var CONFIGURE_VALUE = "__configure__";
  var meta = {
    marketplaces: [
      { id: "vseinstrumenti", title: "ВсеИнструменты", enabled: true },
      { id: "ozon", title: "Ozon", enabled: false },
      { id: "wildberries", title: "Wildberries", enabled: false },
      { id: "yandex_market", title: "Яндекс Маркет", enabled: false },
    ],
    vseinstrumenti: {
      supplier: 'ООО "ШАЙН СИСТЕМС"',
      statuses: ["КЗ", "ПСО"],
      purchase_statuses: [],
      cargo_types: [
        { id: "pallets", title: "Паллеты" },
        { id: "boxes", title: "Короба" },
      ],
    },
  };

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

  function apiDetailText(detail) {
    if (detail == null || detail === "") return "";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map(function (item) {
          return item && item.msg ? item.msg : "";
        })
        .filter(Boolean)
        .join("; ");
    }
    return String(detail);
  }

  function purchaseStatuses() {
    if (meta.vseinstrumenti.purchase_statuses && meta.vseinstrumenti.purchase_statuses.length) {
      return meta.vseinstrumenti.purchase_statuses;
    }
    return (meta.vseinstrumenti.statuses || []).map(function (name, idx) {
      return { id: idx + 1, name: name, is_default: true };
    });
  }

  function buildStatusOptions(selectedName) {
    var html = '<option value="">—</option>';
    purchaseStatuses().forEach(function (item) {
      var sel = String(selectedName || "") === String(item.name) ? " selected" : "";
      html += '<option value="' + esc(item.name) + '"' + sel + ">" + esc(item.name) + "</option>";
    });
    html += '<option value="' + CONFIGURE_VALUE + '">Настроить…</option>';
    return html;
  }

  function bindConfigureSelect(selectEl, onConfigure) {
    if (!selectEl) return;
    selectEl.addEventListener("change", function () {
      if (selectEl.value === CONFIGURE_VALUE) {
        var prev = selectEl.getAttribute("data-prev") || "";
        selectEl.value = prev;
        onConfigure();
        return;
      }
      selectEl.setAttribute("data-prev", selectEl.value);
    });
  }

  function openModal(title, bodyHtml, onSave) {
    var backdrop = document.createElement("div");
    backdrop.className = "wh-modal-backdrop";
    backdrop.innerHTML =
      '<div class="wh-modal" role="dialog">' +
      '<div class="wh-modal-header"><h3>' +
      esc(title) +
      '</h3><button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body">' +
      bodyHtml +
      "</div>" +
      '<div class="wh-modal-footer">' +
      '<button type="button" class="wh-btn wh-btn-primary wh-modal-save">Сохранить</button>' +
      '<button type="button" class="wh-btn wh-modal-cancel">Отмена</button>' +
      "</div></div>";
    document.body.appendChild(backdrop);
    function close() {
      backdrop.remove();
    }
    backdrop.querySelector(".wh-modal-close").addEventListener("click", close);
    backdrop.querySelector(".wh-modal-cancel").addEventListener("click", close);
    backdrop.addEventListener("click", function (e) {
      if (e.target === backdrop) close();
    });
    backdrop.querySelector(".wh-modal-save").addEventListener("click", function () {
      onSave(backdrop, close);
    });
    return backdrop;
  }

  function modalPurchaseStatusesEditor(onDone) {
    var rows = purchaseStatuses().map(function (item) {
      return (
        '<div class="wh-modal-row wh-crm-dict-row" data-id="' +
        esc(item.id) +
        '">' +
        '<input type="text" class="wh-crm-dict-name" value="' +
        esc(item.name) +
        '" placeholder="Название" />' +
        (item.is_default
          ? '<span class="wh-crm-dict-tag">по умолчанию</span>'
          : '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>') +
        "</div>"
      );
    });
    openModal(
      "Статусы закупки",
      '<p class="wh-muted">КЗ и ПСО — базовые значения, их нельзя удалить.</p>' +
        '<div id="whRouteStatusRows">' +
        rows.join("") +
        "</div>" +
        '<button type="button" class="wh-btn wh-btn-sm" id="whRouteStatusAdd">+ Добавить статус</button>',
      function (backdrop, close) {
        var items = [];
        backdrop.querySelectorAll(".wh-crm-dict-row").forEach(function (row) {
          var name = row.querySelector(".wh-crm-dict-name").value.trim();
          if (!name) return;
          var item = { name: name };
          var id = row.getAttribute("data-id");
          if (id) item.id = parseInt(id, 10);
          var tag = row.querySelector(".wh-crm-dict-tag");
          if (tag) item.is_default = true;
          items.push(item);
        });
        fetchJson("/api/warehouse/marketplaces/route-sheets/purchase-statuses", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: items }),
        })
          .then(function (data) {
            meta.vseinstrumenti.purchase_statuses = data.purchase_statuses || [];
            meta.vseinstrumenti.statuses = meta.vseinstrumenti.purchase_statuses.map(function (s) {
              return s.name;
            });
            close();
            if (onDone) onDone();
          })
          .catch(function (err) {
            alert(err.message || "Ошибка сохранения");
          });
      }
    );
    var backdrop = document.querySelector(".wh-modal-backdrop:last-of-type");
    if (!backdrop) return;
    backdrop.querySelector("#whRouteStatusAdd").addEventListener("click", function () {
      var row = document.createElement("div");
      row.className = "wh-modal-row wh-crm-dict-row";
      row.innerHTML =
        '<input type="text" class="wh-crm-dict-name" placeholder="Название" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>';
      backdrop.querySelector("#whRouteStatusRows").appendChild(row);
    });
    backdrop.addEventListener("click", function (e) {
      if (e.target && e.target.classList.contains("wh-crm-dict-remove")) {
        var row = e.target.closest(".wh-crm-dict-row");
        if (row) row.remove();
      }
    });
  }

  function refreshStatusSelect(root, selectedName) {
    var selectEl = root.querySelector('select[name="purchase_status"]');
    if (!selectEl) return;
    var prev = selectedName != null ? selectedName : selectEl.value;
    selectEl.innerHTML = buildStatusOptions(prev);
    if (prev) {
      selectEl.value = prev;
      selectEl.setAttribute("data-prev", prev);
    }
  }

  function downloadBlob(response, fallbackName) {
    return global.WhTaskAttach.parsePdfResponse(response, fallbackName).then(function (result) {
      global.WhTaskAttach.downloadPdfResult(result);
      return result;
    });
  }

  function postPdf(url, payload) {
    return fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) {
      if (r.ok) return downloadBlob(r, "route_sheets.pdf");
      return r.text().then(function (text) {
        var msg = text || "HTTP " + r.status;
        try {
          var json = JSON.parse(text);
          if (json && json.detail != null) msg = apiDetailText(json.detail);
        } catch (e) {}
        throw new Error(msg);
      });
    });
  }

  function marketplaceTabs(activeId) {
    return (
      '<div class="wh-route-tabs">' +
      meta.marketplaces
        .map(function (mp) {
          return (
            '<button type="button" class="wh-btn wh-route-tab' +
            (mp.id === activeId ? " active" : "") +
            '" data-mp="' +
            esc(mp.id) +
            '">' +
            esc(mp.title) +
            "</button>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  function renderVseinstrumenti() {
    var cargoTypes =
      meta.vseinstrumenti.cargo_types && meta.vseinstrumenti.cargo_types.length
        ? meta.vseinstrumenti.cargo_types
        : [
            { id: "pallets", title: "Паллеты" },
            { id: "boxes", title: "Короба" },
          ];
    return (
      '<div class="wh-route-card">' +
      '<p class="wh-muted">PDF маршрутного листа для быстрой печати. Выберите паллеты или короба — для каждого грузоместа будет создана отдельная страница.</p>' +
      '<form id="whRouteVsiForm" class="wh-route-form">' +
      '<label><span>Поставщик</span><input name="supplier" value="' +
      esc(meta.vseinstrumenti.supplier) +
      '" /></label>' +
      '<label><span>№ закупки</span><input name="purchase_number" autocomplete="off" /></label>' +
      '<label><span>Статус закупки</span><select name="purchase_status">' +
      buildStatusOptions("") +
      "</select></label>" +
      '<label><span>Дата доставки</span><input name="delivery_date" type="date" /></label>' +
      '<label><span>Номер перемещения <small>(необязательно)</small></span><input name="transfer_number" autocomplete="off" /></label>' +
      '<label><span>Тип грузомест</span><select name="cargo_type">' +
      cargoTypes
        .map(function (cargoType) {
          return '<option value="' + esc(cargoType.id) + '">' + esc(cargoType.title) + "</option>";
        })
        .join("") +
      "</select></label>" +
      '<label><span id="whRouteCargoCountLabel">Кол-во паллет</span><input name="cargo_count" type="number" min="1" max="200" step="1" required /></label>' +
      '<label><span id="whRouteCargoSequenceLabel">Паллет из</span><input value="сформируется автоматически: 1/N, 2/N..." disabled /></label>' +
      '<div class="wh-route-actions">' +
      '<button type="submit" class="wh-btn wh-btn-primary">Сформировать PDF</button>' +
      '<button type="button" class="wh-btn" id="whRouteAttachBtn" disabled>Привязать к задаче</button>' +
      '<span id="whRouteMsg" class="wh-msg"></span>' +
      "</div>" +
      "</form>" +
      "</div>"
    );
  }

  function renderStub(mp) {
    return (
      '<div class="wh-route-card">' +
      '<p class="wh-placeholder">Маршрутные листы для «' +
      esc(mp.title) +
      "» пока не реализованы. Раздел подготовлен для будущей настройки.</p>" +
      "</div>"
    );
  }

  function bindTabs(root) {
    root.querySelectorAll(".wh-route-tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-mp") || "vseinstrumenti";
        drawMarketplace(root, id);
      });
    });
  }

  function drawMarketplace(root, activeId) {
    var active = meta.marketplaces.filter(function (m) {
      return m.id === activeId;
    })[0] || meta.marketplaces[0];
    root.innerHTML =
      marketplaceTabs(active.id) +
      '<div id="whRouteContent">' +
      (active.id === "vseinstrumenti" ? renderVseinstrumenti() : renderStub(active)) +
      "</div>";
    bindTabs(root);
    if (active.id === "vseinstrumenti") bindVseinstrumenti(root);
  }

  function bindVseinstrumenti(root) {
    var form = root.querySelector("#whRouteVsiForm");
    var msg = root.querySelector("#whRouteMsg");
    var attachBtn = root.querySelector("#whRouteAttachBtn");
    var lastPdfResult = null;
    if (!form) return;
    var statusSelect = form.querySelector('select[name="purchase_status"]');
    var cargoTypeSelect = form.querySelector('select[name="cargo_type"]');
    var cargoCountLabel = form.querySelector("#whRouteCargoCountLabel");
    var cargoSequenceLabel = form.querySelector("#whRouteCargoSequenceLabel");
    function syncCargoTypeLabels() {
      var boxes = cargoTypeSelect && cargoTypeSelect.value === "boxes";
      cargoCountLabel.textContent = boxes ? "Кол-во коробов" : "Кол-во паллет";
      cargoSequenceLabel.textContent = boxes ? "Короб из" : "Паллет из";
    }
    if (cargoTypeSelect) {
      cargoTypeSelect.addEventListener("change", syncCargoTypeLabels);
      syncCargoTypeLabels();
    }
    bindConfigureSelect(statusSelect, function () {
      modalPurchaseStatusesEditor(function () {
        refreshStatusSelect(root);
      });
    });
    if (attachBtn) {
      attachBtn.addEventListener("click", function () {
        if (!lastPdfResult) return;
        var fd = new FormData(form);
        var purchaseNumber = String(fd.get("purchase_number") || "").trim();
        var defaultName = lastPdfResult.filename;
        if (purchaseNumber) {
          defaultName = global.WhTaskAttach.ensurePdfFilename("Маршрутный лист " + purchaseNumber);
        }
        global.WhTaskAttach.openModal({
          pdfBlob: lastPdfResult.blob,
          defaultFilename: defaultName,
          defaultKind: "a4",
          onSuccess: function () {
            msg.className = "wh-msg wh-msg-ok";
            msg.textContent = "PDF прикреплён к задаче.";
          },
        });
      });
    }
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      msg.className = "wh-msg";
      msg.textContent = "Формируем PDF...";
      if (attachBtn) attachBtn.disabled = true;
      lastPdfResult = null;
      var fd = new FormData(form);
      var payload = {
        supplier: fd.get("supplier"),
        purchase_number: fd.get("purchase_number"),
        purchase_status: fd.get("purchase_status"),
        delivery_date: fd.get("delivery_date"),
        transfer_number: fd.get("transfer_number"),
        cargo_type: fd.get("cargo_type"),
        cargo_count: fd.get("cargo_count"),
      };
      postPdf("/api/warehouse/marketplaces/route-sheets/vseinstrumenti.pdf", payload)
        .then(function (result) {
          lastPdfResult = result;
          if (attachBtn) attachBtn.disabled = false;
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = "PDF сформирован и скачан. Можно привязать к задаче.";
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || String(err);
        });
    });
  }

  function loadMeta() {
    return fetchJson("/api/warehouse/marketplaces/route-sheets/meta")
      .then(function (data) {
        meta = data || meta;
        if (!meta.vseinstrumenti) meta.vseinstrumenti = {};
        if (!meta.vseinstrumenti.purchase_statuses) {
          meta.vseinstrumenti.purchase_statuses = (meta.vseinstrumenti.statuses || []).map(
            function (name, idx) {
              return { id: idx + 1, name: name, is_default: true };
            }
          );
        }
        return meta;
      })
      .catch(function () {
        return meta;
      });
  }

  function render(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-placeholder">Загрузка...</p>';
    loadMeta().then(function () {
      drawMarketplace(root, "vseinstrumenti");
    });
  }

  global.WhRouteSheets = { render: render };
})(window);
