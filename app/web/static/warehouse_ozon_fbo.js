(function (global) {
  var meta = { assignees: [], supply_kinds: [], statuses: [] };
  var managerFilters = {};
  var itemDraft = [];
  var cargoDraft = [];
  var currentSupply = null;

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

  function loadMeta() {
    return fetchJson("/api/warehouse/marketplaces/ozon-fbo/meta").then(function (data) {
      meta.assignees = data.assignees || [];
      meta.supply_kinds = data.supply_kinds || [];
      meta.statuses = data.statuses || [];
      return meta;
    });
  }

  function statusName(id) {
    for (var i = 0; i < meta.statuses.length; i++) {
      if (meta.statuses[i].id === id) return meta.statuses[i].name;
    }
    return id || "—";
  }

  function kindName(id) {
    return id === "box" ? "Короба" : "Паллеты";
  }

  function apiDetail(obj) {
    try {
      return JSON.stringify(obj, null, 2);
    } catch (e) {
      return String(obj || "");
    }
  }

  function setMsg(el, text, isError) {
    if (!el) return;
    el.className = "wh-msg" + (isError ? " wh-msg-error" : " wh-msg-ok");
    el.textContent = text || "";
  }

  function collectOzonRows(data, idKeys, nameKeys) {
    var out = [];
    var seen = {};
    function walk(x) {
      if (!x) return;
      if (Array.isArray(x)) {
        x.forEach(walk);
        return;
      }
      if (typeof x !== "object") return;
      var id = "";
      var name = "";
      idKeys.some(function (k) {
        if (x[k] !== undefined && x[k] !== null && String(x[k]).trim()) {
          id = String(x[k]).trim();
          return true;
        }
        return false;
      });
      nameKeys.some(function (k) {
        if (x[k] !== undefined && x[k] !== null && String(x[k]).trim()) {
          name = String(x[k]).trim();
          return true;
        }
        return false;
      });
      if (id && !seen[id]) {
        seen[id] = true;
        out.push({ id: id, name: name || id, raw: x });
      }
      Object.keys(x).forEach(function (k) {
        walk(x[k]);
      });
    }
    walk(data);
    return out.slice(0, 80);
  }

  function formatTs(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1000).toLocaleString("ru-RU");
    } catch (e) {
      return String(ts);
    }
  }

  function assigneeOptions(selectedId) {
    var html = '<option value="">—</option>';
    meta.assignees.forEach(function (a) {
      var sel = String(selectedId || "") === String(a.id) ? " selected" : "";
      html += '<option value="' + esc(a.id) + '"' + sel + ">" + esc(a.display_name) + "</option>";
    });
    return html;
  }

  function statusOptions(selectedId) {
    var html = "";
    meta.statuses.forEach(function (s) {
      var sel = String(selectedId || "") === String(s.id) ? " selected" : "";
      html += '<option value="' + esc(s.id) + '"' + sel + ">" + esc(s.name) + "</option>";
    });
    return html;
  }

  function itemTotalsMap(items) {
    var out = {};
    (items || []).forEach(function (it) {
      out[it.sku] = (out[it.sku] || 0) + Number(it.quantity || 0);
    });
    return out;
  }

  function renderSupplyItemsTable() {
    if (!itemDraft.length) return '<p class="wh-msg">Товары не выбраны.</p>';
    var rows = itemDraft
      .map(function (it, idx) {
        return (
          '<tr data-index="' + idx + '">' +
          "<td>" + esc(it.name || "—") + "</td>" +
          "<td>" + esc(it.sku) + "</td>" +
          '<td><input type="number" min="1" class="wh-fbo-item-qty" value="' +
          esc(it.quantity || 1) +
          '" /></td>' +
          '<td><button type="button" class="wh-btn wh-btn-sm wh-fbo-item-remove">Удалить</button></td></tr>'
        );
      })
      .join("");
    return (
      '<table class="wh-employees-table wh-crm-table wh-fbo-items-table"><thead><tr>' +
      "<th>Товар</th><th>Артикул</th><th>Кол-во</th><th></th></tr></thead><tbody>" +
      rows +
      "</tbody></table>"
    );
  }

  function refreshSupplyItems(root) {
    var box = root.querySelector("#whFboItems");
    if (!box) return;
    box.innerHTML = renderSupplyItemsTable();
    bindSupplyItemEvents(root);
  }

  function bindSupplyItemEvents(root) {
    root.querySelectorAll(".wh-fbo-item-qty").forEach(function (inp) {
      inp.addEventListener("change", function () {
        var row = inp.closest("tr[data-index]");
        var idx = parseInt(row.getAttribute("data-index"), 10);
        itemDraft[idx].quantity = Math.max(1, parseInt(inp.value || "1", 10) || 1);
      });
    });
    root.querySelectorAll(".wh-fbo-item-remove").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var idx = parseInt(btn.closest("tr[data-index]").getAttribute("data-index"), 10);
        itemDraft.splice(idx, 1);
        refreshSupplyItems(root);
      });
    });
  }

  function searchProducts(root) {
    var q = root.querySelector("#whFboProductSearch").value.trim();
    var out = root.querySelector("#whFboProductResults");
    if (!q) {
      out.innerHTML = '<p class="wh-msg">Введите название, артикул или код товара.</p>';
      return;
    }
    out.innerHTML = '<p class="wh-msg">Поиск…</p>';
    fetchJson("/api/warehouse/catalog/products/picker?q=" + encodeURIComponent(q))
      .then(function (data) {
        var products = data.products || [];
        if (!products.length) {
          out.innerHTML = '<p class="wh-msg">Ничего не найдено.</p>';
          return;
        }
        out.innerHTML = products
          .slice(0, 30)
          .map(function (p) {
            return (
              '<button type="button" class="wh-btn wh-btn-sm wh-fbo-product-pick" data-id="' +
              esc(p.id) +
              '" data-sku="' +
              esc(p.sku) +
              '" data-name="' +
              esc(p.name) +
              '">' +
              esc(p.name) +
              " · " +
              esc(p.sku) +
              "</button>"
            );
          })
          .join(" ");
      })
      .catch(function (err) {
        out.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function renderManagerList(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    var q = managerFilters.q ? "?q=" + encodeURIComponent(managerFilters.q) : "";
    fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies" + q)
      .then(function (data) {
        var rows = (data.supplies || [])
          .map(function (s) {
            return (
              '<tr data-id="' + esc(s.id) + '">' +
              "<td>#" + esc(s.id) + "</td>" +
              "<td>" + esc(s.title) + "</td>" +
              "<td>" + esc(s.supply_kind_label || kindName(s.supply_kind)) + "</td>" +
              "<td>" + esc(s.status_label || statusName(s.status)) + "</td>" +
              "<td>" + esc(s.ozon_cluster_name || s.ozon_cluster_id || "—") + "</td>" +
              "<td>" + esc(s.assigned_user_name || "—") + "</td>" +
              "<td>" + esc(formatTs(s.updated_at_ts)) + "</td></tr>"
            );
          })
          .join("");
        root.innerHTML =
          '<div class="wh-crm-toolbar">' +
          '<input type="search" id="whFboSearch" class="wh-crm-search" placeholder="Поиск по заявкам…" value="' +
          esc(managerFilters.q || "") +
          '" />' +
          '<button type="button" class="wh-btn" id="whFboRefresh">Обновить</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whFboCreate">+ FBO-заявка</button>' +
          "</div>" +
          (rows
            ? '<table class="wh-employees-table wh-crm-table"><thead><tr><th>№</th><th>Название</th><th>Тип</th><th>Этап</th><th>Кластер/склад</th><th>Упаковщик</th><th>Обновлено</th></tr></thead><tbody>' +
              rows +
              "</tbody></table>"
            : '<p class="wh-msg">FBO-заявки не найдены.</p>');
        bindManagerListEvents(tab, item, root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function bindManagerListEvents(tab, item, root) {
    root.querySelector("#whFboCreate").addEventListener("click", function () {
      renderSupplyForm(tab, item, null);
    });
    root.querySelector("#whFboRefresh").addEventListener("click", function () {
      renderManagerList(tab, item);
    });
    root.querySelector("#whFboSearch").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        managerFilters.q = e.target.value.trim();
        renderManagerList(tab, item);
      }
    });
    root.querySelectorAll("tbody tr[data-id]").forEach(function (tr) {
      tr.style.cursor = "pointer";
      tr.addEventListener("click", function () {
        renderSupplyForm(tab, item, parseInt(tr.getAttribute("data-id"), 10));
      });
    });
  }

  function renderSupplyForm(tab, item, supplyId) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    var load = supplyId
      ? fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId).then(function (d) {
          return d.supply;
        })
      : Promise.resolve({
          title: "",
          supply_kind: "pallet",
          status: "draft",
          assigned_user_id: "",
          items: [],
        });
    load
      .then(function (s) {
        itemDraft = (s.items || []).map(function (it) {
          return {
            product_id: it.product_id,
            sku: it.sku,
            name: it.name,
            quantity: it.quantity,
          };
        });
        root.innerHTML =
          '<div class="wh-crm-form-toolbar">' +
          '<button type="button" class="wh-btn" id="whFboBack">&larr; К списку</button>' +
          (supplyId ? '<button type="button" class="wh-btn wh-btn-danger" id="whFboDelete">Удалить</button>' : "") +
          (supplyId ? '<button type="button" class="wh-btn" id="whFboCreateDraft">Создать черновик в Ozon</button>' : "") +
          (supplyId ? '<button type="button" class="wh-btn" id="whFboCreateSupply">Создать поставку в Ozon</button>' : "") +
          '<button type="button" class="wh-btn wh-btn-primary" id="whFboSave">Сохранить</button></div>' +
          '<p class="wh-msg" id="whFboMsg"></p>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Заявка</h4>' +
          '<div class="wh-form-row">' +
          '<div><label>Название</label><input type="text" id="whFboTitle" value="' +
          esc(s.title || "") +
          '" placeholder="Например, Ozon FBO июнь" /></div>' +
          '<div><label>Тип сборки</label><select id="whFboKind">' +
          '<option value="pallet"' +
          (s.supply_kind === "pallet" ? " selected" : "") +
          ">Паллеты</option>" +
          '<option value="box"' +
          (s.supply_kind === "box" ? " selected" : "") +
          ">Короба</option></select></div>" +
          '<div><label>Этап</label><select id="whFboStatus">' +
          statusOptions(s.status || "draft") +
          "</select></div>" +
          '<div><label>Упаковщик</label><select id="whFboAssignee">' +
          assigneeOptions(s.assigned_user_id) +
          "</select></div>" +
          '<div><label>ID черновика Ozon</label><input type="text" id="whFboDraftId" value="' +
          esc(s.ozon_draft_id || "") +
          '" /></div>' +
          '<div><label>ID поставки Ozon</label><input type="text" id="whFboSupplyId" value="' +
          esc(s.ozon_supply_id || "") +
          '" /></div>' +
          '<div><label>timeslot_id</label><input type="text" id="whFboTimeslotId" placeholder="Для создания поставки из черновика" /></div>' +
          '<div><label>ID склада Ozon</label><input type="text" id="whFboWarehouseId" value="' +
          esc(s.ozon_warehouse_id || "") +
          '" /></div>' +
          '<div><label>Склад Ozon</label><input type="text" id="whFboWarehouseName" value="' +
          esc(s.ozon_warehouse_name || "") +
          '" /></div>' +
          '<input type="hidden" id="whFboClusterId" value="' +
          esc(s.ozon_cluster_id || "") +
          '" />' +
          '<input type="hidden" id="whFboClusterName" value="' +
          esc(s.ozon_cluster_name || "") +
          '" />' +
          '<div><label>Кластер Ozon</label><select id="whFboClusterSelect">' +
          (s.ozon_cluster_id
            ? '<option value="' +
              esc(s.ozon_cluster_id) +
              '" data-name="' +
              esc(s.ozon_cluster_name || s.ozon_cluster_id) +
              '" selected>' +
              esc(s.ozon_cluster_name || s.ozon_cluster_id) +
              " · " +
              esc(s.ozon_cluster_id) +
              "</option>"
            : '<option value="">Нажмите «Загрузить кластеры Ozon»</option>') +
          "</select></div>" +
          "</div>" +
          '<div class="wh-form-actions">' +
          '<button type="button" class="wh-btn wh-btn-sm" id="whFboLoadWarehouses">Загрузить склады Ozon</button>' +
          '<button type="button" class="wh-btn wh-btn-sm" id="whFboLoadClusters">Загрузить кластеры Ozon</button>' +
          '<button type="button" class="wh-btn wh-btn-sm" id="whFboLoadTimeslots">Загрузить таймслоты</button>' +
          "</div>" +
          '<div id="whFboOzonResults" class="wh-fbo-product-results"></div>' +
          "</section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Товары заявки</h4>' +
          '<div class="wh-rc-search-grid"><div><label>Поиск товара</label><input type="text" id="whFboProductSearch" placeholder="Название, артикул, код" /></div>' +
          '<div class="wh-rc-search-btn-wrap"><button type="button" class="wh-btn" id="whFboProductSearchBtn">Найти</button></div></div>' +
          '<div id="whFboProductResults" class="wh-fbo-product-results"></div>' +
          '<div id="whFboItems">' +
          renderSupplyItemsTable() +
          "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Комментарий</h4>' +
          '<textarea id="whFboComment" class="wh-rc-comment" rows="3">' +
          esc(s.comment || "") +
          "</textarea></section>";
        bindSupplyFormEvents(tab, item, root, supplyId);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function bindSupplyFormEvents(tab, item, root, supplyId) {
    bindSupplyItemEvents(root);
    root.querySelector("#whFboBack").addEventListener("click", function () {
      renderManagerList(tab, item);
    });
    root.querySelector("#whFboProductSearchBtn").addEventListener("click", function () {
      searchProducts(root);
    });
    root.querySelector("#whFboLoadWarehouses").addEventListener("click", function () {
      loadOzonDirectory(root, "warehouses");
    });
    root.querySelector("#whFboLoadClusters").addEventListener("click", function () {
      loadOzonDirectory(root, "clusters");
    });
    root.querySelector("#whFboLoadTimeslots").addEventListener("click", function () {
      loadTimeslots(root);
    });
    root.querySelector("#whFboOzonResults").addEventListener("click", function (e) {
      var btn = e.target.closest(".wh-fbo-ozon-pick");
      if (!btn) return;
      var type = btn.getAttribute("data-type");
      if (type === "warehouse") {
        root.querySelector("#whFboWarehouseId").value = btn.getAttribute("data-id") || "";
        root.querySelector("#whFboWarehouseName").value = btn.getAttribute("data-name") || "";
      } else if (type === "timeslot") {
        root.querySelector("#whFboTimeslotId").value = btn.getAttribute("data-id") || "";
      } else {
        root.querySelector("#whFboClusterId").value = btn.getAttribute("data-id") || "";
        root.querySelector("#whFboClusterName").value = btn.getAttribute("data-name") || "";
        var sel = root.querySelector("#whFboClusterSelect");
        if (sel) {
          sel.innerHTML =
            '<option value="' +
            esc(btn.getAttribute("data-id") || "") +
            '" data-name="' +
            esc(btn.getAttribute("data-name") || "") +
            '" selected>' +
            esc(btn.getAttribute("data-name") || "") +
            " · " +
            esc(btn.getAttribute("data-id") || "") +
            "</option>";
        }
      }
    });
    root.querySelector("#whFboClusterSelect").addEventListener("change", function () {
      var opt = this.options[this.selectedIndex];
      root.querySelector("#whFboClusterId").value = this.value || "";
      root.querySelector("#whFboClusterName").value = opt ? opt.getAttribute("data-name") || "" : "";
    });
    root.querySelector("#whFboProductSearch").addEventListener("keydown", function (e) {
      if (e.key === "Enter") searchProducts(root);
    });
    root.querySelector("#whFboProductResults").addEventListener("click", function (e) {
      var btn = e.target.closest(".wh-fbo-product-pick");
      if (!btn) return;
      var productId = parseInt(btn.getAttribute("data-id"), 10);
      var sku = btn.getAttribute("data-sku") || "";
      for (var i = 0; i < itemDraft.length; i++) {
        if (itemDraft[i].sku === sku) {
          itemDraft[i].quantity += 1;
          refreshSupplyItems(root);
          return;
        }
      }
      itemDraft.push({
        product_id: productId,
        sku: sku,
        name: btn.getAttribute("data-name") || "",
        quantity: 1,
      });
      refreshSupplyItems(root);
    });
    root.querySelector("#whFboSave").addEventListener("click", function () {
      saveSupply(tab, item, root, supplyId);
    });
    var createDraft = root.querySelector("#whFboCreateDraft");
    if (createDraft) {
      createDraft.addEventListener("click", function () {
        createOzonDraft(tab, item, root, supplyId);
      });
    }
    var createSupply = root.querySelector("#whFboCreateSupply");
    if (createSupply) {
      createSupply.addEventListener("click", function () {
        createOzonSupply(tab, item, root, supplyId);
      });
    }
    var del = root.querySelector("#whFboDelete");
    if (del) {
      del.addEventListener("click", function () {
        if (!confirm("Удалить FBO-заявку?")) return;
        fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId, { method: "DELETE" }).then(function () {
          renderManagerList(tab, item);
        });
      });
    }
  }

  function loadOzonDirectory(root, type) {
    var out = root.querySelector("#whFboOzonResults");
    out.innerHTML = '<p class="wh-msg">Загрузка из Ozon…</p>';
    var url =
      type === "warehouses"
        ? "/api/warehouse/marketplaces/ozon-fbo/ozon/warehouses"
        : "/api/warehouse/marketplaces/ozon-fbo/ozon/clusters";
    fetchJson(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    })
      .then(function (resp) {
        var rows =
          type === "warehouses"
            ? collectOzonRows(resp.data, ["warehouse_id", "id"], ["warehouse_name", "name", "address"])
            : collectOzonRows(resp.data, ["cluster_id", "id"], ["cluster_name", "name"]);
        if (!rows.length) {
          out.innerHTML =
            '<details class="wh-fbo-raw"><summary>Ozon вернул данные, но список не распознан</summary><pre>' +
            esc(apiDetail(resp.data)) +
            "</pre></details>";
          return;
        }
        if (type === "clusters") {
          var sel = root.querySelector("#whFboClusterSelect");
          var current = root.querySelector("#whFboClusterId").value;
          sel.innerHTML =
            '<option value="">Выберите кластер</option>' +
            rows
              .map(function (r) {
                var selected = String(current || "") === String(r.id) ? " selected" : "";
                return (
                  '<option value="' +
                  esc(r.id) +
                  '" data-name="' +
                  esc(r.name) +
                  '"' +
                  selected +
                  ">" +
                  esc(r.name) +
                  " · " +
                  esc(r.id) +
                  "</option>"
                );
              })
              .join("");
          out.innerHTML = '<p class="wh-msg wh-msg-ok">Кластеры загружены. Выберите кластер в списке.</p>';
          return;
        }
        out.innerHTML = rows
          .map(function (r) {
            return (
              '<button type="button" class="wh-btn wh-btn-sm wh-fbo-ozon-pick" data-type="warehouse" data-id="' +
              esc(r.id) +
              '" data-name="' +
              esc(r.name) +
              '">' +
              esc(r.name) +
              " · " +
              esc(r.id) +
              "</button>"
            );
          })
          .join(" ");
      })
      .catch(function (err) {
        out.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function isoDayOffset(days) {
    var d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  }

  function loadTimeslots(root) {
    var out = root.querySelector("#whFboOzonResults");
    var warehouseId = root.querySelector("#whFboWarehouseId").value.trim();
    if (!warehouseId) {
      out.innerHTML = '<p class="wh-msg wh-msg-error">Сначала выберите или укажите склад Ozon.</p>';
      return;
    }
    out.innerHTML = '<p class="wh-msg">Загрузка таймслотов…</p>';
    fetchJson("/api/warehouse/marketplaces/ozon-fbo/ozon/timeslots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        warehouse_id: /^\d+$/.test(warehouseId) ? parseInt(warehouseId, 10) : warehouseId,
        date_from: isoDayOffset(0),
        date_to: isoDayOffset(21),
      }),
    })
      .then(function (resp) {
        var rows = collectOzonRows(resp.data, ["timeslot_id", "id"], ["from", "date_from", "date", "time_from"]);
        if (!rows.length) {
          out.innerHTML =
            '<details open class="wh-fbo-raw"><summary>Таймслоты Ozon</summary><pre>' +
            esc(apiDetail(resp.data)) +
            "</pre></details>";
          return;
        }
        out.innerHTML = rows
          .map(function (r) {
            return (
              '<button type="button" class="wh-btn wh-btn-sm wh-fbo-ozon-pick" data-type="timeslot" data-id="' +
              esc(r.id) +
              '">' +
              esc(r.name) +
              " · " +
              esc(r.id) +
              "</button>"
            );
          })
          .join(" ");
      })
      .catch(function (err) {
        out.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function createOzonDraft(tab, item, root, supplyId) {
    var msg = root.querySelector("#whFboMsg");
    setMsg(msg, "Сначала сохраняю заявку…", false);
    saveSupply(tab, item, root, supplyId, { stay: true })
      .then(function () {
        setMsg(msg, "Создаю черновик в Ozon…", false);
        return fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/ozon/draft", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
      })
      .then(function (resp) {
        if (resp.draft_id) root.querySelector("#whFboDraftId").value = resp.draft_id;
        setMsg(
          msg,
          "Ozon принял запрос на черновик" + (resp.draft_id ? ": " + resp.draft_id : ". Проверьте ответ ниже."),
          false
        );
        var out = root.querySelector("#whFboOzonResults");
        if (out) {
          out.innerHTML =
            '<details open class="wh-fbo-raw"><summary>Ответ Ozon</summary><pre>' +
            esc(apiDetail(resp.data)) +
            "</pre></details>";
        }
      })
      .catch(function (err) {
        setMsg(msg, err.message || "Ошибка создания черновика Ozon", true);
      });
  }

  function createOzonSupply(tab, item, root, supplyId) {
    var msg = root.querySelector("#whFboMsg");
    setMsg(msg, "Сначала сохраняю заявку…", false);
    saveSupply(tab, item, root, supplyId, { stay: true })
      .then(function () {
        var timeslotId = root.querySelector("#whFboTimeslotId").value.trim();
        setMsg(msg, "Создаю поставку в Ozon…", false);
        return fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/ozon/supply", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ timeslot_id: timeslotId }),
        });
      })
      .then(function (resp) {
        if (resp.ozon_supply_id) root.querySelector("#whFboSupplyId").value = resp.ozon_supply_id;
        setMsg(
          msg,
          "Ozon принял запрос на создание поставки" +
            (resp.ozon_supply_id ? ": " + resp.ozon_supply_id : ". Проверьте ответ ниже."),
          false
        );
        var out = root.querySelector("#whFboOzonResults");
        if (out) {
          out.innerHTML =
            '<details open class="wh-fbo-raw"><summary>Ответ Ozon</summary><pre>' +
            esc(apiDetail(resp.data)) +
            "</pre></details>";
        }
      })
      .catch(function (err) {
        setMsg(msg, err.message || "Ошибка создания поставки Ozon", true);
      });
  }

  function saveSupply(tab, item, root, supplyId, opts) {
    opts = opts || {};
    var msg = root.querySelector("#whFboMsg");
    msg.textContent = "";
    msg.className = "wh-msg";
    var body = {
      title: root.querySelector("#whFboTitle").value.trim(),
      supply_kind: root.querySelector("#whFboKind").value,
      status: root.querySelector("#whFboStatus").value,
      assigned_user_id: root.querySelector("#whFboAssignee").value || null,
      ozon_draft_id: root.querySelector("#whFboDraftId").value.trim(),
      ozon_supply_id: root.querySelector("#whFboSupplyId").value.trim(),
      ozon_warehouse_id: root.querySelector("#whFboWarehouseId").value.trim(),
      ozon_warehouse_name: root.querySelector("#whFboWarehouseName").value.trim(),
      ozon_cluster_id: root.querySelector("#whFboClusterId").value.trim(),
      ozon_cluster_name: root.querySelector("#whFboClusterName").value.trim(),
      comment: root.querySelector("#whFboComment").value.trim(),
      items: itemDraft,
    };
    var req = supplyId
      ? fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      : fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
    return req
      .then(function (data) {
        msg.className = "wh-msg wh-msg-ok";
        msg.textContent = "Сохранено.";
        if (!supplyId && !opts.stay) renderSupplyForm(tab, item, data.supply.id);
        return data;
      })
      .catch(function (err) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Ошибка сохранения";
        throw err;
      });
  }

  function renderPackingList(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    fetchJson("/api/warehouse/marketplaces/ozon-fbo/my-supplies")
      .then(function (data) {
        var rows = (data.supplies || [])
          .map(function (s) {
            return (
              '<tr data-id="' + esc(s.id) + '">' +
              "<td>#" + esc(s.id) + "</td>" +
              "<td>" + esc(s.title) + "</td>" +
              "<td>" + esc(s.supply_kind_label || kindName(s.supply_kind)) + "</td>" +
              "<td>" + esc(s.status_label || statusName(s.status)) + "</td>" +
              "<td>" + esc(s.cargo_count || 0) + "</td></tr>"
            );
          })
          .join("");
        root.innerHTML =
          '<div class="wh-crm-toolbar"><button type="button" class="wh-btn" id="whFboPackRefresh">Обновить</button></div>' +
          (rows
            ? '<table class="wh-employees-table wh-crm-table"><thead><tr><th>№</th><th>Название</th><th>Тип</th><th>Этап</th><th>ГМ</th></tr></thead><tbody>' +
              rows +
              "</tbody></table>"
            : '<p class="wh-msg">Назначенных FBO-заданий нет.</p>');
        root.querySelector("#whFboPackRefresh").addEventListener("click", function () {
          renderPackingList(tab, item);
        });
        root.querySelectorAll("tbody tr[data-id]").forEach(function (tr) {
          tr.style.cursor = "pointer";
          tr.addEventListener("click", function () {
            renderPackingForm(tab, item, parseInt(tr.getAttribute("data-id"), 10));
          });
        });
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function renderPackingForm(tab, item, supplyId) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId)
      .then(function (data) {
        currentSupply = data.supply;
        cargoDraft = (currentSupply.cargoes || []).map(function (c) {
          return {
            cargo_number: c.cargo_number,
            comment: c.comment || "",
            items: (c.items || []).map(function (i) {
              return {
                product_id: i.product_id,
                sku: i.sku,
                name: i.name,
                quantity: i.quantity,
                expiration_date: i.expiration_date || "",
              };
            }),
          };
        });
        root.innerHTML =
          '<div class="wh-crm-form-toolbar">' +
          '<button type="button" class="wh-btn" id="whFboPackBack">&larr; К заданиям</button>' +
          '<button type="button" class="wh-btn" id="whFboAddCargo">+ Грузоместо</button>' +
          '<button type="button" class="wh-btn" id="whFboCargoRules">Правила Ozon</button>' +
          '<button type="button" class="wh-btn" id="whFboSendCargoes">Отправить ГМ в Ozon</button>' +
          '<button type="button" class="wh-btn" id="whFboCargoStatus">Статус ГМ</button>' +
          '<button type="button" class="wh-btn" id="whFboMakeLabels">Этикетки</button>' +
          '<button type="button" class="wh-btn" id="whFboLabelStatus">Статус этикеток</button>' +
          (currentSupply.labels_file_guid
            ? '<a class="wh-btn" href="/api/warehouse/marketplaces/ozon-fbo/supplies/' +
              esc(supplyId) +
              '/labels.pdf" target="_blank">Скачать PDF</a>'
            : "") +
          '<button type="button" class="wh-btn wh-btn-primary" id="whFboSaveCargoes">Сохранить</button></div>' +
          '<p class="wh-msg" id="whFboPackMsg"></p>' +
          '<div id="whFboPackOzonResult"></div>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">' +
          esc(currentSupply.title) +
          "</h4>" +
          '<p class="wh-muted">Тип сборки: ' +
          esc(currentSupply.supply_kind_label || kindName(currentSupply.supply_kind)) +
          ". Одно грузоместо = " +
          (currentSupply.supply_kind === "box" ? "один короб." : "одна паллета.") +
          "</p>" +
          renderSupplyPlan(currentSupply.items || []) +
          "</section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Грузоместа</h4><div id="whFboCargoes">' +
          renderCargoes() +
          "</div></section>";
        bindPackingEvents(tab, item, root, supplyId);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function renderSupplyPlan(items) {
    var rows = (items || [])
      .map(function (it) {
        return "<tr><td>" + esc(it.name || "—") + "</td><td>" + esc(it.sku) + "</td><td>" + esc(it.quantity) + "</td></tr>";
      })
      .join("");
    return rows
      ? '<table class="wh-employees-table wh-crm-table wh-fbo-plan"><thead><tr><th>Товар</th><th>SKU</th><th>Всего</th></tr></thead><tbody>' +
          rows +
          "</tbody></table>"
      : '<p class="wh-msg">В заявке нет товаров.</p>';
  }

  function productSelectHtml(selectedSku) {
    var opts = '<option value="">Товар</option>';
    (currentSupply.items || []).forEach(function (it) {
      var sel = selectedSku === it.sku ? " selected" : "";
      opts +=
        '<option value="' +
        esc(it.sku) +
        '" data-product-id="' +
        esc(it.product_id || "") +
        '" data-name="' +
        esc(it.name) +
        '"' +
        sel +
        ">" +
        esc(it.name || it.sku) +
        " · " +
        esc(it.sku) +
        "</option>";
    });
    return opts;
  }

  function renderCargoes() {
    if (!cargoDraft.length) return '<p class="wh-msg">Грузоместа ещё не добавлены.</p>';
    return cargoDraft
      .map(function (cargo, cIdx) {
        var rows = (cargo.items || [])
          .map(function (it, iIdx) {
            return (
              '<tr data-cargo="' + cIdx + '" data-item="' + iIdx + '">' +
              '<td><select class="wh-fbo-cargo-item-sku">' +
              productSelectHtml(it.sku) +
              "</select></td>" +
              '<td><input type="number" min="1" class="wh-fbo-cargo-item-qty" value="' +
              esc(it.quantity || 1) +
              '" /></td>' +
              '<td><input type="date" class="wh-fbo-cargo-item-exp" value="' +
              esc(it.expiration_date || "") +
              '" /></td>' +
              '<td><button type="button" class="wh-btn wh-btn-sm wh-fbo-cargo-item-remove">Удалить</button></td></tr>'
            );
          })
          .join("");
        return (
          '<div class="wh-fbo-cargo" data-cargo="' + cIdx + '">' +
          '<div class="wh-fbo-cargo-head">' +
          '<input type="text" class="wh-fbo-cargo-number" value="' +
          esc(cargo.cargo_number || cIdx + 1) +
          '" />' +
          '<button type="button" class="wh-btn wh-btn-sm wh-fbo-add-cargo-item">+ Товар</button>' +
          '<button type="button" class="wh-btn wh-btn-sm wh-btn-danger wh-fbo-cargo-remove">Удалить ГМ</button></div>' +
          '<table class="wh-employees-table wh-crm-table"><thead><tr><th>Товар</th><th>Кол-во</th><th>Срок годности</th><th></th></tr></thead><tbody>' +
          rows +
          "</tbody></table></div>"
        );
      })
      .join("");
  }

  function refreshCargoes(root) {
    root.querySelector("#whFboCargoes").innerHTML = renderCargoes();
    bindCargoEvents(root);
  }

  function bindPackingEvents(tab, item, root, supplyId) {
    root.querySelector("#whFboPackBack").addEventListener("click", function () {
      renderPackingList(tab, item);
    });
    root.querySelector("#whFboAddCargo").addEventListener("click", function () {
      cargoDraft.push({ cargo_number: String(cargoDraft.length + 1), items: [] });
      refreshCargoes(root);
    });
    root.querySelector("#whFboSaveCargoes").addEventListener("click", function () {
      saveCargoes(root, supplyId);
    });
    root.querySelector("#whFboCargoRules").addEventListener("click", function () {
      checkCargoRules(root, supplyId);
    });
    root.querySelector("#whFboSendCargoes").addEventListener("click", function () {
      sendCargoesToOzon(root, supplyId);
    });
    root.querySelector("#whFboCargoStatus").addEventListener("click", function () {
      checkCargoStatus(root, supplyId);
    });
    root.querySelector("#whFboMakeLabels").addEventListener("click", function () {
      makeLabels(root, supplyId);
    });
    root.querySelector("#whFboLabelStatus").addEventListener("click", function () {
      checkLabelStatus(root, supplyId);
    });
    bindCargoEvents(root);
  }

  function showPackOzonResult(root, title, data) {
    var out = root.querySelector("#whFboPackOzonResult");
    if (!out) return;
    out.innerHTML =
      '<details open class="wh-fbo-raw"><summary>' +
      esc(title) +
      "</summary><pre>" +
      esc(apiDetail(data)) +
      "</pre></details>";
  }

  function bindCargoEvents(root) {
    root.querySelectorAll(".wh-fbo-cargo-number").forEach(function (inp) {
      inp.addEventListener("change", function () {
        var cIdx = parseInt(inp.closest(".wh-fbo-cargo").getAttribute("data-cargo"), 10);
        cargoDraft[cIdx].cargo_number = inp.value.trim();
      });
    });
    root.querySelectorAll(".wh-fbo-add-cargo-item").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var cIdx = parseInt(btn.closest(".wh-fbo-cargo").getAttribute("data-cargo"), 10);
        cargoDraft[cIdx].items.push({ sku: "", quantity: 1, expiration_date: "" });
        refreshCargoes(root);
      });
    });
    root.querySelectorAll(".wh-fbo-cargo-remove").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var cIdx = parseInt(btn.closest(".wh-fbo-cargo").getAttribute("data-cargo"), 10);
        cargoDraft.splice(cIdx, 1);
        refreshCargoes(root);
      });
    });
    root.querySelectorAll(".wh-fbo-cargo-item-remove").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var tr = btn.closest("tr[data-cargo]");
        var cIdx = parseInt(tr.getAttribute("data-cargo"), 10);
        var iIdx = parseInt(tr.getAttribute("data-item"), 10);
        cargoDraft[cIdx].items.splice(iIdx, 1);
        refreshCargoes(root);
      });
    });
    root.querySelectorAll(".wh-fbo-cargo-item-sku, .wh-fbo-cargo-item-qty, .wh-fbo-cargo-item-exp").forEach(function (el) {
      el.addEventListener("change", function () {
        var tr = el.closest("tr[data-cargo]");
        var cIdx = parseInt(tr.getAttribute("data-cargo"), 10);
        var iIdx = parseInt(tr.getAttribute("data-item"), 10);
        var sel = tr.querySelector(".wh-fbo-cargo-item-sku");
        var opt = sel.options[sel.selectedIndex];
        cargoDraft[cIdx].items[iIdx] = {
          product_id: opt ? parseInt(opt.getAttribute("data-product-id") || "0", 10) || null : null,
          sku: sel.value,
          name: opt ? opt.getAttribute("data-name") || "" : "",
          quantity: Math.max(1, parseInt(tr.querySelector(".wh-fbo-cargo-item-qty").value || "1", 10) || 1),
          expiration_date: tr.querySelector(".wh-fbo-cargo-item-exp").value,
        };
      });
    });
  }

  function saveCargoes(root, supplyId) {
    var msg = root.querySelector("#whFboPackMsg");
    msg.textContent = "";
    msg.className = "wh-msg";
    return fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/cargoes", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cargoes: cargoDraft }),
    })
      .then(function () {
        msg.className = "wh-msg wh-msg-ok";
        msg.textContent = "Грузоместа сохранены.";
      })
      .catch(function (err) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Ошибка сохранения";
        throw err;
      });
  }

  function sendCargoesToOzon(root, supplyId) {
    var msg = root.querySelector("#whFboPackMsg");
    setMsg(msg, "Сохраняю грузоместа…", false);
    saveCargoes(root, supplyId)
      .then(function () {
        setMsg(msg, "Отправляю грузоместа в Ozon…", false);
        return fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/ozon/cargoes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
      })
      .then(function (resp) {
        setMsg(msg, "Грузоместа отправлены в Ozon" + (resp.operation_id ? ": " + resp.operation_id : ""), false);
        showPackOzonResult(root, "Ответ Ozon по грузоместам", resp.data);
      })
      .catch(function (err) {
        setMsg(msg, err.message || "Ошибка отправки грузомест", true);
      });
  }

  function makeLabels(root, supplyId) {
    var msg = root.querySelector("#whFboPackMsg");
    setMsg(msg, "Запрашиваю этикетки Ozon…", false);
    fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/ozon/labels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    })
      .then(function (resp) {
        if (resp.file_guid) {
          setMsg(msg, "Этикетки готовы. Обновите карточку, чтобы появилась кнопка скачивания.", false);
        } else {
          setMsg(msg, "Генерация этикеток запущена. Если PDF не появился, проверьте статус позже.", false);
        }
        showPackOzonResult(root, "Ответ Ozon по этикеткам", resp.data);
      })
      .catch(function (err) {
        setMsg(msg, err.message || "Ошибка генерации этикеток", true);
      });
  }

  function checkCargoRules(root, supplyId) {
    var msg = root.querySelector("#whFboPackMsg");
    setMsg(msg, "Проверяю правила Ozon…", false);
    fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/ozon/cargoes/rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    })
      .then(function (resp) {
        setMsg(msg, "Правила Ozon получены.", false);
        showPackOzonResult(root, "Правила Ozon", resp.data);
      })
      .catch(function (err) {
        setMsg(msg, err.message || "Ошибка проверки правил", true);
      });
  }

  function checkCargoStatus(root, supplyId) {
    var msg = root.querySelector("#whFboPackMsg");
    setMsg(msg, "Проверяю статус грузомест…", false);
    fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/ozon/cargoes/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    })
      .then(function (resp) {
        setMsg(msg, "Статус грузомест получен.", false);
        showPackOzonResult(root, "Статус грузомест Ozon", resp.data);
      })
      .catch(function (err) {
        setMsg(msg, err.message || "Ошибка проверки статуса грузомест", true);
      });
  }

  function checkLabelStatus(root, supplyId) {
    var msg = root.querySelector("#whFboPackMsg");
    setMsg(msg, "Проверяю статус этикеток…", false);
    fetchJson("/api/warehouse/marketplaces/ozon-fbo/supplies/" + supplyId + "/ozon/labels/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    })
      .then(function (resp) {
        setMsg(
          msg,
          resp.file_guid
            ? "Этикетки готовы. Обновите карточку, чтобы появилась кнопка скачивания."
            : "Статус этикеток получен.",
          false
        );
        showPackOzonResult(root, "Статус этикеток Ozon", resp.data);
      })
      .catch(function (err) {
        setMsg(msg, err.message || "Ошибка проверки статуса этикеток", true);
      });
  }

  function render(tab, item) {
    loadMeta()
      .then(function () {
        if (item.id === "ozon-fbo-packing") {
          renderPackingList(tab, item);
        } else {
          renderManagerList(tab, item);
        }
      })
      .catch(function (err) {
        preparePanel(tab, item);
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  global.WhOzonFbo = { render: render };
})(window);
