(function (global) {
  var ordersMeta = { counterparties: [], statuses: [], price_types: [], order_kinds: [], warehouses: [] };
  var invMeta = { warehouses: [] };

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

  function pager() {
    return global.WH_PAGER;
  }

  function preparePanel(tab, item) {
    shell().contentTitleEl.textContent = item.title;
    shell().contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    shell().contentPlaceholderEl.hidden = true;
    panelEl().hidden = false;
    var card = document.querySelector(".wh-content-card");
    if (card) card.classList.add("wh-content-card--wide");
  }

  function formatTs(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1000).toLocaleString("ru-RU");
    } catch (e) {
      return String(ts);
    }
  }

  function formatDate(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1000).toLocaleDateString("ru-RU");
    } catch (e) {
      return String(ts);
    }
  }

  function statusLabel(id) {
    for (var i = 0; i < ordersMeta.statuses.length; i++) {
      if (ordersMeta.statuses[i].id === id) return ordersMeta.statuses[i].name;
    }
    return id || "—";
  }

  function formatMoney(n) {
    if (n == null || n === "") return "—";
    var num = Number(n);
    if (!isFinite(num)) return "—";
    return num.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ₽";
  }

  function parseMoneyInput(s) {
    var v = String(s || "").trim().replace(/\s/g, "").replace(",", ".");
    if (!v) return null;
    var n = parseFloat(v);
    return isNaN(n) ? null : n;
  }

  function moneyInputValue(n) {
    if (n == null || n === "") return "";
    var num = Number(n);
    if (!isFinite(num)) return "";
    return num.toFixed(2);
  }

  function round2(n) {
    return Math.round(n * 100) / 100;
  }

  function splitVatClient(gross, rate) {
    if (gross == null || !isFinite(gross)) return { net: "", vat: "" };
    var r = Number(rate);
    if (!isFinite(r) || r <= 0) return { net: moneyInputValue(gross), vat: moneyInputValue(0) };
    var net = round2(gross / (1 + r / 100));
    return { net: moneyInputValue(net), vat: moneyInputValue(round2(gross - net)) };
  }

  var orderModalSeq = 0;
  var orderFormLines = [];

  function closeOrderModal() {
    orderModalSeq += 1;
    var box = document.querySelector(".wh-modal-backdrop.wh-order-modal");
    if (box) box.remove();
    document.removeEventListener("keydown", onOrderModalKey);
  }

  function onOrderModalKey(e) {
    if (e.key === "Escape") closeOrderModal();
  }

  function optionList(items, selectedId, emptyLabel) {
    var html = emptyLabel ? '<option value="">' + esc(emptyLabel) + "</option>" : "";
    (items || []).forEach(function (it) {
      var sel = String(selectedId || "") === String(it.id) ? " selected" : "";
      html += '<option value="' + esc(it.id) + '"' + sel + ">" + esc(it.name) + "</option>";
    });
    return html;
  }

  function priceTypeMenuHtml() {
    if (!(ordersMeta.price_types || []).length) {
      return '<p class="wh-msg">Нет видов цен.</p>';
    }
    return ordersMeta.price_types
      .map(function (pt) {
        return (
          '<button type="button" class="wh-rc-price-type-opt" data-price-type-id="' +
          esc(pt.id) +
          '">' +
          esc(pt.name) +
          "</button>"
        );
      })
      .join("");
  }

  function orderLineRowHtml(ln, index, kind) {
    ln = ln || {};
    var commClass = kind === "commission" ? "" : " hidden";
    return (
      '<tr class="wh-order-line-row" data-index="' +
      index +
      '">' +
      "<td><code>" +
      esc(ln.sku || "") +
      "</code></td>" +
      "<td>" +
      esc(ln.name || "") +
      "</td>" +
      '<td><input type="number" class="wh-order-qty" min="0" value="' +
      esc(ln.quantity == null ? 1 : ln.quantity) +
      '" /></td>' +
      '<td><input type="text" class="wh-order-price" inputmode="decimal" value="' +
      esc(moneyInputValue(ln.unit_price)) +
      '" /></td>' +
      '<td><input type="text" class="wh-order-price-net" readonly value="' +
      esc(moneyInputValue(ln.unit_price_net)) +
      '" /></td>' +
      '<td><input type="text" class="wh-order-vat-amt" readonly value="' +
      esc(moneyInputValue(ln.vat_amount)) +
      '" /></td>' +
      '<td><input type="text" class="wh-order-cost" inputmode="decimal" value="' +
      esc(moneyInputValue(ln.cost)) +
      '" /></td>' +
      '<td class="wh-order-comm-cell' +
      commClass +
      '"><input type="text" class="wh-order-commission" inputmode="decimal" value="' +
      esc(moneyInputValue(ln.commission)) +
      '" /></td>' +
      '<td><button type="button" class="wh-btn wh-btn-sm wh-order-remove-line" title="Удалить">&times;</button></td></tr>'
    );
  }

  function renderOrderLinesTable(root) {
    var kind = (root.querySelector("#whOrderKind") || {}).value || "sale";
    var body = root.querySelector("#whOrderLinesBody");
    if (!orderFormLines.length) {
      body.innerHTML =
        '<tr><td colspan="9" class="wh-muted">Нет строк. Найдите товар ниже.</td></tr>';
      return;
    }
    body.innerHTML = orderFormLines.map(function (ln, i) {
      return orderLineRowHtml(ln, i, kind);
    }).join("");
    bindOrderLineEvents(root);
    recalcOrderLineVat(root);
  }

  function recalcOrderLineVat(root) {
    var rate = parseMoneyInput((root.querySelector("#whOrderVatRate") || {}).value);
    root.querySelectorAll(".wh-order-line-row").forEach(function (tr) {
      var gross = parseMoneyInput(tr.querySelector(".wh-order-price").value);
      var parts = splitVatClient(gross, rate);
      tr.querySelector(".wh-order-price-net").value = parts.net;
      tr.querySelector(".wh-order-vat-amt").value = parts.vat;
    });
  }

  function toggleCommissionColumns(root) {
    var kind = (root.querySelector("#whOrderKind") || {}).value;
    var show = kind === "commission";
    root.querySelectorAll(".wh-order-comm-head, .wh-order-comm-cell").forEach(function (el) {
      el.classList.toggle("hidden", !show);
    });
  }

  function collectOrderLines(root) {
    var out = [];
    root.querySelectorAll(".wh-order-line-row").forEach(function (tr) {
      var idx = parseInt(tr.getAttribute("data-index"), 10);
      var src = orderFormLines[idx] || {};
      out.push({
        sku: src.sku,
        product_id: src.product_id || null,
        name: src.name || "",
        quantity: parseInt(tr.querySelector(".wh-order-qty").value, 10) || 0,
        unit_price: parseMoneyInput(tr.querySelector(".wh-order-price").value),
        cost: parseMoneyInput(tr.querySelector(".wh-order-cost").value),
        commission: parseMoneyInput(tr.querySelector(".wh-order-commission").value),
      });
    });
    return out;
  }

  function bindOrderLineEvents(root) {
    root.querySelectorAll(".wh-order-price").forEach(function (inp) {
      inp.addEventListener("input", function () {
        recalcOrderLineVat(root);
      });
    });
    root.querySelectorAll(".wh-order-remove-line").forEach(function (btn) {
      btn.addEventListener("click", function () {
        orderFormLines = collectOrderLines(root);
        var tr = btn.closest("tr");
        var idx = parseInt(tr.getAttribute("data-index"), 10);
        orderFormLines.splice(idx, 1);
        renderOrderLinesTable(root);
      });
    });
  }

  function addProductToOrder(root, product) {
    orderFormLines = collectOrderLines(root);
    var sku = String(product.sku || "").trim();
    if (!sku) return;
    var existing = null;
    for (var i = 0; i < orderFormLines.length; i++) {
      if (orderFormLines[i].sku === sku) {
        existing = orderFormLines[i];
        break;
      }
    }
    if (existing) {
      existing.quantity = (parseInt(existing.quantity, 10) || 0) + 1;
    } else {
      orderFormLines.push({
        sku: sku,
        product_id: product.id || product.product_id || null,
        name: product.name || sku,
        quantity: 1,
        unit_price: null,
        cost: null,
        commission: null,
      });
    }
    renderOrderLinesTable(root);
  }

  function searchOrderProducts(root) {
    var q = (root.querySelector("#whOrderProductQ") || {}).value || "";
    var wrap = root.querySelector("#whOrderSearchResults");
    if (!q.trim()) {
      wrap.innerHTML = "";
      return;
    }
    fetchJson("/api/warehouse/orders/products/search?q=" + encodeURIComponent(q.trim()))
      .then(function (data) {
        var list = data.products || [];
        if (!list.length) {
          wrap.innerHTML = '<p class="wh-msg">Ничего не найдено.</p>';
          return;
        }
        wrap.innerHTML = list
          .map(function (p) {
            return (
              '<button type="button" class="wh-rc-search-item">' +
              esc(p.name) +
              " · <code>" +
              esc(p.sku) +
              "</code></button>"
            );
          })
          .join("");
        wrap.querySelectorAll(".wh-rc-search-item").forEach(function (btn, i) {
          btn.addEventListener("click", function () {
            addProductToOrder(root, list[i]);
            wrap.innerHTML = "";
          });
        });
      })
      .catch(function (err) {
        wrap.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function applyPriceTypeToOrder(orderId, priceTypeId, root) {
    if (orderId) {
      fetchJson("/api/warehouse/orders/" + orderId + "/apply-price-type", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ price_type_id: priceTypeId }),
      })
        .then(function (d) {
          orderFormLines = d.order.lines || [];
          renderOrderLinesTable(root);
        })
        .catch(function (err) {
          alert(err.message || "Не удалось выставить цены");
        });
      return;
    }
    var ids = [];
    orderFormLines = collectOrderLines(root);
    orderFormLines.forEach(function (ln) {
      if (ln.product_id) ids.push(ln.product_id);
    });
    if (!ids.length) return;
    fetchJson("/api/warehouse/orders/price-by-type", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ price_type_id: priceTypeId, product_ids: ids }),
    })
      .then(function (data) {
        var prices = data.prices || {};
        orderFormLines.forEach(function (ln) {
          var raw = prices[String(ln.product_id)];
          ln.unit_price = raw == null || raw === "" ? 0 : raw;
        });
        renderOrderLinesTable(root);
      })
      .catch(function (err) {
        alert(err.message || "Не удалось выставить цены");
      });
  }

  function collectOrderPayload(root, includeLines) {
    var payload = {
      status: root.querySelector("#whOrderStatus").value,
      counterparty_id: parseInt(root.querySelector("#whOrderCounterparty").value, 10),
      comment: root.querySelector("#whOrderComment").value || "",
      order_kind: root.querySelector("#whOrderKind").value,
      vat_rate: parseMoneyInput(root.querySelector("#whOrderVatRate").value),
    };
    if (includeLines) payload.lines = collectOrderLines(root);
    return payload;
  }

  function openOrderModal(order) {
    closeOrderModal();
    var isNew = !order || !order.id;
    orderFormLines = isNew ? [] : (order.lines || []).slice();
    var title = isNew ? "Новый заказ покупателя" : esc(order.number) + " · " + esc(statusLabel(order.status));
    var postingHtml = isNew
      ? ""
      : "<dt>Отправление</dt><dd>" + esc(order.posting_id || "—") + "</dd>";
    var backdrop = document.createElement("div");
    backdrop.className = "wh-modal-backdrop wh-order-modal";
    backdrop.innerHTML =
      '<div class="wh-modal wh-modal-wide wh-modal-order" role="dialog">' +
      '<div class="wh-modal-header"><h3>' +
      title +
      '</h3><button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body">' +
      '<div class="wh-order-form-grid">' +
      "<label>Статус <select id=\"whOrderStatus\">" +
      optionList(ordersMeta.statuses, isNew ? "open" : order.status) +
      "</select></label>" +
      "<label>Контрагент <select id=\"whOrderCounterparty\">" +
      optionList(ordersMeta.counterparties, isNew ? "" : order.counterparty_id, "— выберите —") +
      "</select></label>" +
      "<label>Тип заказа <select id=\"whOrderKind\">" +
      optionList(ordersMeta.order_kinds, isNew ? "sale" : order.order_kind || "sale") +
      "</select></label>" +
      "<label>% НДС <input type=\"text\" id=\"whOrderVatRate\" inputmode=\"decimal\" value=\"" +
      esc(moneyInputValue(isNew ? 22 : order.vat_rate == null ? 22 : order.vat_rate)) +
      '" /></label></div>' +
      (postingHtml ? '<dl class="wh-order-grid">' + postingHtml + "</dl>" : "") +
      '<label class="wh-order-comment-label">Комментарий <textarea id="whOrderComment" rows="3">' +
      esc(isNew ? "" : order.comment || "") +
      "</textarea></label>" +
      '<div class="wh-crm-toolbar">' +
      '<div class="wh-rc-price-type-wrap">' +
      '<button type="button" class="wh-btn wh-btn-sm" id="whOrderPriceBtn">Выставить цены по виду ▾</button>' +
      '<div class="wh-rc-price-menu hidden" id="whOrderPriceMenu">' +
      priceTypeMenuHtml() +
      "</div></div></div>" +
      '<div class="wh-order-table-wrap"><table class="wh-employees-table wh-crm-table wh-order-lines-table"><thead><tr>' +
      "<th>Артикул</th><th>Наименование</th><th>Кол-во</th><th>Цена с НДС</th><th>Цена без НДС</th><th>Сумма НДС</th><th>Себестоимость</th>" +
      '<th class="wh-order-comm-head">Комиссия</th><th></th>' +
      '</tr></thead><tbody id="whOrderLinesBody"></tbody></table></div>' +
      '<div class="wh-crm-toolbar"><input type="search" id="whOrderProductQ" class="wh-crm-search" placeholder="Найти товар по названию или артикулу…" /></div>' +
      '<div id="whOrderSearchResults" class="wh-rc-search-results"></div>' +
      '<p class="wh-msg" id="whOrderFormMsg"></p></div>' +
      '<div class="wh-modal-footer">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whOrderSave">Сохранить</button>' +
      '<button type="button" class="wh-btn wh-modal-cancel">Отмена</button></div></div>';
    document.body.appendChild(backdrop);
    renderOrderLinesTable(backdrop);
    toggleCommissionColumns(backdrop);
    backdrop.querySelector(".wh-modal-close").addEventListener("click", closeOrderModal);
    backdrop.querySelector(".wh-modal-cancel").addEventListener("click", closeOrderModal);
    backdrop.addEventListener("click", function (e) {
      if (e.target === backdrop) closeOrderModal();
    });
    document.addEventListener("keydown", onOrderModalKey);
    backdrop.querySelector("#whOrderKind").addEventListener("change", function () {
      toggleCommissionColumns(backdrop);
    });
    backdrop.querySelector("#whOrderVatRate").addEventListener("input", function () {
      recalcOrderLineVat(backdrop);
    });
    backdrop.querySelector("#whOrderPriceBtn").addEventListener("click", function (e) {
      e.stopPropagation();
      backdrop.querySelector("#whOrderPriceMenu").classList.toggle("hidden");
    });
    backdrop.querySelectorAll(".wh-rc-price-type-opt").forEach(function (btn) {
      btn.addEventListener("click", function () {
        backdrop.querySelector("#whOrderPriceMenu").classList.add("hidden");
        applyPriceTypeToOrder(isNew ? null : order.id, parseInt(btn.getAttribute("data-price-type-id"), 10), backdrop);
      });
    });
    var searchTimer = null;
    backdrop.querySelector("#whOrderProductQ").addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        searchOrderProducts(backdrop);
      }, 250);
    });
    backdrop.querySelector("#whOrderSave").addEventListener("click", function () {
      var msg = backdrop.querySelector("#whOrderFormMsg");
      msg.textContent = "";
      var payload = collectOrderPayload(backdrop, true);
      if (!payload.counterparty_id) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Укажите контрагента";
        return;
      }
      if (!payload.lines.length) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Добавьте хотя бы одну строку";
        return;
      }
      var url = isNew ? "/api/warehouse/orders" : "/api/warehouse/orders/" + order.id;
      var method = isNew ? "POST" : "PATCH";
      fetchJson(url, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function () {
          closeOrderModal();
          var reloadBtn = panelEl().querySelector("#whOrdersReload");
          if (reloadBtn) reloadBtn.click();
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || "Не удалось сохранить";
        });
    });
  }

  function showOrderWindow(id) {
    var seq = ++orderModalSeq;
    var existing = document.querySelector(".wh-modal-backdrop.wh-order-modal");
    if (existing) existing.remove();
    var loading = document.createElement("div");
    loading.className = "wh-modal-backdrop wh-order-modal";
    loading.innerHTML =
      '<div class="wh-modal wh-modal-order" role="dialog"><div class="wh-modal-header"><h3>Заказ</h3>' +
      '<button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body"><p class="wh-msg">Загрузка…</p></div></div>';
    document.body.appendChild(loading);
    loading.querySelector(".wh-modal-close").addEventListener("click", closeOrderModal);
    loading.addEventListener("click", function (e) {
      if (e.target === loading) closeOrderModal();
    });
    document.addEventListener("keydown", onOrderModalKey);
    fetchJson("/api/warehouse/orders/" + id)
      .then(function (d) {
        if (seq !== orderModalSeq) return;
        openOrderModal(d.order);
      })
      .catch(function (err) {
        if (seq !== orderModalSeq) return;
        closeOrderModal();
        alert((err && err.message) || "Не удалось открыть заказ");
      });
  }
  function counterpartyLabel(order) {
    return order.counterparty_name || "—";
  }

  function orderRowHtml(o) {
    var canShip = o.status === "open" || o.status === "in_wave" || o.status === "packed";
    return (
      "<tr data-id=\"" +
      esc(o.id) +
      "\" style=\"cursor:pointer\">" +
      "<td><input type=\"checkbox\" class=\"wh-wms-order-cb\" data-id=\"" +
      esc(o.id) +
      "\"" +
      (canShip ? "" : " disabled") +
      " onclick=\"event.stopPropagation()\" /></td>" +
      "<td>" +
      esc(o.number) +
      "</td><td>" +
      esc(counterpartyLabel(o)) +
      "</td><td>" +
      esc(o.comment || "") +
      "</td><td>" +
      esc(statusLabel(o.status)) +
      "</td><td>" +
      esc(formatDate(o.created_at_ts)) +
      "</td></tr>"
    );
  }

  function renderCustomerOrders(tab, item) {
    preparePanel(tab, item);
    panelEl().innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    var ordersPage = 1;
    fetchJson("/api/warehouse/orders/meta")
      .then(function (meta) {
        ordersMeta = meta;
        panelEl().innerHTML =
          '<div class="wh-crm-toolbar">' +
          '<input type="search" id="whOrdersQ" class="wh-crm-search" placeholder="Номер или комментарий…" />' +
          '<select id="whOrdersStatus"><option value="">Все статусы</option>' +
          ordersMeta.statuses
            .map(function (s) {
              return '<option value="' + esc(s.id) + '">' + esc(s.name) + "</option>";
            })
            .join("") +
          "</select>" +
          '<select id="whOrdersCounterparty"><option value="">Все контрагенты</option>' +
          (ordersMeta.counterparties || [])
            .map(function (s) {
              return '<option value="' + esc(s.id) + '">' + esc(s.name) + "</option>";
            })
            .join("") +
          "</select>" +
          '<button type="button" class="wh-btn wh-btn-primary" id="whOrdersCreate">Создать заказ</button>' +
          '<button type="button" class="wh-btn" id="whOrdersReload">Обновить</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whOrdersShip">Отгрузить выбранные</button>' +
          "</div>" +
          '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
          "<th></th><th>ЗК</th><th>Контрагент</th><th>Комментарий</th><th>Статус</th><th>Дата заказа</th>" +
          "</tr></thead><tbody id=\"whOrdersBody\"></tbody></table>" +
          '<div id="whOrdersPager"></div>';

        function ordersUrl(resetPage) {
          if (resetPage) ordersPage = 1;
          var q = (panelEl().querySelector("#whOrdersQ").value || "").trim();
          var st = panelEl().querySelector("#whOrdersStatus").value;
          var cp = panelEl().querySelector("#whOrdersCounterparty").value;
          var url = "/api/warehouse/orders?page=" + encodeURIComponent(String(ordersPage));
          if (q) url += "&q=" + encodeURIComponent(q);
          if (st) url += "&status=" + encodeURIComponent(st);
          if (cp) url += "&counterparty_id=" + encodeURIComponent(cp);
          return url;
        }

        function bindOrderRows() {
          panelEl().querySelectorAll("#whOrdersBody tr[data-id]").forEach(function (tr) {
            tr.addEventListener("click", function () {
              showOrderWindow(tr.getAttribute("data-id"));
            });
          });
        }

        function paintOrders(data) {
          ordersPage = Number(data.page || 1);
          panelEl().querySelector("#whOrdersBody").innerHTML = (data.orders || [])
            .map(orderRowHtml)
            .join("");
          var host = panelEl().querySelector("#whOrdersPager");
          var p = pager();
          if (host && p) {
            host.innerHTML = p.html(p.state(data.total, data.page, data.limit));
            p.bind(host, function (delta) {
              ordersPage = Math.max(1, ordersPage + delta);
              reload(false);
            });
          }
          bindOrderRows();
        }

        function reload(resetPage) {
          fetchJson(ordersUrl(resetPage)).then(paintOrders);
        }

        panelEl().querySelector("#whOrdersReload").addEventListener("click", function () {
          reload(true);
        });
        panelEl().querySelector("#whOrdersCreate").addEventListener("click", function () {
          openOrderModal(null);
        });
        panelEl().querySelector("#whOrdersQ").addEventListener("keydown", function (e) {
          if (e.key === "Enter") reload(true);
        });
        panelEl().querySelector("#whOrdersStatus").addEventListener("change", function () {
          reload(true);
        });
        panelEl().querySelector("#whOrdersCounterparty").addEventListener("change", function () {
          reload(true);
        });
        panelEl().querySelector("#whOrdersShip").addEventListener("click", function () {
          var ids = [];
          panelEl().querySelectorAll(".wh-wms-order-cb:checked").forEach(function (cb) {
            ids.push(parseInt(cb.getAttribute("data-id"), 10));
          });
          if (!ids.length) {
            alert("Выберите заказы для отгрузки");
            return;
          }
          fetchJson("/api/warehouse/shipments", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ order_ids: ids, post: true, title: "Ручная отгрузка" }),
          })
            .then(function () {
              reload(true);
              alert("Отгрузка создана и проведена");
            })
            .catch(function (err) {
              alert(err.message || "Ошибка отгрузки");
            });
        });
        return reload(true);
      })
      .catch(function (err) {
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function renderShipments(tab, item) {
    preparePanel(tab, item);
    panelEl().innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    fetchJson("/api/warehouse/shipments")
      .then(function (data) {
        var all = data.shipments || [];
        var page = 1;
        function paint() {
          var p = pager();
          var sliced = p ? p.slice(all, page) : { items: all, state: { page: 1 } };
          page = sliced.state.page;
          var rows = sliced.items
            .map(function (s) {
              return (
                "<tr data-id=\"" +
                esc(s.id) +
                "\"><td>" +
                esc(s.number) +
                "</td><td>" +
                esc(s.title) +
                "</td><td>" +
                esc(s.status) +
                "</td><td>" +
                esc(s.origin) +
                "</td><td>" +
                formatTs(s.posted_at_ts || s.created_at_ts) +
                "</td></tr>"
              );
            })
            .join("");
          panelEl().innerHTML =
            '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
            "<th>Номер</th><th>Название</th><th>Статус</th><th>Источник</th><th>Дата</th>" +
            "</tr></thead><tbody>" +
            rows +
            "</tbody></table>" +
            (p ? p.html(sliced.state) : "") +
            '<div id="whShipmentDetail" class="wh-msg hidden"></div>';
          if (p) {
            p.bind(panelEl(), function (delta) {
              page += delta;
              paint();
            });
          }
          panelEl().querySelectorAll("tbody tr[data-id]").forEach(function (tr) {
            tr.style.cursor = "pointer";
            tr.addEventListener("click", function () {
              fetchJson("/api/warehouse/shipments/" + tr.getAttribute("data-id")).then(function (d) {
                var s = d.shipment;
                var lines = (s.lines || [])
                  .map(function (ln) {
                    return "<li>" + esc(ln.sku) + " × " + esc(ln.quantity) + "</li>";
                  })
                  .join("");
                var box = panelEl().querySelector("#whShipmentDetail");
                box.classList.remove("hidden");
                box.innerHTML =
                  "<h4>" +
                  esc(s.number) +
                  " · " +
                  esc(s.status) +
                  "</h4><p>Postings: " +
                  esc((s.posting_ids || []).join(", ")) +
                  "</p><ul>" +
                  lines +
                  "</ul>";
              });
            });
          });
        }
        paint();
      })
      .catch(function (err) {
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function renderPickWaves(tab, item) {
    preparePanel(tab, item);
    panelEl().innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    fetchJson("/api/warehouse/pick-waves")
      .then(function (data) {
        var all = data.jobs || [];
        var page = 1;
        function paint() {
          var p = pager();
          var sliced = p ? p.slice(all, page) : { items: all, state: { page: 1 } };
          page = sliced.state.page;
          var rows = sliced.items
            .map(function (j) {
              var canShip = j.status === "done" || j.status === "in_progress" || j.status === "open";
              return (
                "<tr data-id=\"" +
                esc(j.id) +
                "\"><td>" +
                esc(j.id) +
                "</td><td>" +
                esc(j.marketplace) +
                "</td><td>" +
                esc(j.status) +
                "</td><td>" +
                esc(j.line_done) +
                "/" +
                esc(j.line_total) +
                "</td><td>" +
                formatTs(j.created_at_ts) +
                '</td><td><button type="button" class="wh-btn wh-btn-sm wh-wave-ship" data-id="' +
                esc(j.id) +
                "\"" +
                (canShip ? "" : " disabled") +
                ">Отгрузить волну</button></td></tr>"
              );
            })
            .join("");
          panelEl().innerHTML =
            '<p class="wh-msg">Волны = FBS-задания. Сборка — в разделе «FBS».</p>' +
            '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
            "<th>ID</th><th>МП</th><th>Статус</th><th>Строки</th><th>Создано</th><th></th>" +
            "</tr></thead><tbody>" +
            rows +
            "</tbody></table>" +
            (p ? p.html(sliced.state) : "");
          if (p) {
            p.bind(panelEl(), function (delta) {
              page += delta;
              paint();
            });
          }
          panelEl().querySelectorAll(".wh-wave-ship").forEach(function (btn) {
            btn.addEventListener("click", function (e) {
              e.stopPropagation();
              var jobId = btn.getAttribute("data-id");
              fetchJson("/api/warehouse/shipments", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  packing_job_id: parseInt(jobId, 10),
                  post: true,
                  title: "Отгрузка волны " + jobId,
                }),
              })
                .then(function (data) {
                  var warns = (data && data.shipment && data.shipment.warnings) || [];
                  alert(
                    warns.length
                      ? "Отгрузка волны проведена:\n" + warns.slice(0, 8).join("\n")
                      : "Отгрузка волны проведена. Заказы в статусе «Отгружено»."
                  );
                  renderPickWaves(tab, item);
                })
                .catch(function (err) {
                  alert(err.message || "Ошибка");
                });
            });
          });
        }
        paint();
      })
      .catch(function (err) {
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function binOptions(warehouseId, selectedId) {
    for (var i = 0; i < invMeta.warehouses.length; i++) {
      if (String(invMeta.warehouses[i].id) === String(warehouseId)) {
        return (invMeta.warehouses[i].bins || [])
          .map(function (b) {
            var sel =
              String(selectedId || "") === String(b.id) || (!selectedId && b.is_default)
                ? " selected"
                : "";
            return (
              '<option value="' +
              esc(b.id) +
              '"' +
              sel +
              ">" +
              esc(b.code + (b.name && b.name !== b.code ? " — " + b.name : "")) +
              "</option>"
            );
          })
          .join("");
      }
    }
    return "";
  }

  function renderInventory(tab, item) {
    preparePanel(tab, item);
    panelEl().innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    fetchJson("/api/warehouse/inventory-counts/meta")
      .then(function (meta) {
        invMeta = meta;
        return fetchJson("/api/warehouse/inventory-counts");
      })
      .then(function (data) {
        var whId = invMeta.warehouses[0] ? invMeta.warehouses[0].id : "";
        var invAll = data.counts || [];
        var invPage = 1;
        panelEl().innerHTML =
          '<div class="wh-crm-toolbar">' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whInvNew">Новый пересчёт</button>' +
          "</div>" +
          '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
          "<th>Номер</th><th>Склад</th><th>Ячейка</th><th>Статус</th><th>Создан</th>" +
          "</tr></thead><tbody id=\"whInvList\"></tbody></table>" +
          '<div id="whInvPager"></div>' +
          '<div id="whInvFormWrap" class="hidden wh-crm-section">' +
          '<h4 class="wh-crm-section-title">Пересчёт</h4>' +
          '<div class="wh-form-row">' +
          '<div><label>Склад</label><select id="whInvWh">' +
          invMeta.warehouses
            .map(function (w) {
              return (
                '<option value="' +
                esc(w.id) +
                '"' +
                (String(w.id) === String(whId) ? " selected" : "") +
                ">" +
                esc(w.name) +
                "</option>"
              );
            })
            .join("") +
          "</select></div>" +
          '<div><label>Ячейка</label><select id="whInvBin">' +
          binOptions(whId, "") +
          "</select></div>" +
          '<div class="wh-rc-search-btn-wrap"><button type="button" class="wh-btn" id="whInvFill">Заполнить из ячейки</button></div>' +
          "</div>" +
          '<div id="whInvLines"></div>' +
          '<div class="wh-form-actions">' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whInvSave">Сохранить черновик</button>' +
          '<button type="button" class="wh-btn" id="whInvPost">Провести</button>' +
          "</div></div>";
        var invLines = [];
        var draftId = null;

        function paintInvList() {
          var p = pager();
          var sliced = p ? p.slice(invAll, invPage) : { items: invAll, state: { page: 1 } };
          invPage = sliced.state.page;
          panelEl().querySelector("#whInvList").innerHTML = sliced.items
            .map(function (c) {
              return (
                "<tr data-id=\"" +
                esc(c.id) +
                "\"><td>" +
                esc(c.number) +
                "</td><td>" +
                esc(c.warehouse_name) +
                "</td><td>" +
                esc(c.bin_name) +
                "</td><td>" +
                esc(c.status) +
                "</td><td>" +
                formatTs(c.created_at_ts) +
                "</td></tr>"
              );
            })
            .join("");
          var host = panelEl().querySelector("#whInvPager");
          if (host && p) {
            host.innerHTML = p.html(sliced.state);
            p.bind(host, function (delta) {
              invPage += delta;
              paintInvList();
            });
          }
          panelEl().querySelectorAll("#whInvList tr[data-id]").forEach(function (tr) {
            tr.style.cursor = "pointer";
            tr.addEventListener("click", function () {
              fetchJson("/api/warehouse/inventory-counts/" + tr.getAttribute("data-id")).then(function (d) {
                var c = d.count;
                draftId = c.id;
                panelEl().querySelector("#whInvFormWrap").classList.remove("hidden");
                panelEl().querySelector("#whInvWh").value = c.warehouse_id;
                panelEl().querySelector("#whInvBin").innerHTML = binOptions(c.warehouse_id, c.bin_id);
                invLines = c.lines || [];
                renderLines();
              });
            });
          });
        }

        function renderLines() {
          var html = invLines
            .map(function (ln, idx) {
              return (
                '<div class="wh-form-row wh-inv-line" data-idx="' +
                idx +
                '"><div><code>' +
                esc(ln.sku) +
                "</code></div><div>Учёт: " +
                esc(ln.book_qty != null ? ln.book_qty : "—") +
                '</div><div><label>Факт</label><input type="number" class="wh-inv-fact" min="0" value="' +
                esc(ln.fact_qty != null ? ln.fact_qty : 0) +
                '" /></div></div>'
              );
            })
            .join("");
          panelEl().querySelector("#whInvLines").innerHTML = html || '<p class="wh-msg">Нет строк</p>';
        }

        panelEl().querySelector("#whInvNew").addEventListener("click", function () {
          panelEl().querySelector("#whInvFormWrap").classList.remove("hidden");
          invLines = [];
          draftId = null;
          renderLines();
        });
        panelEl().querySelector("#whInvWh").addEventListener("change", function () {
          panelEl().querySelector("#whInvBin").innerHTML = binOptions(
            panelEl().querySelector("#whInvWh").value,
            ""
          );
        });
        panelEl().querySelector("#whInvFill").addEventListener("click", function () {
          var wh = panelEl().querySelector("#whInvWh").value;
          var bin = panelEl().querySelector("#whInvBin").value;
          fetchJson(
            "/api/warehouse/inventory-counts/fill?warehouse_id=" +
              encodeURIComponent(wh) +
              "&bin_id=" +
              encodeURIComponent(bin)
          ).then(function (d) {
            invLines = d.items || [];
            renderLines();
          });
        });
        panelEl().querySelector("#whInvSave").addEventListener("click", function () {
          panelEl().querySelectorAll(".wh-inv-line").forEach(function (row) {
            var idx = parseInt(row.getAttribute("data-idx"), 10);
            invLines[idx].fact_qty = parseInt(row.querySelector(".wh-inv-fact").value, 10) || 0;
          });
          var body = {
            warehouse_id: panelEl().querySelector("#whInvWh").value,
            bin_id: panelEl().querySelector("#whInvBin").value,
            items: invLines.map(function (ln) {
              return {
                sku: ln.sku,
                product_id: ln.product_id,
                name: ln.name,
                fact_qty: ln.fact_qty,
              };
            }),
          };
          var url = draftId
            ? "/api/warehouse/inventory-counts/" + draftId
            : "/api/warehouse/inventory-counts";
          fetchJson(url, {
            method: draftId ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }).then(function (d) {
            draftId = d.count.id;
            alert("Черновик сохранён: " + d.count.number);
          });
        });
        panelEl().querySelector("#whInvPost").addEventListener("click", function () {
          if (!draftId) {
            alert("Сначала сохраните черновик");
            return;
          }
          fetchJson("/api/warehouse/inventory-counts/" + draftId + "/post", { method: "POST" }).then(
            function () {
              alert("Пересчёт проведён");
              renderInventory(tab, item);
            }
          );
        });
        paintInvList();
      })
      .catch(function (err) {
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function renderBinTransfers(tab, item) {
    preparePanel(tab, item);
    panelEl().innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    var formItems = [];
    fetchJson("/api/warehouse/bin-transfers/meta")
      .then(function (meta) {
        invMeta = meta;
        return fetchJson("/api/warehouse/bin-transfers");
      })
      .then(function (data) {
        var whId = invMeta.warehouses[0] ? invMeta.warehouses[0].id : "";
        var bins = invMeta.warehouses[0] ? invMeta.warehouses[0].bins || [] : [];
        var defaultFrom = bins[0] ? bins[0].id : "";
        var defaultTo = bins[1] ? bins[1].id : defaultFrom;
        var btAll = data.transfers || [];
        var btPage = 1;
        panelEl().innerHTML =
          '<div class="wh-crm-toolbar"><button type="button" class="wh-btn wh-btn-primary" id="whBtNew">Новое перемещение</button></div>' +
          '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
          "<th>Название</th><th>Склад</th><th>Ячейки</th><th>Кол-во</th><th>Дата</th>" +
          "</tr></thead><tbody id=\"whBtList\"></tbody></table>" +
          '<div id="whBtPager"></div>' +
          '<div id="whBtForm" class="hidden wh-crm-section">' +
          '<div class="wh-form-row">' +
          '<div><label>Склад</label><select id="whBtWh">' +
          invMeta.warehouses
            .map(function (w) {
              return '<option value="' + esc(w.id) + '">' + esc(w.name) + "</option>";
            })
            .join("") +
          "</select></div>" +
          '<div><label>Откуда</label><select id="whBtFrom">' +
          binOptions(whId, defaultFrom) +
          "</select></div>" +
          '<div><label>Куда</label><select id="whBtTo">' +
          binOptions(whId, defaultTo) +
          "</select></div>" +
          "</div>" +
          '<div class="wh-rc-search-grid">' +
          '<input type="text" id="whBtSearch" placeholder="Поиск SKU…" />' +
          '<button type="button" class="wh-btn" id="whBtSearchBtn">Найти</button></div>' +
          '<div id="whBtSearchResults"></div>' +
          '<div id="whBtItems"></div>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whBtSave">Провести</button></div>';

        function paintBtList() {
          var p = pager();
          var sliced = p ? p.slice(btAll, btPage) : { items: btAll, state: { page: 1 } };
          btPage = sliced.state.page;
          panelEl().querySelector("#whBtList").innerHTML = sliced.items
            .map(function (t) {
              return (
                "<tr><td>" +
                esc(t.title) +
                "</td><td>" +
                esc(t.warehouse_name) +
                "</td><td>" +
                esc(t.from_bin_name) +
                " → " +
                esc(t.to_bin_name) +
                "</td><td>" +
                esc(t.total_quantity) +
                "</td><td>" +
                formatTs(t.created_at_ts) +
                "</td></tr>"
              );
            })
            .join("");
          var host = panelEl().querySelector("#whBtPager");
          if (host && p) {
            host.innerHTML = p.html(sliced.state);
            p.bind(host, function (delta) {
              btPage += delta;
              paintBtList();
            });
          }
        }

        function renderItems() {
          panelEl().querySelector("#whBtItems").innerHTML = formItems
            .map(function (it, idx) {
              return (
                '<div class="wh-rc-item-row"><span>' +
                esc(it.name) +
                " <code>" +
                esc(it.sku) +
                '</code></span><input type="number" min="1" class="wh-bt-qty" data-idx="' +
                idx +
                '" value="' +
                esc(it.quantity) +
                '" /><button type="button" class="wh-btn wh-btn-sm wh-bt-rm" data-idx="' +
                idx +
                '">&times;</button></div>'
              );
            })
            .join("");
          panelEl().querySelectorAll(".wh-bt-rm").forEach(function (btn) {
            btn.addEventListener("click", function () {
              formItems.splice(parseInt(btn.getAttribute("data-idx"), 10), 1);
              renderItems();
            });
          });
        }

        paintBtList();

        panelEl().querySelector("#whBtNew").addEventListener("click", function () {
          panelEl().querySelector("#whBtForm").classList.remove("hidden");
        });
        panelEl().querySelector("#whBtWh").addEventListener("change", function () {
          var wh = panelEl().querySelector("#whBtWh").value;
          panelEl().querySelector("#whBtFrom").innerHTML = binOptions(wh, "");
          panelEl().querySelector("#whBtTo").innerHTML = binOptions(wh, "");
        });
        panelEl().querySelector("#whBtSearchBtn").addEventListener("click", function () {
          var q = panelEl().querySelector("#whBtSearch").value.trim();
          fetchJson("/api/warehouse/bin-transfers/products/search?q=" + encodeURIComponent(q)).then(
            function (d) {
              panelEl().querySelector("#whBtSearchResults").innerHTML = (d.products || [])
                .map(function (p) {
                  return (
                    '<button type="button" class="wh-btn wh-btn-sm wh-bt-add" data-id="' +
                    esc(p.id) +
                    '" data-sku="' +
                    esc(p.sku) +
                    '" data-name="' +
                    esc(p.name) +
                    '">+ ' +
                    esc(p.sku) +
                    "</button> "
                  );
                })
                .join("");
              panelEl().querySelectorAll(".wh-bt-add").forEach(function (btn) {
                btn.addEventListener("click", function () {
                  formItems.push({
                    product_id: parseInt(btn.getAttribute("data-id"), 10),
                    sku: btn.getAttribute("data-sku"),
                    name: btn.getAttribute("data-name"),
                    quantity: 1,
                  });
                  renderItems();
                });
              });
            }
          );
        });
        panelEl().querySelector("#whBtSave").addEventListener("click", function () {
          panelEl().querySelectorAll(".wh-bt-qty").forEach(function (inp) {
            var idx = parseInt(inp.getAttribute("data-idx"), 10);
            formItems[idx].quantity = parseInt(inp.value, 10) || 1;
          });
          fetchJson("/api/warehouse/bin-transfers", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              title: "Перемещение по ячейкам",
              warehouse_id: panelEl().querySelector("#whBtWh").value,
              from_bin_id: panelEl().querySelector("#whBtFrom").value,
              to_bin_id: panelEl().querySelector("#whBtTo").value,
              items: formItems.map(function (it) {
                return { product_id: it.product_id, quantity: it.quantity };
              }),
            }),
          }).then(function () {
            alert("Перемещение проведено");
            renderBinTransfers(tab, item);
          });
        });
      })
      .catch(function (err) {
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function render(tab, item) {
    if (item.id === "customer-orders") return renderCustomerOrders(tab, item);
    if (item.id === "shipments") return renderShipments(tab, item);
    if (item.id === "pick-waves") return renderPickWaves(tab, item);
    if (item.id === "inventory") return renderInventory(tab, item);
    if (item.id === "bin-transfers") return renderBinTransfers(tab, item);
  }

  global.WhWms = { render: render };
})(window);
