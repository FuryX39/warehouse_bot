/** Поставки FBO — менеджер: потребность, пакеты заявок, грузоместа, этикетки. */
(function (global) {
  var meta = { delivery_types: [], supply_kinds: [], batch_statuses: [], dropoff_presets: [] };
  var macrolocalClusters = [];
  var demandRows = [];
  var createItems = [];
  var selectedClusterIds = {};
  var previewSlots = [];
  var selectedSlot = null;
  var dropoffPick = null;
  var currentBatchId = null;
  var dragPayload = null;
  var ozonScope = "active";
  var ozonSelectedIds = {};
  var ozonOrdersLoaded = false;
  var ozonOrders = [];
  var ozonOrdersCount = 0;
  var listView = "overview";
  var opsPackingFields = [];
  var opsLogisticsFields = [];
  var opsPackingColumns = [];
  var opsLogisticsColumns = [];
  var CONFIGURE_VALUE = "__configure__";
  var OPS_SUMMARY_READONLY = {
    cluster: true,
    warehouse: true,
    ship_date: true,
    supply_id: true,
    movement_number: true,
    units_count: true,
    barcode_link: true,
    logistics_cluster: true,
  };

  function textOnBg(hex) {
    var h = String(hex || "").replace("#", "").trim();
    if (h.length === 3) {
      h = h
        .split("")
        .map(function (c) {
          return c + c;
        })
        .join("");
    }
    if (h.length !== 6) return "#1c2434";
    var r = parseInt(h.slice(0, 2), 16);
    var g = parseInt(h.slice(2, 4), 16);
    var b = parseInt(h.slice(4, 6), 16);
    if (isNaN(r) || isNaN(g) || isNaN(b)) return "#1c2434";
    var lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return lum > 0.58 ? "#1c2434" : "#ffffff";
  }

  function buildPackingStatusOptions(selectedId) {
    var html = '<option value="">—</option>';
    (meta.packing_statuses || []).forEach(function (it) {
      var sel = String(selectedId || "") === String(it.id) ? " selected" : "";
      var bg = it.color || "#9e9e9e";
      var fg = textOnBg(bg);
      html +=
        '<option value="' +
        esc(it.id) +
        '" data-color="' +
        esc(bg) +
        '" style="background-color:' +
        esc(bg) +
        ";color:" +
        esc(fg) +
        '"' +
        sel +
        ">" +
        esc(it.name) +
        "</option>";
    });
    html +=
      '<option value="' + CONFIGURE_VALUE + '" class="wh-crm-status-configure">Настроить…</option>';
    return html;
  }

  function syncStatusSelectAppearance(selectEl) {
    if (!selectEl) return;
    var opt = selectEl.options[selectEl.selectedIndex];
    var color = opt && opt.getAttribute("data-color");
    if (!color || !selectEl.value || selectEl.value === CONFIGURE_VALUE) {
      selectEl.style.backgroundColor = "";
      selectEl.style.color = "";
      selectEl.style.borderColor = "";
      return;
    }
    selectEl.style.backgroundColor = color;
    selectEl.style.color = textOnBg(color);
    selectEl.style.borderColor = color;
  }

  function initPackingStatusSelect(selectEl, onConfigure) {
    if (!selectEl) return;
    selectEl.classList.add("wh-crm-status-select");
    syncStatusSelectAppearance(selectEl);
    selectEl.addEventListener("change", function () {
      if (selectEl.value === CONFIGURE_VALUE) {
        var prev = selectEl.getAttribute("data-prev") || "";
        selectEl.value = prev;
        syncStatusSelectAppearance(selectEl);
        onConfigure();
        return;
      }
      selectEl.setAttribute("data-prev", selectEl.value);
      syncStatusSelectAppearance(selectEl);
    });
  }

  function modalPackingStatusesEditor(onDone) {
    var rows = (meta.packing_statuses || []).map(function (s) {
      return (
        '<div class="wh-modal-row wh-crm-dict-row" data-id="' +
        esc(s.id) +
        '">' +
        '<input type="text" class="wh-crm-dict-name" value="' +
        esc(s.name) +
        '" placeholder="Название" />' +
        '<input type="color" class="wh-crm-dict-color" value="' +
        esc(s.color || "#9e9e9e") +
        '" title="Цвет" />' +
        (s.is_default
          ? '<span class="wh-crm-dict-tag">по умолчанию</span>'
          : '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>') +
        "</div>"
      );
    });
    openModal(
      "Статусы поставок",
      '<p class="wh-muted">Название и цвет — как у статусов контрагентов в CRM.</p>' +
        '<div id="whFboPackingStatusRows">' +
        rows.join("") +
        "</div>" +
        '<button type="button" class="wh-btn wh-btn-sm" id="whFboPackingStatusAdd">+ Добавить статус</button>',
      function (backdrop, close) {
        var items = [];
        backdrop.querySelectorAll(".wh-crm-dict-row").forEach(function (row) {
          var name = row.querySelector(".wh-crm-dict-name").value.trim();
          if (!name) return;
          var item = {
            name: name,
            color: row.querySelector(".wh-crm-dict-color").value,
          };
          var id = row.getAttribute("data-id");
          if (id) item.id = parseInt(id, 10);
          items.push(item);
        });
        fetchJson("/api/warehouse/marketplaces/ozon-fbo/packing-statuses", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: items }),
        })
          .then(function (data) {
            meta.packing_statuses = data.packing_statuses || [];
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
    backdrop.querySelector("#whFboPackingStatusAdd").addEventListener("click", function () {
      var row = document.createElement("div");
      row.className = "wh-modal-row wh-crm-dict-row";
      row.innerHTML =
        '<input type="text" class="wh-crm-dict-name" placeholder="Название" />' +
        '<input type="color" class="wh-crm-dict-color" value="#9e9e9e" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>';
      backdrop.querySelector("#whFboPackingStatusRows").appendChild(row);
    });
    backdrop.addEventListener("click", function (e) {
      if (e.target && e.target.classList.contains("wh-crm-dict-remove")) {
        var row = e.target.closest(".wh-crm-dict-row");
        if (row) row.remove();
      }
    });
  }

  function supplyTypeById(typeId) {
    var found = null;
    (meta.supply_types || []).forEach(function (t) {
      if (String(t.id) === String(typeId || "")) found = t;
    });
    return found;
  }

  function supplyTypeComment(typeId) {
    var item = supplyTypeById(typeId);
    return item && item.comment ? String(item.comment) : "";
  }

  function buildSupplyTypeOptions(selectedId) {
    var html = '<option value="">—</option>';
    (meta.supply_types || []).forEach(function (it) {
      var sel = String(selectedId || "") === String(it.id) ? " selected" : "";
      var bg = it.color || "#9e9e9e";
      var fg = textOnBg(bg);
      html +=
        '<option value="' +
        esc(it.id) +
        '" data-color="' +
        esc(bg) +
        '" style="background-color:' +
        esc(bg) +
        ";color:" +
        esc(fg) +
        '"' +
        sel +
        ">" +
        esc(it.name) +
        "</option>";
    });
    html +=
      '<option value="' + CONFIGURE_VALUE + '" class="wh-crm-status-configure">Настроить…</option>';
    return html;
  }

  function renderSupplyTypeHelpMarkup(typeId) {
    var comment = supplyTypeComment(typeId);
    return (
      '<span class="wh-fbo-help-tip' +
      (comment ? "" : " wh-fbo-help-tip--empty") +
      '">' +
      '<span class="wh-fbo-help-tip__icon" aria-hidden="true">?</span>' +
      '<span class="wh-fbo-help-tip__bubble">' +
      esc(comment || "Комментарий не задан") +
      "</span></span>"
    );
  }

  function syncSupplyTypeHelp(wrapEl, typeId) {
    if (!wrapEl) return;
    var tip = wrapEl.querySelector(".wh-fbo-help-tip");
    if (!tip) return;
    var comment = supplyTypeComment(typeId);
    var bubble = tip.querySelector(".wh-fbo-help-tip__bubble");
    if (bubble) bubble.textContent = comment || "Комментарий не задан";
    tip.classList.toggle("wh-fbo-help-tip--empty", !comment);
  }

  function initSupplyTypeSelect(selectEl, onConfigure) {
    if (!selectEl) return;
    selectEl.classList.add("wh-crm-status-select");
    var wrap = selectEl.closest(".wh-fbo-supply-type-cell");
    syncStatusSelectAppearance(selectEl);
    syncSupplyTypeHelp(wrap, selectEl.value);
    selectEl.addEventListener("change", function () {
      if (selectEl.value === CONFIGURE_VALUE) {
        var prev = selectEl.getAttribute("data-prev") || "";
        selectEl.value = prev;
        syncStatusSelectAppearance(selectEl);
        syncSupplyTypeHelp(wrap, selectEl.value);
        onConfigure();
        return;
      }
      selectEl.setAttribute("data-prev", selectEl.value);
      syncStatusSelectAppearance(selectEl);
      syncSupplyTypeHelp(wrap, selectEl.value);
    });
  }

  function bindSupplyTypeCells(container, onConfigure) {
    if (!container) return;
    container.querySelectorAll('[data-ops-field="ops_supply_type_id"]').forEach(function (sel) {
      sel.setAttribute("data-prev", sel.value || "");
      initSupplyTypeSelect(sel, onConfigure || function () {});
    });
  }

  function modalSupplyTypesEditor(onDone) {
    var rows = (meta.supply_types || []).map(function (s) {
      return (
        '<div class="wh-modal-row wh-crm-dict-row wh-fbo-supply-type-row" data-id="' +
        esc(s.id) +
        '">' +
        '<input type="text" class="wh-crm-dict-name" value="' +
        esc(s.name) +
        '" placeholder="Название" />' +
        '<input type="color" class="wh-crm-dict-color" value="' +
        esc(s.color || "#9e9e9e") +
        '" title="Цвет" />' +
        '<textarea class="wh-fbo-supply-type-comment" rows="2" placeholder="Подсказка для упаковщиков">' +
        esc(s.comment || "") +
        "</textarea>" +
        (s.is_default
          ? '<span class="wh-crm-dict-tag">по умолчанию</span>'
          : '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>') +
        "</div>"
      );
    });
    openModal(
      "Типы поставки",
      '<p class="wh-muted">Название, цвет в таблице и комментарий — подсказка для упаковщиков при наведении на «?».</p>' +
        '<div id="whFboSupplyTypeRows">' +
        rows.join("") +
        "</div>" +
        '<button type="button" class="wh-btn wh-btn-sm" id="whFboSupplyTypeAdd">+ Добавить тип</button>',
      function (backdrop, close) {
        var items = [];
        backdrop.querySelectorAll(".wh-fbo-supply-type-row").forEach(function (row) {
          var name = row.querySelector(".wh-crm-dict-name").value.trim();
          if (!name) return;
          var item = {
            name: name,
            color: row.querySelector(".wh-crm-dict-color").value,
            comment: row.querySelector(".wh-fbo-supply-type-comment").value.trim(),
          };
          var id = row.getAttribute("data-id");
          if (id) item.id = parseInt(id, 10);
          items.push(item);
        });
        fetchJson("/api/warehouse/marketplaces/ozon-fbo/supply-types", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: items }),
        })
          .then(function (data) {
            meta.supply_types = data.supply_types || [];
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
    backdrop.querySelector("#whFboSupplyTypeAdd").addEventListener("click", function () {
      var row = document.createElement("div");
      row.className = "wh-modal-row wh-crm-dict-row wh-fbo-supply-type-row";
      row.innerHTML =
        '<input type="text" class="wh-crm-dict-name" placeholder="Название" />' +
        '<input type="color" class="wh-crm-dict-color" value="#9e9e9e" title="Цвет" />' +
        '<textarea class="wh-fbo-supply-type-comment" rows="2" placeholder="Подсказка для упаковщиков"></textarea>' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>';
      backdrop.querySelector("#whFboSupplyTypeRows").appendChild(row);
    });
    backdrop.addEventListener("click", function (e) {
      if (e.target && e.target.classList.contains("wh-crm-dict-remove")) {
        var row = e.target.closest(".wh-fbo-supply-type-row");
        if (row) row.remove();
      }
    });
  }

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

  function setMsg(el, text, isError) {
    if (!el) return;
    el.className = "wh-msg" + (isError ? " wh-msg-error" : " wh-msg-ok");
    el.textContent = text || "";
  }

  function formatTs(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1000).toLocaleString("ru-RU");
    } catch (e) {
      return String(ts);
    }
  }

  function deliveryLabel(id) {
    for (var i = 0; i < meta.delivery_types.length; i++) {
      if (meta.delivery_types[i].id === id) return meta.delivery_types[i].name;
    }
    return id || "—";
  }

  function batchStatusLabel(id) {
    for (var i = 0; i < meta.batch_statuses.length; i++) {
      if (meta.batch_statuses[i].id === id) return meta.batch_statuses[i].name;
    }
    return id || "—";
  }

  function loadMeta() {
    return fetchJson("/api/warehouse/marketplaces/ozon-fbo/meta").then(function (data) {
      meta = data;
      opsPackingFields = data.ops_packing_fields || [];
      opsLogisticsFields = data.ops_logistics_fields || [];
      opsPackingColumns = data.ops_packing_columns || [];
      opsLogisticsColumns = data.ops_logistics_columns || [];
      return meta;
    });
  }

  function loadMacrolocalClusters() {
    return fetchJson("/api/warehouse/marketplaces/ozon-fbo/ozon/macrolocal-clusters").then(function (data) {
      macrolocalClusters = data.clusters || [];
      return macrolocalClusters;
    });
  }

  function resetCreateState() {
    createItems = [];
    selectedClusterIds = {};
    previewSlots = [];
    selectedSlot = null;
    dropoffPick = null;
  }

  function renderOzonOrdersTable(orders) {
    if (!orders || !orders.length) {
      return '<p class="wh-muted">Заявок в Ozon с выбранным фильтром не найдено.</p>';
    }
    var rows = orders
      .map(function (o) {
        var oid = String(o.order_id);
        var checked = ozonSelectedIds[oid] ? " checked" : "";
        var localMark = o.in_local_system
          ? '<span class="wh-fbo-badge wh-fbo-badge--ok">в системе</span>'
          : '<span class="wh-fbo-badge">только Ozon</span>';
        var stateCls = o.state === "CANCELLED" ? " wh-fbo-state-cancelled" : "";
        return (
          '<tr class="wh-fbo-ozon-row' + stateCls + '" data-order-id="' + esc(oid) + '">' +
          '<td><input type="checkbox" class="wh-fbo-ozon-check" data-order-id="' + esc(oid) + '"' + checked + " /></td>" +
          "<td>" + esc(o.order_id) + "</td>" +
          "<td>" + esc(o.order_number || "—") + "</td>" +
          "<td>" + esc(o.state_label || o.state) + "</td>" +
          "<td>" + esc(o.delivery_type_label || "—") + "</td>" +
          "<td>" + esc(o.warehouse_name || "—") +
          (o.supply_count > 1 ? ' <span class="wh-muted">(' + esc(o.supply_count) + " поставок)</span>" : "") +
          "</td>" +
          "<td>" + esc(o.timeslot_label || "—") + "</td>" +
          "<td>" + esc(o.created_label || "—") + "</td>" +
          "<td>" + localMark + "</td></tr>"
        );
      })
      .join("");
    return (
      '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
      "<th></th><th>order_id</th><th>№ заявки</th><th>Статус</th><th>Доставка</th><th>Склад</th><th>Слот</th><th>Создана</th><th>В системе</th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table>"
    );
  }

  function renderOzonScopeOptions() {
    return (
      '<option value="active"' + (ozonScope === "active" ? " selected" : "") + ">Активные</option>" +
      '<option value="pre_ship"' + (ozonScope === "pre_ship" ? " selected" : "") + ">Подготовка к отгрузке</option>" +
      '<option value="archive"' + (ozonScope === "archive" ? " selected" : "") + ">Архив (завершённые и отменённые)</option>" +
      '<option value="all"' + (ozonScope === "all" ? " selected" : "") + ">Все</option>"
    );
  }

  function renderOzonSectionContent() {
    if (!ozonOrdersLoaded) {
      return (
        '<p class="wh-muted">Список заявок из Ozon не загружен — это ускоряет открытие вкладки.</p>' +
        '<button type="button" class="wh-btn wh-btn-primary" id="whFboLoadOzon">Загрузить из Ozon</button>'
      );
    }
    return (
      '<div class="wh-fbo-row">' +
      '<label>Фильтр</label><select id="whFboOzonScope" class="wh-fbo-input">' +
      renderOzonScopeOptions() +
      "</select>" +
      '<button type="button" class="wh-btn" id="whFboReloadOzon">Обновить из Ozon</button>' +
      '<button type="button" class="wh-btn" id="whFboImportOzon">Импортировать выбранные в систему</button>' +
      "</div>" +
      '<div id="whFboOzonTable">' + renderOzonOrdersTable(ozonOrders) + "</div>"
    );
  }

  function bindOzonListEvents(tab, item, root) {
    var loadBtn = root.querySelector("#whFboLoadOzon");
    if (loadBtn) {
      loadBtn.addEventListener("click", function () {
        fetchOzonOrders(tab, item, root);
      });
      return;
    }
    var reloadBtn = root.querySelector("#whFboReloadOzon");
    if (reloadBtn) {
      reloadBtn.addEventListener("click", function () {
        fetchOzonOrders(tab, item, root);
      });
    }
    var scopeSel = root.querySelector("#whFboOzonScope");
    if (scopeSel) {
      scopeSel.addEventListener("change", function (e) {
        ozonScope = e.target.value || "active";
        fetchOzonOrders(tab, item, root);
      });
    }
    var importBtn = root.querySelector("#whFboImportOzon");
    if (importBtn) {
      importBtn.addEventListener("click", function () {
        var ids = Object.keys(ozonSelectedIds);
        if (!ids.length) {
          setMsg(root.querySelector("#whFboListMsg"), "Отметьте заявки для импорта", true);
          return;
        }
        setMsg(root.querySelector("#whFboListMsg"), "Импорт…", false);
        fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/import-ozon", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ order_ids: ids, title: "Импорт из Ozon" }),
        })
          .then(function (data) {
            if (data.batch && data.batch.id) {
              renderBatchDetail(tab, item, data.batch.id);
            } else {
              renderBatchList(tab, item, { reloadOzon: ozonOrdersLoaded });
            }
          })
          .catch(function (err) {
            setMsg(root.querySelector("#whFboListMsg"), err.message, true);
          });
      });
    }
    root.querySelectorAll(".wh-fbo-ozon-check").forEach(function (cb) {
      cb.addEventListener("change", function () {
        var id = cb.getAttribute("data-order-id");
        if (cb.checked) ozonSelectedIds[id] = true;
        else delete ozonSelectedIds[id];
      });
    });
    root.querySelectorAll(".wh-fbo-ozon-row").forEach(function (tr) {
      tr.style.cursor = "pointer";
      tr.addEventListener("click", function (e) {
        if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "BUTTON")) return;
        renderOzonOrderDetail(tab, item, parseInt(tr.getAttribute("data-order-id"), 10));
      });
    });
  }

  function updateOzonSection(tab, item, root) {
    var section = root.querySelector("#whFboOzonSection");
    var titleCount = root.querySelector("#whFboOzonCount");
    if (titleCount) {
      titleCount.textContent = "(" + (ozonOrdersLoaded ? String(ozonOrdersCount) : "—") + ")";
    }
    if (section) {
      section.innerHTML = renderOzonSectionContent();
      bindOzonListEvents(tab, item, root);
    }
  }

  function fetchOzonOrders(tab, item, root) {
    var tableWrap = root.querySelector("#whFboOzonSection");
    if (tableWrap) {
      tableWrap.innerHTML = '<p class="wh-msg">Загрузка заявок из Ozon…</p>';
    }
    setMsg(root.querySelector("#whFboListMsg"), "Загрузка заявок из Ozon…", false);
    fetchJson("/api/warehouse/marketplaces/ozon-fbo/ozon/supply-orders?scope=" + encodeURIComponent(ozonScope))
      .then(function (ozonData) {
        ozonOrdersLoaded = true;
        ozonOrders = ozonData.orders || [];
        ozonOrdersCount = ozonData.count || ozonOrders.length;
        updateOzonSection(tab, item, root);
        setMsg(
          root.querySelector("#whFboListMsg"),
          "Загружено заявок из Ozon: " + ozonOrdersCount,
          false
        );
      })
      .catch(function (err) {
        ozonOrdersLoaded = false;
        ozonOrders = [];
        ozonOrdersCount = 0;
        updateOzonSection(tab, item, root);
        setMsg(root.querySelector("#whFboListMsg"), err.message, true);
      });
  }

  function batchLabelsCell(b) {
    return b.labels_url
      ? '<a href="' + esc(b.labels_url) + '" target="_blank" rel="noopener" class="wh-fbo-batch-labels-link">Этикетки PDF</a>'
      : '<span class="wh-muted">—</span>';
  }

  function opsInput(name, label, value, placeholder) {
    return (
      '<label class="wh-fbo-ops-field"><span class="wh-fbo-ops-label">' + esc(label) + "</span>" +
      '<input type="text" class="wh-fbo-input wh-fbo-ops-input" data-ops-field="' + esc(name) + '" value="' + esc(value || "") + '"' +
      (placeholder ? ' placeholder="' + esc(placeholder) + '"' : "") +
      " /></label>"
    );
  }

  function opsDateInput(name, label, value, placeholder) {
    return (
      '<label class="wh-fbo-ops-field"><span class="wh-fbo-ops-label">' + esc(label) + "</span>" +
      '<input type="date" class="wh-fbo-input wh-fbo-ops-input" data-ops-field="' + esc(name) + '" value="' + esc(value || "") + '"' +
      (placeholder ? ' placeholder="' + esc(placeholder) + '"' : "") +
      " /></label>"
    );
  }

  function opsSelect(name, label, optionsHtml) {
    return (
      '<label class="wh-fbo-ops-field"><span class="wh-fbo-ops-label">' + esc(label) + "</span>" +
      '<select class="wh-fbo-input wh-fbo-ops-input" data-ops-field="' + esc(name) + '">' +
      optionsHtml +
      "</select></label>"
    );
  }

  function buildCounterpartyOptions(selectedId) {
    var html = '<option value="">—</option>';
    (meta.counterparties || []).forEach(function (cp) {
      var sel = String(selectedId || "") === String(cp.id) ? " selected" : "";
      html += '<option value="' + esc(cp.id) + '"' + sel + ">" + esc(cp.name) + "</option>";
    });
    return html;
  }

  function unloadAddressLabel(item) {
    var name = String(item.name || "").trim();
    var addr = String(item.address || "").trim();
    if (name && addr) return name + " — " + addr;
    return name || addr || "—";
  }

  function buildUnloadAddressOptions(selectedId) {
    var html = '<option value="">—</option>';
    (meta.unload_addresses || []).forEach(function (a) {
      var sel = String(selectedId || "") === String(a.id) ? " selected" : "";
      html += '<option value="' + esc(a.id) + '"' + sel + ">" + esc(unloadAddressLabel(a)) + "</option>";
    });
    html += '<option value="' + CONFIGURE_VALUE + '">Настроить…</option>';
    return html;
  }

  function bindConfigureSelect(selectEl, onConfigure) {
    if (!selectEl) return;
    selectEl.setAttribute("data-prev", selectEl.value || "");
    selectEl.addEventListener("change", function () {
      if (selectEl.value === CONFIGURE_VALUE) {
        var prev = selectEl.getAttribute("data-prev") || "";
        selectEl.value = prev;
        onConfigure();
        return;
      }
      selectEl.setAttribute("data-prev", selectEl.value || "");
    });
  }

  function openModal(title, bodyHtml, onSave) {
    var backdrop = document.createElement("div");
    backdrop.className = "wh-modal-backdrop";
    backdrop.innerHTML =
      '<div class="wh-modal wh-modal-wide" role="dialog">' +
      '<div class="wh-modal-header"><h3>' + esc(title) + '</h3><button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body">' + bodyHtml + "</div>" +
      '<div class="wh-modal-footer">' +
      '<button type="button" class="wh-btn wh-btn-primary wh-modal-save">Сохранить</button>' +
      '<button type="button" class="wh-btn wh-modal-cancel">Отмена</button></div></div>';
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

  function modalUnloadAddressEditor(onDone) {
    var rows = (meta.unload_addresses || []).map(function (item) {
      return (
        '<div class="wh-modal-row wh-crm-dict-row" data-id="' + esc(item.id) + '">' +
        '<input type="text" class="wh-crm-dict-name" value="' + esc(item.name) + '" placeholder="Название" />' +
        '<input type="text" class="wh-crm-dict-address" value="' + esc(item.address) + '" placeholder="Адрес" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button></div>'
      );
    });
    openModal(
      "Адреса выгрузки",
      '<div id="whFboUnloadAddrRows">' + rows.join("") + "</div>" +
        '<button type="button" class="wh-btn wh-btn-sm" id="whFboUnloadAddrAdd">+ Добавить</button>',
      function (backdrop, close) {
        var items = [];
        backdrop.querySelectorAll(".wh-crm-dict-row").forEach(function (row) {
          var name = row.querySelector(".wh-crm-dict-name").value.trim();
          var address = row.querySelector(".wh-crm-dict-address").value.trim();
          if (!name) return;
          var item = { name: name, address: address };
          var id = row.getAttribute("data-id");
          if (id) item.id = parseInt(id, 10);
          items.push(item);
        });
        fetchJson("/api/warehouse/marketplaces/ozon-fbo/unload-addresses", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: items }),
        })
          .then(function (data) {
            meta.unload_addresses = data.unload_addresses || [];
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
    backdrop.querySelector("#whFboUnloadAddrAdd").addEventListener("click", function () {
      var box = backdrop.querySelector("#whFboUnloadAddrRows");
      var row = document.createElement("div");
      row.className = "wh-modal-row wh-crm-dict-row";
      row.innerHTML =
        '<input type="text" class="wh-crm-dict-name" placeholder="Название" />' +
        '<input type="text" class="wh-crm-dict-address" placeholder="Адрес" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>';
      box.appendChild(row);
    });
    backdrop.addEventListener("click", function (e) {
      if (e.target && e.target.classList.contains("wh-crm-dict-remove")) {
        var row = e.target.closest(".wh-crm-dict-row");
        if (row) row.remove();
      }
    });
  }

  function packerUserIdsFromRow(row) {
    var ids = row && row.packer_user_ids;
    if (!ids || !ids.length) return [];
    return ids
      .map(function (x) {
        return parseInt(x, 10);
      })
      .filter(Boolean);
  }

  function packerNamesLabel(ids) {
    ids = ids || [];
    if (!ids.length) return "Выбрать…";
    var names = [];
    (meta.assignees || []).forEach(function (a) {
      if (ids.indexOf(a.id) >= 0 || ids.indexOf(parseInt(a.id, 10)) >= 0) {
        names.push(a.display_name);
      }
    });
    return names.length ? names.join(", ") : "Выбрать…";
  }

  function renderPackerPicker(selectedIds, extraAttrs) {
    selectedIds = (selectedIds || [])
      .map(function (x) {
        return parseInt(x, 10);
      })
      .filter(Boolean);
    if (!(meta.assignees || []).length) {
      return '<span class="wh-muted">Нет сотрудников</span>';
    }
    var checks = (meta.assignees || [])
      .map(function (a) {
        var checked = selectedIds.indexOf(a.id) >= 0 ? " checked" : "";
        return (
          '<label class="wh-fbo-packer-option">' +
          '<input type="checkbox" class="wh-fbo-packer-cb" value="' +
          esc(a.id) +
          '"' +
          checked +
          " /> " +
          esc(a.display_name) +
          "</label>"
        );
      })
      .join("");
    return (
      '<details class="wh-fbo-packer-picker"' +
      (extraAttrs || "") +
      ">" +
      '<summary class="wh-fbo-packer-picker__summary">' +
      esc(packerNamesLabel(selectedIds)) +
      "</summary>" +
      '<div class="wh-fbo-packer-picker__panel">' +
      checks +
      "</div></details>"
    );
  }

  function syncPackerPickerSummary(picker) {
    if (!picker) return;
    var ids = [];
    picker.querySelectorAll(".wh-fbo-packer-cb:checked").forEach(function (cb) {
      var id = parseInt(cb.value, 10);
      if (id) ids.push(id);
    });
    var summary = picker.querySelector(".wh-fbo-packer-picker__summary");
    if (summary) summary.textContent = packerNamesLabel(ids);
  }

  function collectPackerIdsFromPicker(picker) {
    var ids = [];
    if (!picker) return ids;
    picker.querySelectorAll(".wh-fbo-packer-cb:checked").forEach(function (cb) {
      var id = parseInt(cb.value, 10);
      if (id) ids.push(id);
    });
    return ids;
  }

  function bindPackerPickers(container) {
    if (!container) return;
    container.querySelectorAll(".wh-fbo-packer-picker").forEach(function (picker) {
      if (picker.getAttribute("data-bound")) return;
      picker.setAttribute("data-bound", "1");
      picker.querySelectorAll(".wh-fbo-packer-cb").forEach(function (cb) {
        cb.addEventListener("change", function () {
          syncPackerPickerSummary(picker);
        });
      });
      var panel = picker.querySelector(".wh-fbo-packer-picker__panel");
      if (panel) {
        panel.addEventListener("click", function (e) {
          e.stopPropagation();
        });
      }
    });
  }

  function cargoesCountManual(row) {
    return !!(row && (row.ops_cargoes_count_manual || (row.ops_editable || {}).ops_cargoes_count_manual === "1"));
  }

  function cargoesSuffixFromRow(row) {
    var ops = (row && row.ops_editable) || {};
    if (ops.ops_cargoes_suffix) return ops.ops_cargoes_suffix;
    return "П";
  }

  function renderCargoesCountInput(countVal, suffix, countAttrs, suffixAttrs, manual) {
    var manualClass = manual ? " wh-fbo-ops-manual" : "";
    var suf = suffix === "К" ? "К" : "П";
    return (
      '<div class="wh-fbo-cargoes-count-cell' +
      manualClass +
      '">' +
      '<input type="number" min="0" step="1"' +
      countAttrs +
      ' value="' +
      esc(countVal) +
      '" />' +
      "<select" +
      suffixAttrs +
      ' class="wh-fbo-input wh-fbo-cargoes-suffix-select" title="П — паллеты, К — короба">' +
      '<option value="П"' +
      (suf === "П" ? " selected" : "") +
      ">П</option>" +
      '<option value="К"' +
      (suf === "К" ? " selected" : "") +
      ">К</option>" +
      "</select></div>"
    );
  }

  function renderOpsField(f, editable, values, ctx) {
    ctx = ctx || {};
    var ph = values[f.value_key] || "";
    var val = editable[f.name];
    if (f.type === "packers") {
      return (
        '<label class="wh-fbo-ops-field wh-fbo-ops-field--packers"><span class="wh-fbo-ops-label">' +
        esc(f.label) +
        "</span>" +
        renderPackerPicker(ctx.packerIds || [], "") +
        "</label>"
      );
    }
    if (f.type === "counterparty") {
      return opsSelect(f.name, f.label, buildCounterpartyOptions(val));
    }
    if (f.type === "unload_address") {
      return opsSelect(f.name, f.label, buildUnloadAddressOptions(val));
    }
    if (f.type === "packing_status") {
      return opsSelect(f.name, f.label, buildPackingStatusOptions(val));
    }
    if (f.type === "supply_type") {
      return (
        '<label class="wh-fbo-ops-field wh-fbo-ops-field--supply-type"><span class="wh-fbo-ops-label">' +
        esc(f.label) +
        "</span>" +
        '<div class="wh-fbo-supply-type-cell">' +
        '<select class="wh-fbo-input wh-fbo-ops-input" data-ops-field="' +
        esc(f.name) +
        '">' +
        buildSupplyTypeOptions(val) +
        "</select>" +
        renderSupplyTypeHelpMarkup(val) +
        "</div></label>"
      );
    }
    if (f.type === "cargoes_count") {
      var suffix = editable.ops_cargoes_suffix || "П";
      var countVal = editable.ops_cargoes_count != null && editable.ops_cargoes_count !== "" ? String(editable.ops_cargoes_count) : "";
      var manual = editable.ops_cargoes_count_manual === "1";
      return (
        '<label class="wh-fbo-ops-field wh-fbo-ops-field--cargoes-count' +
        (manual ? " wh-fbo-ops-manual" : "") +
        '"><span class="wh-fbo-ops-label">' +
        esc(f.label) +
        (manual ? ' <span class="wh-fbo-ops-manual-badge" title="Значение изменено вручную">ручн.</span>' : "") +
        "</span>" +
        renderCargoesCountInput(
          countVal,
          suffix,
          ' class="wh-fbo-input wh-fbo-ops-input" data-ops-field="ops_cargoes_count"',
          ' data-ops-field="ops_cargoes_suffix"',
          manual
        ) +
        "</label>"
      );
    }
    if (f.name === "ops_assembly_date") {
      return opsDateInput(f.name, f.label, val, ph && val ? "" : ph);
    }
    return opsInput(f.name, f.label, val, ph && val ? "" : ph);
  }

  function renderBatchOpsForm(batch) {
    var ops = batch.ops_sheet || {};
    var editable = ops.editable || {};
    var summaryValues = (ops.summary_row && ops.summary_row.values) || {};
    var supplyDetails = ops.supply_details || [];
    var packerIds = batch.ops_packer_user_ids || packerUserIdsFromRow(ops.summary_row || {});
    var fieldCtx = { packerIds: packerIds };
    var packingHtml = opsPackingFields.map(function (f) {
      return renderOpsField(f, editable, summaryValues, fieldCtx);
    }).join("");
    var packingNames = {};
    opsPackingFields.forEach(function (f) {
      packingNames[f.name] = true;
    });
    var logisticsHtml = opsLogisticsFields
      .filter(function (f) {
        return !packingNames[f.name];
      })
      .map(function (f) {
        return renderOpsField(f, editable, summaryValues, fieldCtx);
      })
      .join("");
    var shipDate = ops.ship_date || batch.ops_ship_date || summaryValues.ship_date || "—";
    var autoHtml = supplyDetails
      .map(function (r) {
        var v = r.values || {};
        return (
          "<li><b>" + esc(v.cluster || "—") + "</b> · " + esc(v.warehouse || "—") +
          " · ID " + esc(v.supply_id || "—") + " · упаковщик " + esc(v.packer || "—") +
          " · " + esc(v.units_count || "—") + " ед.</li>"
        );
      })
      .join("");
    return (
      '<details class="wh-fbo-ops-sheet wh-fbo-batch-ops" open>' +
      "<summary>Данные для таблиц (на весь пакет)</summary>" +
      '<p class="wh-muted wh-fbo-ops-auto">Дата отгрузки из таймслота: <b>' + esc(shipDate) +
      "</b>. Время отгрузки указывает логист. Количество грузомест — одно на весь пакет (П — паллеты, К — короба). По заявкам автоматически:</p>" +
      (autoHtml ? '<ul class="wh-fbo-ops-auto-list">' + autoHtml + "</ul>" : "") +
      '<div class="wh-fbo-ops-grid">' +
      '<div class="wh-fbo-ops-col"><h5 class="wh-fbo-ops-col-title">Поставки (упаковщики)</h5>' + packingHtml + "</div>" +
      '<div class="wh-fbo-ops-col"><h5 class="wh-fbo-ops-col-title">Отгрузка (логисты)</h5>' + logisticsHtml + "</div>" +
      "</div>" +
      '<button type="button" class="wh-btn wh-btn-sm" id="whFboSaveBatchOps">Сохранить для таблиц</button>' +
      "</details>"
    );
  }

  function collectOpsPayload(section) {
    var ops = {};
    section.querySelectorAll("[data-ops-field]").forEach(function (inp) {
      var key = inp.getAttribute("data-ops-field");
      var val = inp.value.trim();
      if (key && key.endsWith("_id")) {
        ops[key] = val ? parseInt(val, 10) : null;
      } else {
        ops[key] = val;
      }
    });
    var picker = section.querySelector(".wh-fbo-packer-picker");
    if (picker) ops.ops_packer_user_ids = collectPackerIdsFromPicker(picker);
    if (Object.prototype.hasOwnProperty.call(ops, "ops_cargoes_count") || Object.prototype.hasOwnProperty.call(ops, "ops_cargoes_suffix")) {
      ops.ops_cargoes_count_manual = 1;
    }
    return ops;
  }

  function opsSummaryRowDate(row, dateKey) {
    if (row && row.values && row.values[dateKey]) return row.values[dateKey];
    var cells = row.packing_row || row.logistics_row || [];
    for (var i = 0; i < cells.length; i++) {
      if (cells[i].key === dateKey) return cells[i].value || "";
    }
    return "";
  }

  function sortOpsSummaryRows(rows, dateKey) {
    return (rows || []).slice().sort(function (a, b) {
      var av = opsSummaryRowDate(a, dateKey);
      var bv = opsSummaryRowDate(b, dateKey);
      var aKey = av && av.length >= 10 ? "0" + av : "1";
      var bKey = bv && bv.length >= 10 ? "0" + bv : "1";
      if (aKey !== bKey) return aKey < bKey ? -1 : 1;
      if (av !== bv) return av < bv ? -1 : av > bv ? 1 : 0;
      return String(a.batch_id || "").localeCompare(String(b.batch_id || ""), "ru");
    });
  }

  function opsFieldMapByValueKey() {
    var map = {};
    opsPackingFields.concat(opsLogisticsFields).forEach(function (f) {
      map[f.value_key] = f;
    });
    return map;
  }

  function summaryColgroup(columns) {
    var cols =
      '<col class="wh-fbo-ops-col wh-fbo-ops-col--batch" />' +
      columns
        .map(function (c) {
          return '<col class="wh-fbo-ops-col wh-fbo-ops-col--' + esc(c.key) + '" />';
        })
        .join("");
    return "<colgroup>" + cols + "</colgroup>";
  }

  function summaryCellAttrs(cell) {
    var key = cell && cell.key ? cell.key : "";
    return key ? ' data-col-key="' + esc(key) + '"' : "";
  }

  function renderOpsSummaryReadonlyCell(cell) {
    var val = cell.value || "";
    var attrs = summaryCellAttrs(cell);
    if (cell.key === "barcode_link" && val) {
      return (
        '<td class="wh-fbo-ops-summary-readonly"' +
        attrs +
        '><a href="' +
        esc(val) +
        '" target="_blank" rel="noopener">ссылка</a></td>'
      );
    }
    return (
      '<td class="wh-fbo-ops-summary-readonly"' +
      attrs +
      (val ? ' title="' + esc(val) + '"' : "") +
      ">" +
      esc(val || "—") +
      "</td>"
    );
  }

  function renderOpsSummaryEditableCell(row, cell, field, rowKey) {
    var bid = row.batch_id;
    var ops = row.ops_editable || {};
    var fieldName = field.name;
    var val = ops[fieldName] != null && ops[fieldName] !== "" ? String(ops[fieldName]) : "";
    var colAttrs = summaryCellAttrs(cell);
    var manual = field.type === "cargoes_count" && rowKey === "packing_row" && cargoesCountManual(row);
    var tdClass = manual ? ' class="wh-fbo-ops-manual"' : "";
    var attrs =
      ' class="wh-fbo-input wh-fbo-ops-summary-cell" data-batch-id="' +
      esc(bid) +
      '" data-ops-field="' +
      esc(fieldName) +
      '"';
    if (field.type === "packing_status") {
      return "<td" + tdClass + colAttrs + "><select" + attrs + ">" + buildPackingStatusOptions(val) + "</select></td>";
    }
    if (field.type === "supply_type") {
      return (
        "<td" +
        tdClass +
        colAttrs +
        '><div class="wh-fbo-supply-type-cell"><select' +
        attrs +
        ">" +
        buildSupplyTypeOptions(val) +
        "</select>" +
        renderSupplyTypeHelpMarkup(val) +
        "</div></td>"
      );
    }
    if (field.type === "packers") {
      return (
        "<td" +
        tdClass +
        colAttrs +
        ">" +
        renderPackerPicker(packerUserIdsFromRow(row), ' data-batch-id="' + esc(bid) + '"') +
        "</td>"
      );
    }
    if (field.type === "counterparty") {
      return "<td" + colAttrs + "><select" + attrs + ">" + buildCounterpartyOptions(val) + "</select></td>";
    }
    if (field.type === "unload_address") {
      return "<td" + colAttrs + "><select" + attrs + ">" + buildUnloadAddressOptions(val) + "</select></td>";
    }
    if (field.type === "cargoes_count") {
      var suffix = ops.ops_cargoes_suffix || cargoesSuffixFromRow(row);
      return (
        "<td" +
        tdClass +
        colAttrs +
        ">" +
        renderCargoesCountInput(
          val,
          suffix,
          ' class="wh-fbo-input wh-fbo-ops-summary-cell" data-batch-id="' + esc(bid) + '" data-ops-field="ops_cargoes_count"',
          ' class="wh-fbo-input wh-fbo-ops-summary-cell wh-fbo-cargoes-suffix-select" data-batch-id="' + esc(bid) + '" data-ops-field="ops_cargoes_suffix"',
          manual
        ) +
        "</td>"
      );
    }
    if (field.type === "date") {
      return "<td" + colAttrs + '><input type="date"' + attrs + ' value="' + esc(val) + '" /></td>';
    }
    if (field.name === "ops_packing_comment" || field.name === "ops_logistics_comment") {
      return (
        "<td" +
        tdClass +
        colAttrs +
        '><textarea rows="2"' +
        attrs +
        ">" +
        esc(val) +
        "</textarea></td>"
      );
    }
    return "<td" + colAttrs + '><input type="text"' + attrs + ' value="' + esc(val) + '" /></td>';
  }

  function renderOpsSummaryTableBody(rowList, rowKey, fieldMap) {
    return rowList
      .map(function (r) {
        var cells = (r[rowKey] || [])
          .map(function (cell) {
            var field = fieldMap[cell.key];
            if (field && field.type === "cargoes_count" && rowKey !== "packing_row") {
              return renderOpsSummaryReadonlyCell(cell);
            }
            if (!OPS_SUMMARY_READONLY[cell.key] && field) {
              return renderOpsSummaryEditableCell(r, cell, field, rowKey);
            }
            return renderOpsSummaryReadonlyCell(cell);
          })
          .join("");
        return (
          '<tr data-batch-id="' +
          esc(r.batch_id) +
          '"><td class="wh-fbo-ops-summary-readonly wh-fbo-ops-summary-batch" data-col-key="batch" title="' +
          esc(r.batch_title || r.title || "") +
          '">' +
          esc(r.batch_title || r.title || ("Пакет #" + (r.batch_id || "—"))) +
          "</td>" +
          cells +
          "</tr>"
        );
      })
      .join("");
  }

  function renderOpsSummaryTables(summary) {
    summary = summary || {};
    var rows = summary.rows || [];
    if (!rows.length) {
      return '<p class="wh-muted">Нет данных. Заполните поля пакета и сохраните.</p>';
    }
    var packingRows = summary.packing_summary_rows || sortOpsSummaryRows(rows, "assembly_date");
    var logisticsRows = summary.logistics_summary_rows || sortOpsSummaryRows(rows, "ship_date");
    var fieldMap = opsFieldMapByValueKey();
    function table(title, columns, rowList, rowKey) {
      var head = columns
        .map(function (c) {
          return (
            '<th data-col-key="' +
            esc(c.key) +
            '" title="' +
            esc(c.label) +
            '">' +
            esc(c.label) +
            "</th>"
          );
        })
        .join("");
      var body = renderOpsSummaryTableBody(rowList, rowKey, fieldMap);
      return (
        '<div class="wh-fbo-ops-summary-block"><h5 class="wh-fbo-ops-col-title">' +
        esc(title) +
        "</h5>" +
        '<div class="wh-fbo-ops-table-wrap"><table class="wh-employees-table wh-crm-table wh-fbo-ops-summary-table wh-fbo-ops-summary-table--editable">' +
        summaryColgroup(columns) +
        "<thead><tr><th data-col-key=\"batch\">Пакет</th>" +
        head +
        "</tr></thead><tbody>" +
        body +
        "</tbody></table></div></div>"
      );
    }
    return (
      '<div class="wh-fbo-ops-summary-toolbar">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whFboOpsSummarySave">Сохранить изменения</button>' +
      '<span class="wh-muted" id="whFboOpsSummarySaveMsg"></span></div>' +
      '<p class="wh-muted wh-fbo-ops-summary-hint">Количество грузомест редактируется в таблице «Поставки» (число и П/К). В «Отгрузке» — только просмотр. Жёлтая подсветка — ручное значение, не перезапишется при обновлении из Ozon.</p>' +
      table("ПОСТАВКИ (упаковщики)", summary.packing_columns || opsPackingColumns, packingRows, "packing_row") +
      table("ОТГРУЗКА (логисты)", summary.logistics_columns || opsLogisticsColumns, logisticsRows, "logistics_row")
    );
  }

  function collectBatchOpsFromSummary(container, batchId) {
    var ops = {};
    container.querySelectorAll('[data-batch-id="' + batchId + '"][data-ops-field]').forEach(function (inp) {
      var key = inp.getAttribute("data-ops-field");
      if (!key) return;
      var val = inp.value.trim();
      if (key.endsWith("_id")) ops[key] = val ? parseInt(val, 10) : null;
      else ops[key] = val;
    });
    if (Object.prototype.hasOwnProperty.call(ops, "ops_cargoes_count") || Object.prototype.hasOwnProperty.call(ops, "ops_cargoes_suffix")) {
      ops.ops_cargoes_count_manual = 1;
    }
    var picker = container.querySelector('.wh-fbo-packer-picker[data-batch-id="' + batchId + '"]');
    if (picker) ops.ops_packer_user_ids = collectPackerIdsFromPicker(picker);
    return ops;
  }

  function saveOpsSummaryEdits(container, opts) {
    opts = opts || {};
    var msgEl = container.querySelector("#whFboOpsSummarySaveMsg");
    var batchIds = {};
    container.querySelectorAll("[data-batch-id][data-ops-field]").forEach(function (el) {
      batchIds[el.getAttribute("data-batch-id")] = true;
    });
    container.querySelectorAll(".wh-fbo-packer-picker[data-batch-id]").forEach(function (el) {
      batchIds[el.getAttribute("data-batch-id")] = true;
    });
    var ids = Object.keys(batchIds).filter(Boolean);
    if (!ids.length) return Promise.resolve();
    setMsg(msgEl, "Сохранение…", false);
    return Promise.all(
      ids.map(function (bid) {
        return fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + bid + "/ops", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ops: collectBatchOpsFromSummary(container, bid) }),
        });
      })
    )
      .then(function () {
        setMsg(msgEl, "Сохранено: " + ids.length + " пакет(ов).", false);
        if (opts.onSaved) return opts.onSaved();
      })
      .catch(function (err) {
        setMsg(msgEl, err.message, true);
        throw err;
      });
  }

  function bindOpsSummaryEditor(container, tab, item, opts) {
    if (!container) return;
    opts = opts || {};
    var saveBtn = container.querySelector("#whFboOpsSummarySave");
    if (saveBtn && !saveBtn.getAttribute("data-bound")) {
      saveBtn.setAttribute("data-bound", "1");
      saveBtn.addEventListener("click", function () {
        saveOpsSummaryEdits(container, opts);
      });
    }
    var reloadSummary = function () {
      if (opts.reload) return opts.reload();
      return Promise.resolve();
    };
    container.querySelectorAll('[data-ops-field="ops_unload_address_id"]').forEach(function (sel) {
      bindConfigureSelect(sel, function () {
        modalUnloadAddressEditor(function () {
          reloadSummary();
        });
      });
    });
    container.querySelectorAll('[data-ops-field="ops_packing_status_id"]').forEach(function (sel) {
      sel.setAttribute("data-prev", sel.value || "");
      initPackingStatusSelect(sel, function () {
        modalPackingStatusesEditor(function () {
          reloadSummary();
        });
      });
    });
    bindSupplyTypeCells(container, function () {
      modalSupplyTypesEditor(function () {
        reloadSummary();
      });
    });
    bindPackerPickers(container);
  }

  function opsSummaryFromBatch(batch) {
    var ops = (batch && batch.ops_sheet) || {};
    var rows = ops.summary_rows || (ops.summary_row ? [ops.summary_row] : []);
    return {
      packing_columns: opsPackingColumns,
      logistics_columns: opsLogisticsColumns,
      rows: rows,
      packing_summary_rows: sortOpsSummaryRows(rows, "assembly_date"),
      logistics_summary_rows: sortOpsSummaryRows(rows, "ship_date"),
    };
  }

  function bindOpsSummaryExport(tab, item, root, batchId) {
    var btn = root.querySelector("#whFboExportSheets");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var msg = root.querySelector("#whFboBatchMsg");
      setMsg(msg, "Подготовка данных для таблиц…", false);
      fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId + "/ops/export", {
        method: "POST",
        body: "{}",
      })
        .then(function (data) {
          var box = root.querySelector("#whFboOpsSummaryTables");
          if (box) {
            box.innerHTML = renderOpsSummaryTables(data);
            refreshBatchOpsSummaryBox(box, tab, item, batchId, root);
          }
          setMsg(msg, data.message || "Данные подготовлены.", false);
        })
        .catch(function (err) {
          setMsg(msg, err.message, true);
        });
    });
  }

  function refreshGlobalOpsSummaryBox(box, tab, item) {
    if (!box) return;
    bindOpsSummaryEditor(box, tab, item, {
      onSaved: function () {
        return fetchJson("/api/warehouse/marketplaces/ozon-fbo/ops-summary").then(function (data) {
          box.innerHTML = renderOpsSummaryTables(data);
          refreshGlobalOpsSummaryBox(box, tab, item);
        });
      },
      reload: function () {
        return fetchJson("/api/warehouse/marketplaces/ozon-fbo/ops-summary").then(function (data) {
          box.innerHTML = renderOpsSummaryTables(data);
          refreshGlobalOpsSummaryBox(box, tab, item);
        });
      },
    });
  }

  function refreshBatchOpsSummaryBox(box, tab, item, batchId, root) {
    if (!box) return;
    bindOpsSummaryEditor(box, tab, item, {
      onSaved: function () {
        return fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId).then(function (fresh) {
          if (fresh.batch) {
            root._batchData = fresh.batch;
            box.innerHTML = renderOpsSummaryTables(opsSummaryFromBatch(fresh.batch));
            refreshBatchOpsSummaryBox(box, tab, item, batchId, root);
          }
        });
      },
      reload: function () {
        return fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId).then(function (fresh) {
          if (fresh.batch) {
            root._batchData = fresh.batch;
            box.innerHTML = renderOpsSummaryTables(opsSummaryFromBatch(fresh.batch));
            refreshBatchOpsSummaryBox(box, tab, item, batchId, root);
          }
        });
      },
    });
  }

  function renderDefaultClientSettings() {
    return (
      '<section class="wh-crm-section wh-fbo-default-client-section">' +
      '<h4 class="wh-crm-section-title">Клиент по умолчанию</h4>' +
      '<p class="wh-muted">Подставляется во все новые пакеты FBO и в пакеты, где клиент ещё не выбран.</p>' +
      '<div class="wh-fbo-default-client-row">' +
      '<label class="wh-fbo-default-client-field"><span class="wh-muted">Клиент</span>' +
      '<select id="whFboDefaultClient" class="wh-fbo-input">' +
      buildCounterpartyOptions(meta.default_counterparty_id || "") +
      "</select></label>" +
      '<button type="button" class="wh-btn wh-btn-primary" id="whFboSaveDefaultClient">Сохранить</button>' +
      '<span class="wh-muted" id="whFboDefaultClientMsg"></span></div></section>'
    );
  }

  function bindDefaultClientSettings(tab, item, root) {
    var sel = root.querySelector("#whFboDefaultClient");
    var btn = root.querySelector("#whFboSaveDefaultClient");
    if (!sel || !btn || btn.getAttribute("data-bound")) return;
    btn.setAttribute("data-bound", "1");
    sel.value = meta.default_counterparty_id ? String(meta.default_counterparty_id) : "";
    btn.addEventListener("click", function () {
      var msg = root.querySelector("#whFboDefaultClientMsg");
      var raw = sel.value.trim();
      setMsg(msg, "Сохранение…", false);
      fetchJson("/api/warehouse/marketplaces/ozon-fbo/settings/default-counterparty", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          counterparty_id: raw ? parseInt(raw, 10) : null,
          apply_to_existing: true,
        }),
      })
        .then(function (data) {
          meta.default_counterparty_id = data.default_counterparty_id || null;
          var note = "Клиент по умолчанию сохранён.";
          if (data.updated_batches) {
            note += " Обновлено пакетов без клиента: " + data.updated_batches + ".";
          }
          setMsg(msg, note, false);
        })
        .catch(function (err) {
          setMsg(msg, err.message, true);
        });
    });
  }

  function bindGlobalOpsSummary(tab, item, root) {
    var btn = root.querySelector("#whFboLoadOpsSummary");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var box = root.querySelector("#whFboGlobalOpsSummary");
      var msg = root.querySelector("#whFboListMsg");
      if (box) box.innerHTML = '<p class="wh-msg">Загрузка…</p>';
      fetchJson("/api/warehouse/marketplaces/ozon-fbo/ops-summary")
        .then(function (data) {
          if (box) {
            box.innerHTML = renderOpsSummaryTables(data);
            refreshGlobalOpsSummaryBox(box, tab, item);
          }
          setMsg(
            msg,
            "Сводка обновлена (" + (data.batch_count || 0) + " пакетов, " + (data.supply_count || 0) + " заявок).",
            false
          );
        })
        .catch(function (err) {
          if (box) box.innerHTML = "";
          setMsg(msg, err.message, true);
        });
    });
  }

  function renderBatchList(tab, item, opts) {
    opts = opts || {};
    currentBatchId = null;
    listView = "overview";
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка локальных пакетов…</p>';
    Promise.all([loadMeta(), fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches")])
      .then(function (parts) {
        var batches = (parts[1].batches || []);
        var batchRows = batches
          .map(function (b) {
            return (
              '<tr data-id="' + esc(b.id) + '">' +
              "<td>#" + esc(b.id) + "</td>" +
              "<td>" + esc(b.title) + "</td>" +
              "<td>" + esc(b.delivery_type_label || deliveryLabel(b.delivery_type)) + "</td>" +
              "<td>" + esc(b.timeslot_label || "—") + "</td>" +
              "<td>" + esc(b.supply_count) + "</td>" +
              "<td>" + esc(b.status_label || batchStatusLabel(b.status)) + "</td>" +
              "<td>" + batchLabelsCell(b) + "</td>" +
              "<td>" + esc(formatTs(b.updated_at_ts)) + '</td>' +
              '<td><button type="button" class="wh-btn wh-btn-sm wh-btn-danger wh-fbo-batch-del" data-id="' + esc(b.id) + '">Удалить</button></td></tr>'
            );
          })
          .join("");
        root.innerHTML =
          '<div class="wh-fbo-layout">' +
          '<div class="wh-crm-toolbar">' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whFboNewBatch">+ Создать пакет заявок</button>' +
          '<button type="button" class="wh-btn" id="whFboRefreshBatches">Обновить</button></div>' +
          '<p class="wh-msg" id="whFboListMsg"></p>' +
          renderDefaultClientSettings() +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Пакеты в системе</h4>' +
          (batchRows
            ? '<table class="wh-employees-table wh-crm-table wh-fbo-batch-table"><thead><tr>' +
              "<th>№</th><th>Название</th><th>Доставка</th><th>Слот</th><th>Заявок</th><th>Статус</th><th>Этикетки</th><th>Обновлено</th><th></th>" +
              "</tr></thead><tbody>" + batchRows + "</tbody></table>"
            : '<p class="wh-msg">Пакетов в системе пока нет.</p>') +
          "</section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Сводка для таблиц</h4>' +
          '<p class="wh-muted">Все пакеты в одной таблице — редактируйте ячейки и сохраните без входа в каждый пакет.</p>' +
          '<button type="button" class="wh-btn" id="whFboLoadOpsSummary">Показать сводку</button>' +
          '<div id="whFboGlobalOpsSummary" class="wh-fbo-ops-summary-root"></div></section>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Заявки в Ozon <span class="wh-muted" id="whFboOzonCount">(' +
          esc(ozonOrdersLoaded ? ozonOrdersCount : "—") +
          ")</span></h4>" +
          '<p class="wh-muted">Актуальные данные из Ozon Seller API. Загружаются по запросу.</p>' +
          '<div id="whFboOzonSection">' + renderOzonSectionContent() + "</div></section></div>";
        root.querySelector("#whFboNewBatch").addEventListener("click", function () {
          resetCreateState();
          renderCreateWizard(tab, item);
        });
        root.querySelector("#whFboRefreshBatches").addEventListener("click", function () {
          renderBatchList(tab, item, { reloadOzon: ozonOrdersLoaded });
        });
        bindOzonListEvents(tab, item, root);
        bindDefaultClientSettings(tab, item, root);
        bindGlobalOpsSummary(tab, item, root);
        root.querySelectorAll(".wh-fbo-batch-table tbody tr[data-id]").forEach(function (tr) {
          tr.style.cursor = "pointer";
          tr.addEventListener("click", function (e) {
            if (e.target && e.target.closest(".wh-fbo-batch-del, .wh-fbo-batch-labels-link")) return;
            renderBatchDetail(tab, item, parseInt(tr.getAttribute("data-id"), 10));
          });
        });
        root.querySelectorAll(".wh-fbo-batch-del").forEach(function (btn) {
          btn.addEventListener("click", function (e) {
            e.stopPropagation();
            var id = btn.getAttribute("data-id");
            if (!confirm("Удалить пакет #" + id + " из системы? Заявки в Ozon не затрагиваются.")) return;
            fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + id, { method: "DELETE" })
              .then(function () {
                renderBatchList(tab, item, { reloadOzon: ozonOrdersLoaded });
              })
              .catch(function (err) {
                setMsg(root.querySelector("#whFboListMsg"), err.message, true);
              });
          });
        });
        if (opts.reloadOzon && ozonOrdersLoaded) {
          fetchOzonOrders(tab, item, root);
        }
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function renderOzonOrderDetail(tab, item, orderId) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка заявки Ozon…</p>';
    fetchJson("/api/warehouse/marketplaces/ozon-fbo/ozon/supply-orders/" + orderId)
      .then(function (data) {
        var o = data.order || {};
        var items = data.items || [];
        var itemRows = items
          .map(function (it) {
            return (
              "<tr><td>" + esc(it.offer_id || it.sku) + "</td><td>" + esc(it.name) + "</td><td>" + esc(it.quantity) + "</td></tr>"
            );
          })
          .join("");
        var lines = o.supply_lines || [];
        var linesHtml = "";
        if (lines.length > 1) {
          linesHtml =
            '<table class="wh-employees-table wh-crm-table"><thead><tr><th>supply_id</th><th>Склад</th><th>Кластер</th></tr></thead><tbody>' +
            lines
              .map(function (ln) {
                return (
                  "<tr><td>" + esc(ln.supply_id || "—") + "</td><td>" + esc(ln.warehouse_name || "—") + "</td><td>" +
                  esc(ln.macrolocal_cluster_id || "—") + "</td></tr>"
                );
              })
              .join("") +
            "</tbody></table>";
        }
        root.innerHTML =
          '<div class="wh-fbo-layout">' +
          '<div class="wh-crm-form-toolbar">' +
          '<button type="button" class="wh-btn" id="whFboOzonBack">&larr; К списку</button>' +
          (data.local_supply_id
            ? '<button type="button" class="wh-btn" id="whFboOpenLocal">Открыть в системе</button>'
            : '<button type="button" class="wh-btn wh-btn-primary" id="whFboImportOne">Импортировать в систему</button>') +
          "</div>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Заявка Ozon #' + esc(o.order_id) + "</h4>" +
          "<p><strong>Статус:</strong> " + esc(o.state_label) + " · <strong>Доставка:</strong> " + esc(o.delivery_type_label) + "</p>" +
          "<p><strong>Склад:</strong> " + esc(o.warehouse_name) + " · <strong>Слот:</strong> " + esc(o.timeslot_label) + "</p>" +
          "<p><strong>Создана:</strong> " + esc(o.created_label) + " · <strong>№:</strong> " + esc(o.order_number) +
          (o.supply_count > 1 ? " · <strong>Поставок:</strong> " + esc(o.supply_count) : "") + "</p>" +
          (linesHtml || "") +
          (itemRows
            ? '<table class="wh-employees-table wh-crm-table"><thead><tr><th>SKU</th><th>Товар</th><th>Кол-во</th></tr></thead><tbody>' +
              itemRows +
              "</tbody></table>"
            : '<p class="wh-muted">Состав недоступен</p>') +
          "</section></div>";
        root.querySelector("#whFboOzonBack").addEventListener("click", function () {
          renderBatchList(tab, item);
        });
        if (data.local_supply_id) {
          root.querySelector("#whFboOpenLocal").addEventListener("click", function () {
            fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + data.local_supply_id).then(function (sd) {
              var bid = sd.supply && sd.supply.batch_id;
              if (bid) renderBatchDetail(tab, item, bid);
              else setMsg(root.querySelector("#whFboListMsg"), "Заявка не в пакете", true);
            });
          });
        } else {
          root.querySelector("#whFboImportOne").addEventListener("click", function () {
            fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/import-ozon", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ order_ids: [orderId], title: "Ozon #" + (o.order_number || orderId) }),
            }).then(function (res) {
              if (res.batch && res.batch.id) renderBatchDetail(tab, item, res.batch.id);
            });
          });
        }
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function renderDemandTable() {
    if (!demandRows.length) {
      return '<p class="wh-muted">Введите артикул и нажмите «Загрузить потребность».</p>';
    }
    var rows = demandRows
      .map(function (r) {
        var macro = r.macrolocal_cluster_id;
        var checked = macro && selectedClusterIds[String(macro)] ? " checked" : "";
        var disabled = macro ? "" : ' disabled title="Нет ID кластера в Ozon"';
        return (
          "<tr>" +
          '<td><input type="checkbox" class="wh-fbo-cluster-check" data-macro="' +
          esc(macro || "") +
          '" data-name="' +
          esc(r.cluster_name) +
          '"' +
          checked +
          disabled +
          " /></td>" +
          "<td>" + esc(r.cluster_name) + "</td>" +
          "<td>" + esc((r.ads_per_day != null ? r.ads_per_day : r.ads_cluster || 0).toFixed(2)) + "</td>" +
          "<td>" + esc(r.demand_60_days != null ? r.demand_60_days : Math.round((r.ads_cluster || 0) * 60)) + "</td>" +
          "<td>" + esc(r.available_stock_count || 0) + "</td>" +
          "<td>" + esc((r.turnover_grades || []).join(", ")) + "</td></tr>"
        );
      })
      .join("");
    return (
      '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
      "<th></th><th>Кластер</th><th>Продажи в день</th><th>Потребность 60 дн.</th><th>Остаток FBO</th><th>Ликвидность</th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table>"
    );
  }

  function renderClusterPicker() {
    if (!macrolocalClusters.length) {
      return '<p class="wh-muted">Кластеры загружаются из Ozon API…</p>';
    }
    var chips = macrolocalClusters
      .map(function (c) {
        var id = String(c.macrolocal_cluster_id);
        var on = selectedClusterIds[id] ? " wh-fbo-chip--on" : "";
        return (
          '<button type="button" class="wh-fbo-chip' + on + '" data-macro="' + esc(id) + '" data-name="' + esc(c.name) + '">' +
          esc(c.name) +
          "</button>"
        );
      })
      .join("");
    return '<div class="wh-fbo-chips">' + chips + "</div>";
  }

  function renderCreateItems() {
    if (!createItems.length) return '<p class="wh-muted">Товары не добавлены.</p>';
    return (
      '<table class="wh-employees-table wh-crm-table"><thead><tr><th>SKU</th><th>Название</th><th>Кол-во</th><th></th></tr></thead><tbody>' +
      createItems
        .map(function (it, idx) {
          return (
            "<tr>" +
            "<td>" + esc(it.sku) + "</td>" +
            "<td>" + esc(it.name || "—") + "</td>" +
            '<td><input type="number" min="1" class="wh-fbo-item-qty" data-idx="' + idx + '" value="' + esc(it.quantity) + '" /></td>' +
            '<td><button type="button" class="wh-btn wh-btn-sm wh-fbo-item-remove" data-idx="' + idx + '">×</button></td></tr>'
          );
        })
        .join("") +
      "</tbody></table>"
    );
  }

  function renderSlotsSelect() {
    if (!previewSlots.length) return '<p class="wh-muted">Нажмите «Загрузить таймслоты» после выбора кластера и товаров.</p>';
    var opts = '<option value="">— выберите слот —</option>';
    previewSlots.forEach(function (s, idx) {
      var sel = selectedSlot && selectedSlot.from_in_timezone === s.from_in_timezone ? " selected" : "";
      opts += '<option value="' + idx + '"' + sel + ">" + esc(s.label || s.from_in_timezone) + "</option>";
    });
    return '<select id="whFboSlotSelect" class="wh-fbo-slot-select">' + opts + "</select>";
  }

  function renderCreateWizard(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    Promise.all([loadMeta(), loadMacrolocalClusters()])
      .then(function () {
        root.innerHTML =
          '<div class="wh-fbo-layout">' +
          '<div class="wh-crm-form-toolbar">' +
          '<button type="button" class="wh-btn" id="whFboCreateBack">&larr; К списку</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whFboSubmitBatch">Создать заявки в Ozon</button></div>' +
          '<p class="wh-msg" id="whFboCreateMsg"></p>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">1. Потребность по товару</h4>' +
          '<div class="wh-fbo-row"><input type="text" id="whFboDemandSku" placeholder="Артикул (offer_id)" class="wh-fbo-input" />' +
          '<button type="button" class="wh-btn" id="whFboLoadDemand">Загрузить потребность</button></div>' +
          '<div id="whFboDemandTable">' + renderDemandTable() + "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">2. Кластеры (из Ozon API)</h4>' +
          '<p class="wh-muted">Отметьте кластеры для поставки. Список обновляется из Ozon.</p>' +
          '<div id="whFboClusterPicker">' + renderClusterPicker() + "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">3. Товары и количества</h4>' +
          '<p class="wh-muted">Количество указывается <strong>на каждый выбранный кластер</strong>. ' +
          "Например, SS996 × 150 и 4 кластера → 4 отдельные заявки по 150 шт. (всего 600).</p>" +
          '<div class="wh-fbo-row"><input type="text" id="whFboAddSku" placeholder="Артикул" class="wh-fbo-input" />' +
          '<input type="number" min="1" id="whFboAddQty" value="1" class="wh-fbo-input wh-fbo-input--qty" />' +
          '<button type="button" class="wh-btn" id="whFboAddItem">Добавить</button></div>' +
          '<div id="whFboItemsTable">' + renderCreateItems() + "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">4. Способ доставки и точка отгрузки</h4>' +
          '<div class="wh-fbo-row"><label>Доставка</label><select id="whFboDeliveryType" class="wh-fbo-input">' +
          (meta.delivery_types || [])
            .map(function (d) {
              return '<option value="' + esc(d.id) + '">' + esc(d.name) + "</option>";
            })
            .join("") +
          "</select>" +
          '<label>Тип сборки</label><select id="whFboSupplyKind" class="wh-fbo-input">' +
          (meta.supply_kinds || [])
            .map(function (k) {
              return '<option value="' + esc(k.id) + '">' + esc(k.name) + "</option>";
            })
            .join("") +
          "</select></div>" +
          '<div class="wh-fbo-row wh-fbo-dropoff-row" id="whFboDropoffRow" hidden>' +
          '<p class="wh-muted">Точка отгрузки (кросс-док)</p>' +
          '<div class="wh-fbo-chips" id="whFboDropoffPresets"></div>' +
          '<span id="whFboDropoffPicked" class="wh-muted"></span>' +
          '<details class="wh-fbo-dropoff-search" id="whFboDropoffSearchBlock">' +
          '<summary class="wh-muted">Поиск другой точки</summary>' +
          '<div class="wh-fbo-row">' +
          '<input type="text" id="whFboDropoffSearch" placeholder="Мин. 4 символа" class="wh-fbo-input wh-fbo-input--wide" />' +
          '<button type="button" class="wh-btn" id="whFboSearchDropoff">Найти</button>' +
          "</div></details></div>" +
          '<div id="whFboDropoffList"></div></section>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">5. Таймслот</h4>' +
          '<div class="wh-fbo-row">' +
          '<label>С</label><input type="date" id="whFboSlotFrom" class="wh-fbo-input" />' +
          '<label>По</label><input type="date" id="whFboSlotTo" class="wh-fbo-input" />' +
          '<button type="button" class="wh-btn" id="whFboLoadSlots">Загрузить таймслоты</button></div>' +
          '<div id="whFboSlotSelectWrap">' + renderSlotsSelect() + "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">6. Название пакета</h4>' +
          '<input type="text" id="whFboBatchTitle" class="wh-fbo-input wh-fbo-input--wide" placeholder="Пакет FBO" /></section>' +
          "</div>";
        bindCreateEvents(tab, item, root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function dropoffPayload() {
    if (!dropoffPick) return {};
    return {
      dropoff_warehouse_id: dropoffPick.id,
      dropoff_warehouse_name: dropoffPick.name,
      dropoff_warehouse_type: dropoffPick.warehouse_type || "",
    };
  }

  function setDropoffPick(root, pick) {
    dropoffPick = pick;
    var pickedEl = root.querySelector("#whFboDropoffPicked");
    if (pickedEl) {
      pickedEl.textContent = pick ? "Выбрано: " + pick.name : "";
    }
    var presets = root.querySelector("#whFboDropoffPresets");
    if (presets) {
      presets.querySelectorAll(".wh-fbo-chip").forEach(function (btn) {
        var on = pick && btn.getAttribute("data-id") === pick.id;
        btn.classList.toggle("wh-fbo-chip--on", !!on);
      });
    }
  }

  function renderDropoffPresets() {
    var presets = meta.dropoff_presets || [];
    if (!presets.length) {
      return '<p class="wh-muted">Пресеты не заданы — используйте поиск</p>';
    }
    return presets
      .map(function (p) {
        return (
          '<button type="button" class="wh-fbo-chip wh-fbo-dropoff-preset" data-id="' +
          esc(p.warehouse_id) +
          '" data-name="' +
          esc(p.name) +
          '" data-warehouse-type="' +
          esc(p.warehouse_type || "") +
          '">' +
          esc(p.name) +
          "</button>"
        );
      })
      .join("");
  }

  function bindDropoffPresets(root) {
    var presets = root.querySelector("#whFboDropoffPresets");
    if (!presets) return;
    presets.innerHTML = renderDropoffPresets();
    presets.querySelectorAll(".wh-fbo-dropoff-preset").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setDropoffPick(root, {
          id: btn.getAttribute("data-id"),
          name: btn.getAttribute("data-name"),
          warehouse_type: btn.getAttribute("data-warehouse-type") || "",
        });
      });
    });
  }

  function bindCreateEvents(tab, item, root) {
    var msg = root.querySelector("#whFboCreateMsg");
    root.querySelector("#whFboCreateBack").addEventListener("click", function () {
      renderBatchList(tab, item);
    });
    root.querySelector("#whFboDeliveryType").addEventListener("change", function () {
      var isCross = root.querySelector("#whFboDeliveryType").value === "crossdock";
      root.querySelector("#whFboDropoffRow").hidden = !isCross;
      if (!isCross) {
        setDropoffPick(root, null);
      }
    });
    bindDropoffPresets(root);
    root.querySelector("#whFboLoadDemand").addEventListener("click", function () {
      var sku = root.querySelector("#whFboDemandSku").value.trim();
      if (!sku) return setMsg(msg, "Введите артикул", true);
      setMsg(msg, "Загрузка потребности…", false);
      fetchJson("/api/warehouse/marketplaces/ozon-fbo/ozon/demand?offer_id=" + encodeURIComponent(sku))
        .then(function (data) {
          demandRows = data.clusters || [];
          macrolocalClusters = data.macrolocal_clusters || macrolocalClusters;
          if (data.product && !createItems.some(function (x) { return x.sku === data.product.offer_id; })) {
            createItems.push({
              sku: data.product.offer_id,
              name: data.product.name,
              quantity: 1,
            });
          }
          root.querySelector("#whFboDemandTable").innerHTML = renderDemandTable();
          root.querySelector("#whFboClusterPicker").innerHTML = renderClusterPicker();
          root.querySelector("#whFboItemsTable").innerHTML = renderCreateItems();
          bindClusterChecks(root);
          bindClusterChips(root);
          bindItemEditors(root);
          setMsg(msg, "Потребность загружена.", false);
        })
        .catch(function (err) {
          setMsg(msg, err.message, true);
        });
    });
    bindClusterChecks(root);
    bindClusterChips(root);
    bindItemEditors(root);
    root.querySelector("#whFboAddItem").addEventListener("click", function () {
      var sku = root.querySelector("#whFboAddSku").value.trim();
      var qty = parseInt(root.querySelector("#whFboAddQty").value, 10) || 1;
      if (!sku) return;
      createItems.push({ sku: sku, name: sku, quantity: qty });
      root.querySelector("#whFboItemsTable").innerHTML = renderCreateItems();
      bindItemEditors(root);
    });
    root.querySelector("#whFboSearchDropoff").addEventListener("click", function () {
      var q = root.querySelector("#whFboDropoffSearch").value.trim();
      if (q.length < 4) return setMsg(msg, "Минимум 4 символа для поиска", true);
      fetchJson("/api/warehouse/marketplaces/ozon-fbo/ozon/dropoff-warehouses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ search: q }),
      }).then(function (resp) {
        var list = root.querySelector("#whFboDropoffList");
        var rows = (resp.items || []).map(function (r) {
          return {
            id: String(r.warehouse_id),
            name: String(r.name),
            warehouse_type: String(r.draft_warehouse_type || r.warehouse_type || ""),
          };
        });
        list.innerHTML = rows.slice(0, 30).map(function (r) {
          return (
            '<button type="button" class="wh-fbo-dropoff-pick wh-btn wh-btn-sm" data-id="' +
            esc(r.id) +
            '" data-name="' +
            esc(r.name) +
            '" data-warehouse-type="' +
            esc(r.warehouse_type) +
            '">' +
            esc(r.name) +
            "</button> "
          );
        }).join("") || '<p class="wh-muted">Ничего не найдено</p>';
        list.querySelectorAll(".wh-fbo-dropoff-pick").forEach(function (btn) {
          btn.addEventListener("click", function () {
            setDropoffPick(root, {
              id: btn.getAttribute("data-id"),
              name: btn.getAttribute("data-name"),
              warehouse_type: btn.getAttribute("data-warehouse-type") || "",
            });
          });
        });
      }).catch(function (err) { setMsg(msg, err.message, true); });
    });
    root.querySelector("#whFboLoadSlots").addEventListener("click", function () {
      var clusters = selectedClustersArray();
      if (!clusters.length) return setMsg(msg, "Выберите кластер", true);
      if (!createItems.length) return setMsg(msg, "Добавьте товары", true);
      var deliveryType = root.querySelector("#whFboDeliveryType").value;
      if (deliveryType === "crossdock" && !dropoffPick) {
        return setMsg(msg, "Выберите точку отгрузки", true);
      }
      var body = Object.assign(
        {
          delivery_type: root.querySelector("#whFboDeliveryType").value,
          clusters: clusters,
          items: createItems,
          date_from: root.querySelector("#whFboSlotFrom").value,
          date_to: root.querySelector("#whFboSlotTo").value,
        },
        dropoffPayload()
      );
      setMsg(msg, "Загрузка таймслотов (создаётся пробный черновик)…", false);
      fetchJson("/api/warehouse/marketplaces/ozon-fbo/ozon/preview-timeslots", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (data) {
        previewSlots = data.slots || [];
        root.querySelector("#whFboSlotSelectWrap").innerHTML = renderSlotsSelect();
        bindSlotSelect(root);
        setMsg(msg, "Доступно слотов: " + previewSlots.length, false);
      }).catch(function (err) { setMsg(msg, err.message, true); });
    });
    bindSlotSelect(root);
    root.querySelector("#whFboSubmitBatch").addEventListener("click", function () {
      var clusters = selectedClustersArray();
      if (!clusters.length) return setMsg(msg, "Выберите кластеры", true);
      if (!createItems.length) return setMsg(msg, "Добавьте товары", true);
      if (!selectedSlot) return setMsg(msg, "Выберите таймслот", true);
      var delivery = root.querySelector("#whFboDeliveryType").value;
      if (delivery === "crossdock" && !dropoffPick) return setMsg(msg, "Выберите точку отгрузки", true);
      var body = Object.assign(
        {
          title: root.querySelector("#whFboBatchTitle").value.trim() || "Пакет FBO",
          delivery_type: delivery,
          supply_kind: root.querySelector("#whFboSupplyKind").value,
          timeslot: selectedSlot,
          clusters: clusters,
          items: createItems,
        },
        dropoffPayload()
      );
      setMsg(msg, "Создание заявок в Ozon… Задача запущена в фоне.", false);
      root.querySelector("#whFboSubmitBatch").disabled = true;
      fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (data) {
        if (!data.job_id) throw new Error("Сервер не вернул job_id");
        return pollOzonFboJob(data.job_id, function (job) {
          setMsg(msg, "Создание заявок в Ozon… (" + (job.status || "running") + ")", false);
        });
      }).then(function (result) {
        setMsg(msg, "Создано заявок: " + (result.created || 0) + ", ошибок: " + (result.errors || 0), !result.created);
        if (result.batch && result.batch.id) {
          renderBatchDetail(tab, item, result.batch.id);
        }
      }).catch(function (err) {
        setMsg(msg, err.message, true);
      }).finally(function () {
        root.querySelector("#whFboSubmitBatch").disabled = false;
      });
    });
  }

  function bindSlotSelect(root) {
    var sel = root.querySelector("#whFboSlotSelect");
    if (!sel) return;
    sel.addEventListener("change", function () {
      var idx = parseInt(sel.value, 10);
      selectedSlot = previewSlots[idx] || null;
    });
  }

  function bindClusterChecks(root) {
    root.querySelectorAll(".wh-fbo-cluster-check").forEach(function (cb) {
      cb.addEventListener("change", function () {
        var id = cb.getAttribute("data-macro");
        if (!id) return;
        if (cb.checked) selectedClusterIds[id] = cb.getAttribute("data-name") || id;
        else delete selectedClusterIds[id];
        root.querySelector("#whFboClusterPicker").innerHTML = renderClusterPicker();
        bindClusterChips(root);
      });
    });
  }

  function bindClusterChips(root) {
    root.querySelectorAll(".wh-fbo-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var id = chip.getAttribute("data-macro");
        var name = chip.getAttribute("data-name");
        if (selectedClusterIds[id]) delete selectedClusterIds[id];
        else selectedClusterIds[id] = name;
        chip.classList.toggle("wh-fbo-chip--on");
        root.querySelector("#whFboDemandTable").innerHTML = renderDemandTable();
        bindClusterChecks(root);
      });
    });
  }

  function bindItemEditors(root) {
    root.querySelectorAll(".wh-fbo-item-qty").forEach(function (inp) {
      inp.addEventListener("change", function () {
        var idx = parseInt(inp.getAttribute("data-idx"), 10);
        createItems[idx].quantity = parseInt(inp.value, 10) || 1;
      });
    });
    root.querySelectorAll(".wh-fbo-item-remove").forEach(function (btn) {
      btn.addEventListener("click", function () {
        createItems.splice(parseInt(btn.getAttribute("data-idx"), 10), 1);
        root.querySelector("#whFboItemsTable").innerHTML = renderCreateItems();
        bindItemEditors(root);
      });
    });
  }

  function selectedClustersArray() {
    return Object.keys(selectedClusterIds).map(function (id) {
      return { macrolocal_cluster_id: parseInt(id, 10), name: selectedClusterIds[id] };
    });
  }

  function pollOzonFboJob(jobId, onProgress) {
    return new Promise(function (resolve, reject) {
      function tick() {
        fetchJson("/api/warehouse/marketplaces/ozon-fbo/jobs/" + encodeURIComponent(jobId))
          .then(function (data) {
            var job = data.job || {};
            if (typeof onProgress === "function") onProgress(job);
            if (job.status === "done") {
              resolve(job.result || {});
              return;
            }
            if (job.status === "failed") {
              reject(new Error(job.error || "Задача завершилась с ошибкой"));
              return;
            }
            setTimeout(tick, 2000);
          })
          .catch(reject);
      }
      tick();
    });
  }

  function fetchBatchData(batchId, opts) {
    opts = opts || {};
    return fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId);
  }

  function allocatedMap(cargoes, planItems) {
    var alloc = {};
    cargoes.forEach(function (c) {
      (c.items || []).forEach(function (it) {
        alloc[it.sku] = (alloc[it.sku] || 0) + Number(it.quantity || 0);
      });
    });
    var pool = [];
    (planItems || []).forEach(function (it) {
      var left = Number(it.quantity || 0) - (alloc[it.sku] || 0);
      if (left > 0) {
        pool.push({
          sku: it.sku,
          name: it.name,
          product_id: it.product_id,
          image_url: it.image_url || "",
          quantity: left,
        });
      }
    });
    return pool;
  }

  function planQtyForSku(supply, sku) {
    var total = 0;
    (supply.items || []).forEach(function (it) {
      if (it.sku === sku) total += Number(it.quantity || 0);
    });
    return total;
  }

  function allocatedQtyForSku(cargoes, sku, exceptCargoIdx, exceptItemIdx) {
    var used = 0;
    (cargoes || []).forEach(function (c, cIdx) {
      (c.items || []).forEach(function (it, iIdx) {
        if (it.sku !== sku) return;
        if (cIdx === exceptCargoIdx && iIdx === exceptItemIdx) return;
        used += Number(it.quantity || 0);
      });
    });
    return used;
  }

  function maxAllocatable(supply, sku, exceptCargoIdx, exceptItemIdx) {
    var cargoes = supply._cargoesDraft || supply.cargoes || [];
    var plan = planQtyForSku(supply, sku);
    var used = allocatedQtyForSku(cargoes, sku, exceptCargoIdx, exceptItemIdx);
    return Math.max(0, plan - used);
  }

  function renderProductThumb(imageUrl, name) {
    if (imageUrl) {
      return '<img class="wh-fbo-product-thumb" src="' + esc(imageUrl) + '" alt="" loading="lazy" />';
    }
    var letter = String(name || "?").trim().charAt(0) || "?";
    return '<span class="wh-fbo-product-thumb wh-fbo-product-thumb--empty">' + esc(letter) + "</span>";
  }

  function showCargoAddDialog(info) {
    return new Promise(function (resolve, reject) {
      var overlay = document.createElement("div");
      overlay.className = "wh-fbo-modal-overlay";
      var defaultQty = Math.max(1, Math.min(info.maxQty || 1, info.qty || 1));
      overlay.innerHTML =
        '<div class="wh-fbo-modal" role="dialog">' +
        '<div class="wh-fbo-modal-product">' +
        renderProductThumb(info.image_url, info.name) +
        "<div><strong>" + esc(info.name || info.sku) + "</strong>" +
        '<div class="wh-muted">Артикул ' + esc(info.sku) + "</div></div></div>" +
        '<label class="wh-fbo-modal-field">Количество' +
        ' <input type="number" class="wh-fbo-modal-qty" min="1" max="' + esc(info.maxQty) + '" value="' + esc(defaultQty) + '" /></label>' +
        '<label class="wh-fbo-modal-field">Годен до' +
        ' <input type="date" class="wh-fbo-modal-exp" value="' + esc(info.expiration_date || "") + '" /></label>' +
        '<p class="wh-muted wh-fbo-modal-hint">Доступно к распределению: ' + esc(info.maxQty) + " шт.</p>" +
        '<div class="wh-fbo-modal-actions">' +
        '<button type="button" class="wh-btn wh-fbo-modal-cancel">Отмена</button>' +
        '<button type="button" class="wh-btn wh-btn-primary wh-fbo-modal-ok">Добавить в ГМ</button>' +
        "</div></div>";
      document.body.appendChild(overlay);
      var qtyInp = overlay.querySelector(".wh-fbo-modal-qty");
      var expInp = overlay.querySelector(".wh-fbo-modal-exp");
      function close() {
        overlay.remove();
      }
      overlay.querySelector(".wh-fbo-modal-cancel").addEventListener("click", function () {
        close();
        reject(new Error("cancelled"));
      });
      overlay.addEventListener("click", function (e) {
        if (e.target === overlay) {
          close();
          reject(new Error("cancelled"));
        }
      });
      overlay.querySelector(".wh-fbo-modal-ok").addEventListener("click", function () {
        var qty = Math.max(1, parseInt(qtyInp.value || "1", 10) || 1);
        qty = Math.min(qty, info.maxQty || qty);
        close();
        resolve({ quantity: qty, expiration_date: expInp.value || "" });
      });
      setTimeout(function () { qtyInp.focus(); qtyInp.select(); }, 0);
    });
  }

  function syncCargoDraftFromDom(root) {
    root.querySelectorAll(".wh-fbo-supply-section").forEach(function (section) {
      var supplyId = section.getAttribute("data-supply-id");
      var supply = getSupplyDraft(root, supplyId);
      if (!supply || !supply._cargoesDraft) return;
      section.querySelectorAll(".wh-fbo-cargo-line").forEach(function (line) {
        var cIdx = parseInt(line.getAttribute("data-cargo"), 10);
        var iIdx = parseInt(line.getAttribute("data-item"), 10);
        var cargo = supply._cargoesDraft[cIdx];
        if (!cargo || !cargo.items || !cargo.items[iIdx]) return;
        var qtyInp = line.querySelector(".wh-fbo-line-qty");
        var expInp = line.querySelector(".wh-fbo-line-exp");
        if (qtyInp) {
          var maxQ = maxAllocatable(supply, cargo.items[iIdx].sku, cIdx, iIdx) + Number(cargo.items[iIdx].quantity || 0);
          var newQty = Math.max(1, parseInt(qtyInp.value || "1", 10) || 1);
          cargo.items[iIdx].quantity = Math.min(newQty, maxQ);
        }
        if (expInp) {
          cargo.items[iIdx].expiration_date = expInp.value || "";
        }
      });
    });
  }

  function cargoStateFromSupply(supply) {
    var cargoes = (supply.cargoes || []).map(function (c) {
      return {
        id: c.id,
        cargo_number: c.cargo_number,
        ozon_cargo_id: c.ozon_cargo_id || "",
        cargo_type: c.cargo_type || "",
        comment: c.comment || "",
        items: (c.items || []).map(function (i) {
          return {
            sku: i.sku,
            name: i.name,
            product_id: i.product_id,
            image_url: i.image_url || "",
            quantity: i.quantity,
            expiration_date: i.expiration_date || "",
          };
        }),
      };
    });
    if (!cargoes.length) {
      cargoes.push({ cargo_number: "1", ozon_cargo_id: "", cargo_type: "", comment: "", items: [] });
    }
    return cargoes;
  }

  function renderCargoBoard(supply, cargoes) {
    supply._cargoesDraft = cargoes;
    var pool = allocatedMap(cargoes, supply.items || []);
    var poolHtml = pool.length
      ? pool
          .map(function (it) {
            return (
              '<div class="wh-fbo-pool-item" draggable="true" data-sku="' + esc(it.sku) + '" data-name="' + esc(it.name) + '" data-product-id="' + esc(it.product_id || "") + '" data-image="' + esc(it.image_url || "") + '" data-max="' + esc(it.quantity) + '">' +
              '<div class="wh-fbo-product-row">' +
              renderProductThumb(it.image_url, it.name) +
              '<div class="wh-fbo-product-meta">' +
              '<div class="wh-fbo-product-sku">' + esc(it.sku) + "</div>" +
              '<div class="wh-fbo-product-name">' + esc(it.name || it.sku) + "</div>" +
              '<div class="wh-muted">Остаток: ' + esc(it.quantity) + " шт.</div>" +
              "</div></div>" +
              '<div class="wh-fbo-pool-actions">' +
              '<label class="wh-fbo-pool-qty-label">Взять <input type="number" min="1" max="' + esc(it.quantity) + '" class="wh-fbo-pool-qty" value="1" /> шт.</label>' +
              '<button type="button" class="wh-btn wh-btn-sm wh-fbo-pool-add" title="Добавить в грузоместо">+</button>' +
              "</div></div>"
            );
          })
          .join("")
      : '<p class="wh-muted">Все товары распределены.</p>';
    var cargoesHtml = cargoes
      .map(function (cargo, cIdx) {
        var itemsHtml = (cargo.items || [])
          .map(function (it, iIdx) {
            var maxQ = maxAllocatable(supply, it.sku, cIdx, iIdx) + Number(it.quantity || 0);
            return (
              '<div class="wh-fbo-cargo-line" data-cargo="' + cIdx + '" data-item="' + iIdx + '">' +
              '<div class="wh-fbo-product-row">' +
              renderProductThumb(it.image_url, it.name) +
              '<div class="wh-fbo-product-meta">' +
              '<div class="wh-fbo-product-sku">' + esc(it.sku) + "</div>" +
              '<div class="wh-fbo-product-name">' + esc(it.name || it.sku) + "</div>" +
              '<div class="wh-fbo-cargo-line-fields">' +
              '<label>Кол-во <input type="number" min="1" max="' + esc(maxQ) + '" class="wh-fbo-line-qty" value="' + esc(it.quantity) + '" /></label>' +
              '<label>Годен до <input type="date" class="wh-fbo-line-exp" value="' + esc(it.expiration_date || "") + '" /></label>' +
              "</div></div></div>" +
              '<button type="button" class="wh-btn wh-btn-sm wh-fbo-line-remove" data-cargo="' + cIdx + '" data-item="' + iIdx + '" title="Убрать">×</button></div>'
            );
          })
          .join("");
        return (
          '<div class="wh-fbo-cargo-drop" data-cargo="' + cIdx + '">' +
          '<div class="wh-fbo-cargo-drop-head">ГМ #' + esc(cargo.cargo_number || cIdx + 1) +
          (cargo.ozon_cargo_id ? ' <span class="wh-muted">(Ozon ' + esc(cargo.ozon_cargo_id) + ")</span>" : "") +
          ' <button type="button" class="wh-btn wh-btn-sm wh-fbo-cargo-del" data-cargo="' + cIdx + '">Удалить</button></div>' +
          '<div class="wh-fbo-cargo-drop-body">' + (itemsHtml || '<span class="wh-muted">Перетащите товар сюда</span>') + "</div></div>"
        );
      })
      .join("");
    return (
      '<div class="wh-fbo-cargo-board" data-supply-id="' + esc(supply.id) + '">' +
      '<div class="wh-fbo-pool"><h5>Нераспределено</h5><p class="wh-muted wh-fbo-pool-hint">Укажите количество и перетащите в ГМ. Shift — весь остаток.</p>' +
      '<div class="wh-fbo-pool-list">' + poolHtml + "</div></div>" +
      '<div class="wh-fbo-cargoes-col"><h5>Грузоместа <button type="button" class="wh-btn wh-btn-sm wh-fbo-add-cargo" data-supply-id="' + esc(supply.id) + '">+ ГМ</button></h5>' +
      cargoesHtml + "</div></div>"
    );
  }

  function addItemToCargo(supply, cIdx, payload, qty, expirationDate) {
    var left = maxAllocatable(supply, payload.sku, -1, -1);
    if (left < 1) return false;
    var moveQty = Math.min(qty || 1, left);
    var exp = expirationDate || "";
    var items = supply._cargoesDraft[cIdx].items || [];
    var existing = items.find(function (x) {
      return x.sku === payload.sku && (x.expiration_date || "") === exp;
    });
    if (existing) {
      var room = maxAllocatable(supply, payload.sku, cIdx, items.indexOf(existing)) + Number(existing.quantity || 0);
      existing.quantity = Math.min(Number(existing.quantity || 0) + moveQty, room);
    } else {
      items.push({
        sku: payload.sku,
        name: payload.name,
        product_id: payload.product_id,
        image_url: payload.image_url || "",
        quantity: moveQty,
        expiration_date: exp,
      });
    }
    return true;
  }

  function poolPayloadFromEl(el) {
    var qtyInp = el.querySelector(".wh-fbo-pool-qty");
    var maxQty = parseInt(el.getAttribute("data-max") || "1", 10) || 1;
    var qty = qtyInp ? parseInt(qtyInp.value || "1", 10) || 1 : 1;
    return {
      sku: el.getAttribute("data-sku"),
      name: el.getAttribute("data-name"),
      product_id: el.getAttribute("data-product-id") || null,
      image_url: el.getAttribute("data-image") || "",
      quantity: Math.max(1, Math.min(maxQty, qty)),
      maxQty: maxQty,
    };
  }

  function rerenderCargoSection(root, batch, tab, item, batchId, supplyId) {
    var supply = getSupplyDraft(root, supplyId);
    if (!supply) return;
    var section = root.querySelector('.wh-fbo-supply-section[data-supply-id="' + supplyId + '"]');
    if (!section) return;
    section.querySelector(".wh-fbo-cargo-board").outerHTML = renderCargoBoard(supply, supply._cargoesDraft);
    bindCargoBoard(root, batch, tab, item, batchId);
  }

  function renderBatchDetail(tab, item, batchId) {
    currentBatchId = batchId;
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    fetchBatchData(batchId)
      .then(function (data) {
        paintBatchDetail(tab, item, batchId, root, data.batch);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function paintBatchDetail(tab, item, batchId, root, batch) {
        var supplies = batch.supplies || [];
        var supplySections = supplies
          .map(function (s) {
            var cargoes = cargoStateFromSupply(s);
            s._cargoesDraft = cargoes;
            return (
              '<section class="wh-crm-section wh-fbo-supply-section" data-supply-id="' + esc(s.id) + '">' +
              "<h4>" + esc(s.ozon_cluster_name || s.title) +
              " · Ozon поставка #" + esc(s.ozon_supply_id || "—") +
              (s.ozon_order_id ? " (заявка " + esc(s.ozon_order_id) + ")" : "") +
              "</h4>" +
              '<p class="wh-muted">' + esc(s.ozon_warehouse_name) + " · " + esc(s.timeslot_label || "") + "</p>" +
              renderCargoBoard(s, cargoes) +
              '<div class="wh-fbo-supply-actions">' +
              '<button type="button" class="wh-btn wh-btn-primary wh-fbo-save-cargoes" data-supply-id="' + esc(s.id) + '" title="Сохранить черновик грузомест в системе">Сохранить</button>' +
              '<button type="button" class="wh-btn wh-fbo-send-cargoes" data-supply-id="' + esc(s.id) + '" title="Сохранить и отправить грузоместа в Ozon">Отправить в Ozon</button>' +
              '<details class="wh-fbo-more-actions"><summary>Ещё</summary>' +
              '<button type="button" class="wh-btn wh-btn-sm wh-fbo-sync-cargoes" data-supply-id="' + esc(s.id) + '">Обновить грузоместа из Ozon</button>' +
              '<button type="button" class="wh-btn wh-btn-sm wh-btn-danger wh-fbo-del-supply" data-supply-id="' + esc(s.id) + '">Удалить заявку</button>' +
              "</details></div></section>"
            );
          })
          .join("");
        root.innerHTML =
          '<div class="wh-fbo-layout">' +
          '<div class="wh-crm-form-toolbar wh-fbo-batch-toolbar">' +
          '<button type="button" class="wh-btn" id="whFboBatchBack">&larr; К списку</button>' +
          '<button type="button" class="wh-btn" id="whFboSyncAllCargoes" title="Подтянуть грузоместа и состав из Ozon">Обновить из Ozon</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whFboSendAllCargoes" title="Сохранить черновик и отправить все грузоместа в Ozon">Сохранить и отправить в Ozon</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whFboLoadAllLabels" title="Загрузить PDF этикеток из Ozon для всех поставок пакета">Загрузить этикетки из Ozon</button>' +
          (batch.labels_url
            ? '<a class="wh-btn" href="' + esc(batch.labels_url) + '" target="_blank" rel="noopener">Этикетки PDF</a>'
            : "") +
          '<button type="button" class="wh-btn" id="whFboExportSheets" title="Подготовить строки для Google Таблиц">Выгрузить в таблицы</button>' +
          '<button type="button" class="wh-btn wh-btn-danger" id="whFboDeleteBatch">Удалить пакет</button>' +
          "</div>" +
          '<p class="wh-muted wh-fbo-batch-hint">После отправки грузомест в Ozon нажмите «Загрузить этикетки» — в списке пакетов появится одна ссылка на объединённый PDF.</p>' +
          '<p class="wh-msg" id="whFboBatchMsg"></p>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">' + esc(batch.title) + "</h4>" +
          "<p>" + esc(batch.delivery_type_label) + " · " + esc(batch.timeslot_label || "—") + " · " + esc(batch.supply_count) + " заявок</p>" +
          renderBatchOpsForm(batch) +
          "</section>" +
          '<section class="wh-crm-section wh-fbo-ops-summary-section"><h4 class="wh-crm-section-title">Сводка для таблиц</h4>' +
          '<p class="wh-muted">Редактируйте ячейки в таблице ниже или в форме выше — одна строка на пакет.</p>' +
          '<div id="whFboOpsSummaryTables">' + renderOpsSummaryTables(opsSummaryFromBatch(batch)) + "</div></section>" +
          supplySections +
          "</div>";
        batch.supplies.forEach(function (s) { s._cargoesDraft = cargoStateFromSupply(s); });
        root._batchData = batch;
        root.querySelector("#whFboBatchBack").addEventListener("click", function () {
          renderBatchList(tab, item);
        });
        root.querySelector("#whFboDeleteBatch").addEventListener("click", function () {
          if (!confirm("Удалить пакет «" + batch.title + "» из системы? Заявки в Ozon останутся.")) return;
          var msg = root.querySelector("#whFboBatchMsg");
          fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId, { method: "DELETE" })
            .then(function () {
              renderBatchList(tab, item);
            })
            .catch(function (err) { setMsg(msg, err.message, true); });
        });
        root.querySelector("#whFboSyncAllCargoes").addEventListener("click", function () {
          var msg = root.querySelector("#whFboBatchMsg");
          setMsg(msg, "Обновление данных из Ozon…", false);
          fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId + "/cargoes/sync-from-ozon", {
            method: "POST",
            body: "{}",
          })
            .then(function (data) {
              if (!data.job_id) throw new Error("Сервер не вернул job_id");
              return pollOzonFboJob(data.job_id, function (job) {
                setMsg(msg, "Синхронизация из Ozon… (" + (job.status || "running") + ")", false);
              });
            })
            .then(function () {
              return fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId);
            })
            .then(function (fresh) {
              paintBatchDetail(tab, item, batchId, root, fresh.batch);
              setMsg(msg, "Данные обновлены из Ozon.", false);
            })
            .catch(function (err) { setMsg(msg, err.message, true); });
        });
        root.querySelector("#whFboSendAllCargoes").addEventListener("click", function () {
          syncCargoDraftFromDom(root);
          var msg = root.querySelector("#whFboBatchMsg");
          setMsg(msg, "Сохранение и отправка в Ozon…", false);
          var supplies = (root._batchData && root._batchData.supplies) || batch.supplies || [];
          var saves = supplies.map(function (s) {
            var draft = getSupplyDraft(root, s.id) || s;
            return fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + s.id + "/cargoes", {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ cargoes: draft._cargoesDraft || [] }),
            });
          });
          Promise.all(saves)
            .then(function () {
              return fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId + "/cargoes/send-all", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: "{}",
              });
            })
            .then(function (data) {
              if (!data.job_id) throw new Error("Сервер не вернул job_id");
              return pollOzonFboJob(data.job_id, function (job) {
                setMsg(msg, "Отправка в Ozon… (" + (job.status || "running") + ")", false);
              });
            })
            .then(function (r) {
              var errs = (r.results || []).filter(function (x) { return x.error; });
              var oks = (r.results || []).filter(function (x) { return x.ok; });
              if (errs.length) {
                setMsg(
                  msg,
                  "Ошибки: " + errs.map(function (e) { return "#" + e.supply_id + ": " + e.error; }).join("; ") +
                    (oks.length ? " · Успешно: " + oks.length : ""),
                  true
                );
              } else {
                setMsg(msg, "Грузоместа отправлены в Ozon (" + oks.length + ").", false);
              }
              return fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId);
            })
            .then(function (fresh) {
              if (fresh && fresh.batch) paintBatchDetail(tab, item, batchId, root, fresh.batch);
            })
            .catch(function (err) { setMsg(msg, err.message, true); });
        });
        root.querySelector("#whFboLoadAllLabels").addEventListener("click", function () {
          var msg = root.querySelector("#whFboBatchMsg");
          setMsg(msg, "Загрузка этикеток из Ozon для всех заявок пакета…", false);
          fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId + "/labels/generate", {
            method: "POST",
            body: "{}",
          })
            .then(function (data) {
              if (!data.job_id) throw new Error("Сервер не вернул job_id");
              return pollOzonFboJob(data.job_id, function (job) {
                setMsg(msg, "Загрузка этикеток… (" + (job.status || "running") + ")", false);
              });
            })
            .then(function (result) {
              var rows = (result && result.results) || [];
              var ok = rows.filter(function (r) { return r.ok_count; }).length;
              var failed = rows.filter(function (r) { return r.error; });
              if (failed.length) {
                setMsg(
                  msg,
                  "Загружено: " + ok + " из " + rows.length + ". Ошибки: " +
                    failed.map(function (r) { return "#" + r.supply_id + ": " + r.error; }).join("; "),
                  true
                );
              } else {
                setMsg(msg, "Этикетки сохранены. Ссылка появится в списке пакетов.", false);
              }
              return fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId);
            })
            .then(function (fresh) {
              if (fresh && fresh.batch) paintBatchDetail(tab, item, batchId, root, fresh.batch);
            })
            .catch(function (err) { setMsg(msg, err.message, true); });
        });
        bindCargoBoard(root, batch, tab, item, batchId);
        bindOpsSummaryExport(tab, item, root, batchId);
        bindBatchOpsForm(tab, item, root, batchId);
        refreshBatchOpsSummaryBox(root.querySelector("#whFboOpsSummaryTables"), tab, item, batchId, root);
  }

  function getSupplyDraft(root, supplyId) {
    var batch = root._batchData;
    if (!batch) return null;
    for (var i = 0; i < batch.supplies.length; i++) {
      if (String(batch.supplies[i].id) === String(supplyId)) return batch.supplies[i];
    }
    return null;
  }

  function bindCargoBoard(root, batch, tab, item, batchId) {
    root.querySelectorAll(".wh-fbo-pool-item").forEach(function (el) {
      el.addEventListener("dragstart", function (e) {
        var payload = poolPayloadFromEl(el);
        if (e.shiftKey) payload.quantity = payload.maxQty;
        dragPayload = payload;
        e.dataTransfer.setData("text/plain", dragPayload.sku);
      });
      el.querySelectorAll(".wh-fbo-pool-qty, .wh-fbo-pool-add").forEach(function (inp) {
        inp.addEventListener("mousedown", function (e) { e.stopPropagation(); });
        inp.addEventListener("click", function (e) { e.stopPropagation(); });
      });
      var addBtn = el.querySelector(".wh-fbo-pool-add");
      if (addBtn) {
        addBtn.addEventListener("click", function () {
          var board = el.closest(".wh-fbo-cargo-board");
          var supplyId = board.getAttribute("data-supply-id");
          var supply = getSupplyDraft(root, supplyId);
          if (!supply) return;
          var payload = poolPayloadFromEl(el);
          var left = maxAllocatable(supply, payload.sku, -1, -1);
          if (left < 1) return;
          var cIdx = 0;
          if (!supply._cargoesDraft.length) {
            supply._cargoesDraft.push({ cargo_number: "1", comment: "", items: [] });
          }
          showCargoAddDialog({
            sku: payload.sku,
            name: payload.name,
            image_url: payload.image_url,
            product_id: payload.product_id,
            maxQty: left,
            qty: payload.quantity,
          }).then(function (result) {
            addItemToCargo(supply, cIdx, payload, result.quantity, result.expiration_date);
            rerenderCargoSection(root, batch, tab, item, batchId, supplyId);
          }).catch(function () {});
        });
      }
    });
    root.querySelectorAll(".wh-fbo-cargo-drop-body").forEach(function (zone) {
      zone.addEventListener("dragover", function (e) { e.preventDefault(); zone.classList.add("wh-fbo-drop--over"); });
      zone.addEventListener("dragleave", function () { zone.classList.remove("wh-fbo-drop--over"); });
      zone.addEventListener("drop", function (e) {
        e.preventDefault();
        zone.classList.remove("wh-fbo-drop--over");
        if (!dragPayload) return;
        var cargoEl = zone.closest(".wh-fbo-cargo-drop");
        var board = zone.closest(".wh-fbo-cargo-board");
        var supplyId = board.getAttribute("data-supply-id");
        var supply = getSupplyDraft(root, supplyId);
        if (!supply) return;
        var cIdx = parseInt(cargoEl.getAttribute("data-cargo"), 10);
        var left = maxAllocatable(supply, dragPayload.sku, -1, -1);
        if (left < 1) {
          dragPayload = null;
          return;
        }
        var payload = dragPayload;
        dragPayload = null;
        showCargoAddDialog({
          sku: payload.sku,
          name: payload.name,
          image_url: payload.image_url,
          product_id: payload.product_id,
          maxQty: left,
          qty: payload.quantity,
        }).then(function (result) {
          addItemToCargo(supply, cIdx, payload, result.quantity, result.expiration_date);
          rerenderCargoSection(root, batch, tab, item, batchId, supplyId);
        }).catch(function () {});
      });
    });
    root.querySelectorAll(".wh-fbo-line-qty").forEach(function (inp) {
      inp.addEventListener("change", function () {
        var line = inp.closest(".wh-fbo-cargo-line");
        var supplyId = inp.closest(".wh-fbo-supply-section").getAttribute("data-supply-id");
        var supply = getSupplyDraft(root, supplyId);
        if (!supply || !line) return;
        var cIdx = parseInt(line.getAttribute("data-cargo"), 10);
        var iIdx = parseInt(line.getAttribute("data-item"), 10);
        var lineItem = supply._cargoesDraft[cIdx].items[iIdx];
        var maxQ = maxAllocatable(supply, lineItem.sku, cIdx, iIdx) + Number(lineItem.quantity || 0);
        var newQty = Math.max(1, parseInt(inp.value || "1", 10) || 1);
        lineItem.quantity = Math.min(newQty, maxQ);
        rerenderCargoSection(root, batch, tab, item, batchId, supplyId);
      });
    });
    root.querySelectorAll(".wh-fbo-line-exp").forEach(function (inp) {
      inp.addEventListener("change", function () {
        var line = inp.closest(".wh-fbo-cargo-line");
        var supplyId = inp.closest(".wh-fbo-supply-section").getAttribute("data-supply-id");
        var supply = getSupplyDraft(root, supplyId);
        if (!supply || !line) return;
        var cIdx = parseInt(line.getAttribute("data-cargo"), 10);
        var iIdx = parseInt(line.getAttribute("data-item"), 10);
        supply._cargoesDraft[cIdx].items[iIdx].expiration_date = inp.value || "";
      });
    });
    root.querySelectorAll(".wh-fbo-line-remove").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var supplyId = btn.closest(".wh-fbo-supply-section").getAttribute("data-supply-id");
        var supply = getSupplyDraft(root, supplyId);
        if (!supply) return;
        var cIdx = parseInt(btn.getAttribute("data-cargo"), 10);
        var iIdx = parseInt(btn.getAttribute("data-item"), 10);
        supply._cargoesDraft[cIdx].items.splice(iIdx, 1);
        rerenderCargoSection(root, batch, tab, item, batchId, supplyId);
      });
    });
    root.querySelectorAll(".wh-fbo-add-cargo").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var supplyId = btn.getAttribute("data-supply-id");
        var supply = getSupplyDraft(root, supplyId);
        if (!supply) return;
        supply._cargoesDraft.push({ cargo_number: String(supply._cargoesDraft.length + 1), comment: "", items: [] });
        rerenderCargoSection(root, batch, tab, item, batchId, supplyId);
      });
    });
    root.querySelectorAll(".wh-fbo-cargo-del").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var supplyId = btn.closest(".wh-fbo-supply-section").getAttribute("data-supply-id");
        var supply = getSupplyDraft(root, supplyId);
        if (!supply) return;
        var cIdx = parseInt(btn.getAttribute("data-cargo"), 10);
        supply._cargoesDraft.splice(cIdx, 1);
        rerenderCargoSection(root, batch, tab, item, batchId, supplyId);
      });
    });
        root.querySelectorAll(".wh-fbo-save-cargoes").forEach(function (btn) {
      btn.addEventListener("click", function () {
        syncCargoDraftFromDom(root);
        var supplyId = btn.getAttribute("data-supply-id");
        var supply = getSupplyDraft(root, supplyId);
        if (!supply) return;
        var msg = root.querySelector("#whFboBatchMsg");
        fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/cargoes", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cargoes: supply._cargoesDraft }),
        }).then(function () {
          setMsg(msg, "Грузоместа сохранены для заявки #" + supplyId, false);
        }).catch(function (err) { setMsg(msg, err.message, true); });
      });
    });
    root.querySelectorAll(".wh-fbo-send-cargoes").forEach(function (btn) {
      btn.addEventListener("click", function () {
        syncCargoDraftFromDom(root);
        var supplyId = btn.getAttribute("data-supply-id");
        var supply = getSupplyDraft(root, supplyId);
        if (!supply) return;
        var msg = root.querySelector("#whFboBatchMsg");
        setMsg(msg, "Сохранение и отправка…", false);
        fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/cargoes", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cargoes: supply._cargoesDraft }),
        })
          .then(function () {
            return fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/ozon/cargoes", { method: "POST", body: "{}" });
          })
          .then(function () {
            setMsg(msg, "Грузоместа отправлены в Ozon для заявки #" + supplyId, false);
          })
          .catch(function (err) { setMsg(msg, err.message, true); });
      });
    });
    root.querySelectorAll(".wh-fbo-sync-cargoes").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var supplyId = btn.getAttribute("data-supply-id");
        var msg = root.querySelector("#whFboBatchMsg");
        setMsg(msg, "Обновление из Ozon…", false);
        fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/ozon/cargoes/sync", {
          method: "POST",
          body: "{}",
        })
          .then(function () {
            return fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId);
          })
          .then(function (fresh) {
            paintBatchDetail(tab, item, batchId, root, fresh.batch);
            setMsg(msg, "Данные обновлены из Ozon.", false);
          })
          .catch(function (err) { setMsg(msg, err.message, true); });
      });
    });
    root.querySelectorAll(".wh-fbo-del-supply").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var supplyId = btn.getAttribute("data-supply-id");
        if (!confirm("Удалить заявку #" + supplyId + " из системы? В Ozon она останется.")) return;
        var msg = root.querySelector("#whFboBatchMsg");
        fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId, { method: "DELETE" })
          .then(function () {
            return fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId);
          })
          .then(function (fresh) {
            if (!fresh.batch || !(fresh.batch.supplies || []).length) {
              renderBatchList(tab, item);
              return;
            }
            paintBatchDetail(tab, item, batchId, root, fresh.batch);
            setMsg(msg, "Заявка удалена из системы.", false);
          })
          .catch(function (err) { setMsg(msg, err.message, true); });
      });
    });
  }

  function bindBatchOpsForm(tab, item, root, batchId) {
    var section = root.querySelector(".wh-fbo-batch-ops");
    if (!section) return;
    var refreshForm = function () {
      var batch = root._batchData;
      if (!batch) return;
      var opsBox = root.querySelector(".wh-fbo-batch-ops");
      if (opsBox) {
        opsBox.outerHTML = renderBatchOpsForm(batch);
        bindBatchOpsForm(tab, item, root, batchId);
      }
    };
    var addrSelect = section.querySelector('[data-ops-field="ops_unload_address_id"]');
    bindConfigureSelect(addrSelect, function () {
      modalUnloadAddressEditor(refreshForm);
    });
    var statusSelect = section.querySelector('[data-ops-field="ops_packing_status_id"]');
    if (statusSelect) {
      statusSelect.setAttribute("data-prev", statusSelect.value || "");
      initPackingStatusSelect(statusSelect, function () {
        modalPackingStatusesEditor(refreshForm);
      });
    }
    bindSupplyTypeCells(section, function () {
      modalSupplyTypesEditor(refreshForm);
    });
    bindPackerPickers(section);
    var saveBtn = root.querySelector("#whFboSaveBatchOps");
    if (!saveBtn || saveBtn.getAttribute("data-bound") === "1") return;
    saveBtn.setAttribute("data-bound", "1");
    saveBtn.addEventListener("click", function () {
      var opsSection = root.querySelector(".wh-fbo-batch-ops");
      if (!opsSection) return;
      var msg = root.querySelector("#whFboBatchMsg");
      var ops = collectOpsPayload(opsSection);
      setMsg(msg, "Сохранение…", false);
      fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId + "/ops", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ops: ops }),
      })
        .then(function (data) {
          var batch = data.batch;
          if (batch) {
            root._batchData = batch;
            var summaryBox = root.querySelector("#whFboOpsSummaryTables");
            if (summaryBox) {
              summaryBox.innerHTML = renderOpsSummaryTables(opsSummaryFromBatch(batch));
              refreshBatchOpsSummaryBox(summaryBox, tab, item, batchId, root);
            }
            var opsBox = root.querySelector(".wh-fbo-batch-ops");
            if (opsBox) {
              opsBox.outerHTML = renderBatchOpsForm(batch);
              bindBatchOpsForm(tab, item, root, batchId);
            }
          }
          setMsg(msg, "Данные для таблиц сохранены.", false);
        })
        .catch(function (err) {
          setMsg(msg, err.message, true);
        });
    });
  }

  function render(tab, item) {
    loadMeta()
      .then(function () {
        if (currentBatchId) renderBatchDetail(tab, item, currentBatchId);
        else renderBatchList(tab, item);
      })
      .catch(function (err) {
        preparePanel(tab, item);
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  global.WhOzonFboSupplies = { render: render, renderBatchList: renderBatchList };
})(window);
