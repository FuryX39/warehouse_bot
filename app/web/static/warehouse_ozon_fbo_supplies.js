/** Поставки FBO — менеджер: потребность, пакеты заявок, грузоместа, этикетки. */
(function (global) {
  var meta = { delivery_types: [], supply_kinds: [], batch_statuses: [] };
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
  var listView = "overview";

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
          "<td>" + esc(o.warehouse_name || "—") + "</td>" +
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

  function renderBatchList(tab, item) {
    currentBatchId = null;
    listView = "overview";
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка заявок из Ozon и локальных пакетов…</p>';
    Promise.all([
      loadMeta(),
      fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches"),
      fetchJson("/api/warehouse/marketplaces/ozon-fbo/ozon/supply-orders?scope=" + encodeURIComponent(ozonScope)),
    ])
      .then(function (parts) {
        var batches = (parts[1].batches || []);
        var ozonData = parts[2] || {};
        var ozonOrders = ozonData.orders || [];
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
              "<td>" + esc(formatTs(b.updated_at_ts)) + "</td></tr>"
            );
          })
          .join("");
        root.innerHTML =
          '<div class="wh-fbo-layout">' +
          '<div class="wh-crm-toolbar">' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whFboNewBatch">+ Создать пакет заявок</button>' +
          '<button type="button" class="wh-btn" id="whFboRefreshBatches">Обновить</button></div>' +
          '<p class="wh-msg" id="whFboListMsg"></p>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Заявки в Ozon <span class="wh-muted">(' + esc(ozonData.count || 0) + ")</span></h4>" +
          '<p class="wh-muted">Актуальные данные из Ozon Seller API. Заявки, созданные вне панели (в т.ч. через API), тоже отображаются здесь.</p>' +
          '<div class="wh-fbo-row">' +
          '<label>Фильтр</label><select id="whFboOzonScope" class="wh-fbo-input">' +
          '<option value="active"' + (ozonScope === "active" ? " selected" : "") + ">Активные</option>" +
          '<option value="archive"' + (ozonScope === "archive" ? " selected" : "") + ">Архив (завершённые и отменённые)</option>" +
          '<option value="all"' + (ozonScope === "all" ? " selected" : "") + ">Все</option>" +
          "</select>" +
          '<button type="button" class="wh-btn" id="whFboImportOzon">Импортировать выбранные в систему</button>' +
          "</div>" +
          '<div id="whFboOzonTable">' + renderOzonOrdersTable(ozonOrders) + "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Пакеты в системе</h4>' +
          (batchRows
            ? '<table class="wh-employees-table wh-crm-table wh-fbo-batch-table"><thead><tr>' +
              "<th>№</th><th>Название</th><th>Доставка</th><th>Слот</th><th>Заявок</th><th>Статус</th><th>Обновлено</th>" +
              "</tr></thead><tbody>" + batchRows + "</tbody></table>"
            : '<p class="wh-msg">Пакетов в системе пока нет.</p>') +
          "</section></div>";
        root.querySelector("#whFboNewBatch").addEventListener("click", function () {
          resetCreateState();
          renderCreateWizard(tab, item);
        });
        root.querySelector("#whFboRefreshBatches").addEventListener("click", function () {
          renderBatchList(tab, item);
        });
        root.querySelector("#whFboOzonScope").addEventListener("change", function (e) {
          ozonScope = e.target.value || "active";
          renderBatchList(tab, item);
        });
        root.querySelector("#whFboImportOzon").addEventListener("click", function () {
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
                renderBatchList(tab, item);
              }
            })
            .catch(function (err) {
              setMsg(root.querySelector("#whFboListMsg"), err.message, true);
            });
        });
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
        root.querySelectorAll(".wh-fbo-batch-table tbody tr[data-id]").forEach(function (tr) {
          tr.style.cursor = "pointer";
          tr.addEventListener("click", function () {
            renderBatchDetail(tab, item, parseInt(tr.getAttribute("data-id"), 10));
          });
        });
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
          "<p><strong>Создана:</strong> " + esc(o.created_label) + " · <strong>№:</strong> " + esc(o.order_number) + "</p>" +
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
          '<input type="text" id="whFboDropoffSearch" placeholder="Поиск точки отгрузки (мин. 4 символа)" class="wh-fbo-input wh-fbo-input--wide" />' +
          '<button type="button" class="wh-btn" id="whFboSearchDropoff">Найти</button>' +
          '<span id="whFboDropoffPicked" class="wh-muted"></span></div>' +
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

  function bindCreateEvents(tab, item, root) {
    var msg = root.querySelector("#whFboCreateMsg");
    root.querySelector("#whFboCreateBack").addEventListener("click", function () {
      renderBatchList(tab, item);
    });
    root.querySelector("#whFboDeliveryType").addEventListener("change", function () {
      var isCross = root.querySelector("#whFboDeliveryType").value === "crossdock";
      root.querySelector("#whFboDropoffRow").hidden = !isCross;
    });
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
        var rows = [];
        function walk(x) {
          if (!x) return;
          if (Array.isArray(x)) return x.forEach(walk);
          if (typeof x !== "object") return;
          var wid = x.warehouse_id || x.id;
          var name = x.name || x.warehouse_name;
          if (wid && name) rows.push({ id: String(wid), name: String(name) });
          Object.keys(x).forEach(function (k) { walk(x[k]); });
        }
        walk(resp.data);
        list.innerHTML = rows.slice(0, 30).map(function (r) {
          return '<button type="button" class="wh-fbo-dropoff-pick wh-btn wh-btn-sm" data-id="' + esc(r.id) + '" data-name="' + esc(r.name) + '">' + esc(r.name) + "</button> ";
        }).join("") || '<p class="wh-muted">Ничего не найдено</p>';
        list.querySelectorAll(".wh-fbo-dropoff-pick").forEach(function (btn) {
          btn.addEventListener("click", function () {
            dropoffPick = { id: btn.getAttribute("data-id"), name: btn.getAttribute("data-name") };
            root.querySelector("#whFboDropoffPicked").textContent = "Выбрано: " + dropoffPick.name;
          });
        });
      }).catch(function (err) { setMsg(msg, err.message, true); });
    });
    root.querySelector("#whFboLoadSlots").addEventListener("click", function () {
      var clusters = selectedClustersArray();
      if (!clusters.length) return setMsg(msg, "Выберите кластер", true);
      if (!createItems.length) return setMsg(msg, "Добавьте товары", true);
      var body = {
        delivery_type: root.querySelector("#whFboDeliveryType").value,
        dropoff_warehouse_id: dropoffPick ? dropoffPick.id : "",
        clusters: clusters,
        items: createItems,
        date_from: root.querySelector("#whFboSlotFrom").value,
        date_to: root.querySelector("#whFboSlotTo").value,
      };
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
      var body = {
        title: root.querySelector("#whFboBatchTitle").value.trim() || "Пакет FBO",
        delivery_type: delivery,
        supply_kind: root.querySelector("#whFboSupplyKind").value,
        dropoff_warehouse_id: dropoffPick ? dropoffPick.id : "",
        dropoff_warehouse_name: dropoffPick ? dropoffPick.name : "",
        timeslot: selectedSlot,
        clusters: clusters,
        items: createItems,
      };
      setMsg(msg, "Создание заявок в Ozon… Это может занять несколько минут.", false);
      root.querySelector("#whFboSubmitBatch").disabled = true;
      fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (data) {
        setMsg(msg, "Создано заявок: " + (data.created || 0) + ", ошибок: " + (data.errors || 0), !data.created);
        if (data.batch && data.batch.id) {
          renderBatchDetail(tab, item, data.batch.id);
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

  function cargoStateFromSupply(supply) {
    var cargoes = (supply.cargoes || []).map(function (c) {
      return {
        cargo_number: c.cargo_number,
        comment: c.comment || "",
        items: (c.items || []).map(function (i) {
          return {
            sku: i.sku,
            name: i.name,
            product_id: i.product_id,
            quantity: i.quantity,
            expiration_date: i.expiration_date || "",
          };
        }),
      };
    });
    if (!cargoes.length) {
      cargoes.push({ cargo_number: "1", comment: "", items: [] });
    }
    return cargoes;
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
        pool.push({ sku: it.sku, name: it.name, product_id: it.product_id, quantity: left });
      }
    });
    return pool;
  }

  function renderCargoBoard(supply, cargoes) {
    var pool = allocatedMap(cargoes, supply.items || []);
    var poolHtml = pool.length
      ? pool
          .map(function (it) {
            return (
              '<div class="wh-fbo-pool-item" draggable="true" data-sku="' + esc(it.sku) + '" data-name="' + esc(it.name) + '" data-product-id="' + esc(it.product_id || "") + '" data-qty="1">' +
              esc(it.name || it.sku) + " · " + esc(it.sku) +
              " <span class=\"wh-muted\">(остаток " + esc(it.quantity) + ")</span></div>"
            );
          })
          .join("")
      : '<p class="wh-muted">Все товары распределены.</p>';
    var cargoesHtml = cargoes
      .map(function (cargo, cIdx) {
        var itemsHtml = (cargo.items || [])
          .map(function (it, iIdx) {
            return (
              '<div class="wh-fbo-cargo-line" data-cargo="' + cIdx + '" data-item="' + iIdx + '">' +
              esc(it.name || it.sku) + " × " + esc(it.quantity) +
              ' <button type="button" class="wh-btn wh-btn-sm wh-fbo-line-remove" data-cargo="' + cIdx + '" data-item="' + iIdx + '">×</button></div>'
            );
          })
          .join("");
        return (
          '<div class="wh-fbo-cargo-drop" data-cargo="' + cIdx + '">' +
          '<div class="wh-fbo-cargo-drop-head">ГМ #' + esc(cargo.cargo_number || cIdx + 1) +
          ' <button type="button" class="wh-btn wh-btn-sm wh-fbo-cargo-del" data-cargo="' + cIdx + '">Удалить</button></div>' +
          '<div class="wh-fbo-cargo-drop-body">' + (itemsHtml || '<span class="wh-muted">Перетащите товар сюда</span>') + "</div></div>"
        );
      })
      .join("");
    return (
      '<div class="wh-fbo-cargo-board" data-supply-id="' + esc(supply.id) + '">' +
      '<div class="wh-fbo-pool"><h5>Нераспределено</h5><div class="wh-fbo-pool-list">' + poolHtml + "</div></div>" +
      '<div class="wh-fbo-cargoes-col"><h5>Грузоместа <button type="button" class="wh-btn wh-btn-sm wh-fbo-add-cargo" data-supply-id="' + esc(supply.id) + '">+ ГМ</button></h5>' +
      cargoesHtml + "</div></div>"
    );
  }

  function renderBatchDetail(tab, item, batchId) {
    currentBatchId = batchId;
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId)
      .then(function (data) {
        var batch = data.batch;
        var supplies = batch.supplies || [];
        var supplySections = supplies
          .map(function (s) {
            var cargoes = cargoStateFromSupply(s);
            s._cargoesDraft = cargoes;
            return (
              '<section class="wh-crm-section wh-fbo-supply-section" data-supply-id="' + esc(s.id) + '">' +
              "<h4>" + esc(s.ozon_cluster_name || s.title) + " · Ozon #" + esc(s.ozon_supply_id || "—") + "</h4>" +
              '<p class="wh-muted">' + esc(s.ozon_warehouse_name) + " · " + esc(s.timeslot_label || "") + "</p>" +
              renderCargoBoard(s, cargoes) +
              '<button type="button" class="wh-btn wh-fbo-save-cargoes" data-supply-id="' + esc(s.id) + '">Сохранить грузоместа</button></section>'
            );
          })
          .join("");
        root.innerHTML =
          '<div class="wh-fbo-layout">' +
          '<div class="wh-crm-form-toolbar">' +
          '<button type="button" class="wh-btn" id="whFboBatchBack">&larr; К списку</button>' +
          '<button type="button" class="wh-btn" id="whFboSendAllCargoes">Отправить все ГМ в Ozon</button>' +
          '<button type="button" class="wh-btn" id="whFboGenAllLabels">Сгенерировать этикетки</button>' +
          '<a class="wh-btn wh-btn-primary" id="whFboAllLabelsPdf" href="/api/warehouse/marketplaces/ozon-fbo/batches/' + esc(batchId) + '/labels.pdf" target="_blank">Скачать все этикетки (PDF)</a>' +
          "</div>" +
          '<p class="wh-msg" id="whFboBatchMsg"></p>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">' + esc(batch.title) + "</h4>" +
          "<p>" + esc(batch.delivery_type_label) + " · " + esc(batch.timeslot_label || "—") + " · " + esc(batch.supply_count) + " заявок</p></section>" +
          supplySections +
          "</div>";
        batch.supplies.forEach(function (s) { s._cargoesDraft = cargoStateFromSupply(s); });
        root._batchData = batch;
        root.querySelector("#whFboBatchBack").addEventListener("click", function () {
          renderBatchList(tab, item);
        });
        root.querySelector("#whFboSendAllCargoes").addEventListener("click", function () {
          var msg = root.querySelector("#whFboBatchMsg");
          setMsg(msg, "Отправка грузомест…", false);
          fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId + "/cargoes/send-all", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
          }).then(function (r) {
            setMsg(msg, "Готово. Проверьте статусы в Ozon.", false);
          }).catch(function (err) { setMsg(msg, err.message, true); });
        });
        root.querySelector("#whFboGenAllLabels").addEventListener("click", function () {
          var msg = root.querySelector("#whFboBatchMsg");
          setMsg(msg, "Генерация этикеток…", false);
          fetchJson("/api/warehouse/marketplaces/ozon-fbo/batches/" + batchId + "/labels/generate", {
            method: "POST",
            body: "{}",
          }).then(function () {
            setMsg(msg, "Запрос отправлен. Через минуту можно скачать PDF.", false);
          }).catch(function (err) { setMsg(msg, err.message, true); });
        });
        bindCargoBoard(root, batch);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function getSupplyDraft(root, supplyId) {
    var batch = root._batchData;
    if (!batch) return null;
    for (var i = 0; i < batch.supplies.length; i++) {
      if (String(batch.supplies[i].id) === String(supplyId)) return batch.supplies[i];
    }
    return null;
  }

  function bindCargoBoard(root, batch) {
    root.querySelectorAll(".wh-fbo-pool-item").forEach(function (el) {
      el.addEventListener("dragstart", function (e) {
        dragPayload = {
          sku: el.getAttribute("data-sku"),
          name: el.getAttribute("data-name"),
          product_id: el.getAttribute("data-product-id") || null,
        };
        e.dataTransfer.setData("text/plain", dragPayload.sku);
      });
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
        var pool = allocatedMap(supply._cargoesDraft, supply.items || []);
        var left = 0;
        pool.forEach(function (p) { if (p.sku === dragPayload.sku) left = p.quantity; });
        if (left < 1) return;
        var existing = (supply._cargoesDraft[cIdx].items || []).find(function (x) { return x.sku === dragPayload.sku; });
        if (existing) existing.quantity += 1;
        else supply._cargoesDraft[cIdx].items.push({ sku: dragPayload.sku, name: dragPayload.name, product_id: dragPayload.product_id, quantity: 1, expiration_date: "" });
        var section = root.querySelector('.wh-fbo-supply-section[data-supply-id="' + supplyId + '"]');
        section.querySelector(".wh-fbo-cargo-board").outerHTML = renderCargoBoard(supply, supply._cargoesDraft);
        bindCargoBoard(root, batch);
        dragPayload = null;
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
        var section = root.querySelector('.wh-fbo-supply-section[data-supply-id="' + supplyId + '"]');
        section.querySelector(".wh-fbo-cargo-board").outerHTML = renderCargoBoard(supply, supply._cargoesDraft);
        bindCargoBoard(root, batch);
      });
    });
    root.querySelectorAll(".wh-fbo-add-cargo").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var supplyId = btn.getAttribute("data-supply-id");
        var supply = getSupplyDraft(root, supplyId);
        if (!supply) return;
        supply._cargoesDraft.push({ cargo_number: String(supply._cargoesDraft.length + 1), comment: "", items: [] });
        var section = root.querySelector('.wh-fbo-supply-section[data-supply-id="' + supplyId + '"]');
        section.querySelector(".wh-fbo-cargo-board").outerHTML = renderCargoBoard(supply, supply._cargoesDraft);
        bindCargoBoard(root, batch);
      });
    });
    root.querySelectorAll(".wh-fbo-save-cargoes").forEach(function (btn) {
      btn.addEventListener("click", function () {
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
