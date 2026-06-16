(function (global) {
  var meta = { warehouses: [], price_types: [] };
  var listFilters = {};
  var filterPanelOpen = false;
  var editingId = null;
  var formItems = [];

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

  function formatTs(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1000).toLocaleString("ru-RU");
    } catch (e) {
      return String(ts);
    }
  }

  function loadMeta() {
    return fetchJson("/api/warehouse/writeoffs/meta").then(function (data) {
      meta.warehouses = data.warehouses || [];
      meta.price_types = data.price_types || [];
      return meta;
    });
  }

  function defaultWarehouseId() {
    for (var i = 0; i < meta.warehouses.length; i++) {
      if (meta.warehouses[i].is_default) return meta.warehouses[i].id;
    }
    return meta.warehouses[0] ? meta.warehouses[0].id : "";
  }

  function filtersQuery() {
    var parts = [];
    Object.keys(listFilters).forEach(function (k) {
      if (listFilters[k]) parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(listFilters[k]));
    });
    return parts.length ? "?" + parts.join("&") : "";
  }

  function readFilterPanel(root) {
    var out = {};
    root.querySelectorAll("[data-filter]").forEach(function (el) {
      var key = el.getAttribute("data-filter");
      var val = el.value.trim();
      if (val) out[key] = val;
    });
    return out;
  }

  function filterField(key, label, type, items) {
    var val = esc(listFilters[key] || "");
    if (type === "select") {
      var opts = '<option value="">—</option>';
      (items || []).forEach(function (it) {
        var sel = String(listFilters[key] || "") === String(it.id) ? " selected" : "";
        opts += '<option value="' + esc(it.id) + '"' + sel + ">" + esc(it.name) + "</option>";
      });
      return '<div><label>' + esc(label) + '</label><select data-filter="' + key + '">' + opts + "</select></div>";
    }
    return '<div><label>' + esc(label) + '</label><input type="text" data-filter="' + key + '" value="' + val + '" /></div>';
  }

  function renderFilterPanel() {
    return (
      '<div class="wh-crm-filters' + (filterPanelOpen ? "" : " hidden") + '" id="whWoFilters">' +
      '<div class="wh-crm-filter-grid">' +
      filterField("title", "Название", "text") +
      filterField("comment", "Комментарий", "text") +
      filterField("warehouse_id", "Склад", "select", meta.warehouses) +
      "</div>" +
      '<div class="wh-form-actions">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whWoApplyFilter">Применить</button>' +
      '<button type="button" class="wh-btn" id="whWoResetFilter">Сбросить</button>' +
      "</div></div>"
    );
  }

  function productThumbCell(imageUrl, inline) {
    var u = String(imageUrl || "").trim();
    if (!u) {
      return inline ? '<span class="wh-cat-thumb-inline"></span>' : '<td class="wh-cat-thumb-cell"></td>';
    }
    var img =
      '<img class="wh-cat-thumb" src="' + esc(u) + '" alt="" loading="lazy" onerror="this.remove()" />';
    return inline
      ? '<span class="wh-cat-thumb-inline">' + img + "</span>"
      : '<td class="wh-cat-thumb-cell">' + img + "</td>";
  }

  function parseNum(s) {
    var v = String(s || "").trim().replace(",", ".");
    if (!v) return null;
    var n = parseFloat(v);
    return isNaN(n) ? null : n;
  }

  function formatMoney(n) {
    if (n === null || n === undefined || isNaN(n)) return "";
    return n.toFixed(2);
  }

  function syncLineFromPrice(rowEl) {
    var qty = parseInt(rowEl.querySelector(".wh-rc-qty").value, 10) || 1;
    var price = parseNum(rowEl.querySelector(".wh-rc-price").value);
    var sumEl = rowEl.querySelector(".wh-rc-sum");
    if (price !== null) sumEl.value = formatMoney(price * qty);
  }

  function syncLineFromSum(rowEl) {
    var qty = parseInt(rowEl.querySelector(".wh-rc-qty").value, 10) || 1;
    var sum = parseNum(rowEl.querySelector(".wh-rc-sum").value);
    var priceEl = rowEl.querySelector(".wh-rc-price");
    if (sum !== null && qty > 0) priceEl.value = formatMoney(sum / qty);
  }

  function renderItemRow(item, index) {
    item = item || {};
    var kitBtn = item.is_kit
      ? '<button type="button" class="wh-btn wh-btn-sm wh-rc-split-kit" data-index="' + index + '">Разбить на компоненты</button>'
      : "";
    return (
      '<div class="wh-rc-item-row" data-index="' + index + '" data-product-id="' + esc(item.product_id || "") + '">' +
      productThumbCell(item.image_url, true) +
      '<div class="wh-rc-item-main">' +
      '<div class="wh-rc-item-title">' + esc(item.name || "") + "</div>" +
      '<div class="wh-rc-item-meta"><code>' + esc(item.sku || "") + "</code> · " + esc(item.code || "") + "</div>" +
      "</div>" +
      '<label class="wh-rc-field">Кол-во <input type="number" class="wh-rc-qty" min="1" value="' + esc(item.quantity || 1) + '" /></label>' +
      '<label class="wh-rc-field">Цена <input type="text" class="wh-rc-price" value="' + esc(item.unit_price || "") + '" inputmode="decimal" placeholder="—" /></label>' +
      '<label class="wh-rc-field">Сумма <input type="text" class="wh-rc-sum" value="' + esc(item.line_sum || "") + '" inputmode="decimal" placeholder="—" /></label>' +
      kitBtn +
      '<button type="button" class="wh-btn wh-btn-sm wh-rc-remove-item" title="Удалить">&times;</button></div>'
    );
  }

  function renderItemsBlock() {
    if (!formItems.length) {
      return '<p class="wh-msg" id="whWoItemsEmpty">Товары не добавлены.</p>';
    }
    return formItems.map(renderItemRow).join("");
  }

  function warehouseOptions(selectedId) {
    var html = '<option value="">— выберите склад —</option>';
    meta.warehouses.forEach(function (w) {
      var sel = String(selectedId || "") === String(w.id) ? " selected" : "";
      html += '<option value="' + esc(w.id) + '"' + sel + ">" + esc(w.name) + "</option>";
    });
    return html;
  }

  function priceTypeMenuHtml() {
    if (!meta.price_types.length) {
      return '<p class="wh-msg">Нет видов цен.</p>';
    }
    return meta.price_types
      .map(function (pt) {
        return (
          '<button type="button" class="wh-rc-price-type-opt" data-price-type-id="' + esc(pt.id) + '">' +
          esc(pt.name) + "</button>"
        );
      })
      .join("");
  }

  function bindItemRowEvents(root) {
    root.querySelectorAll(".wh-rc-item-row").forEach(function (rowEl) {
      var priceInp = rowEl.querySelector(".wh-rc-price");
      var sumInp = rowEl.querySelector(".wh-rc-sum");
      var qtyInp = rowEl.querySelector(".wh-rc-qty");
      priceInp.addEventListener("input", function () {
        syncLineFromPrice(rowEl);
      });
      sumInp.addEventListener("input", function () {
        syncLineFromSum(rowEl);
      });
      qtyInp.addEventListener("input", function () {
        if (parseNum(priceInp.value) !== null) syncLineFromPrice(rowEl);
        else if (parseNum(sumInp.value) !== null) syncLineFromSum(rowEl);
      });
    });
    root.querySelectorAll(".wh-rc-remove-item").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var idx = parseInt(btn.closest(".wh-rc-item-row").getAttribute("data-index"), 10);
        formItems.splice(idx, 1);
        refreshItemsDom(root);
      });
    });
    root.querySelectorAll(".wh-rc-split-kit").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var idx = parseInt(btn.getAttribute("data-index"), 10);
        var item = formItems[idx];
        if (!item) return;
        fetchJson("/api/warehouse/writeoffs/expand-kit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ product_id: item.product_id, quantity: item.quantity || 1 }),
        })
          .then(function (data) {
            var lines = (data.items || []).map(function (line) {
              return {
                product_id: line.product_id,
                name: line.name,
                sku: line.sku,
                code: line.code,
                image_url: line.image_url,
                is_kit: line.is_kit,
                quantity: line.quantity,
                unit_price: "",
                line_sum: "",
              };
            });
            formItems.splice(idx, 1);
            lines.forEach(function (line) {
              var exists = -1;
              for (var i = 0; i < formItems.length; i++) {
                if (formItems[i].product_id === line.product_id) {
                  exists = i;
                  break;
                }
              }
              if (exists >= 0) {
                formItems[exists].quantity = (parseInt(formItems[exists].quantity, 10) || 0) + line.quantity;
              } else {
                formItems.push(line);
              }
            });
            refreshItemsDom(root);
          })
          .catch(function (err) {
            alert(err.message || "Не удалось разбить комплект");
          });
      });
    });
  }

  function refreshItemsDom(root) {
    var wrap = root.querySelector("#whWoItems");
    if (!wrap) return;
    wrap.innerHTML = renderItemsBlock();
    bindItemRowEvents(root);
  }

  function collectItemsFromDom(root) {
    var items = [];
    root.querySelectorAll(".wh-rc-item-row").forEach(function (rowEl) {
      var productId = parseInt(rowEl.getAttribute("data-product-id"), 10);
      if (!productId) return;
      items.push({
        product_id: productId,
        quantity: parseInt(rowEl.querySelector(".wh-rc-qty").value, 10) || 1,
        unit_price: rowEl.querySelector(".wh-rc-price").value.trim(),
        line_sum: rowEl.querySelector(".wh-rc-sum").value.trim(),
      });
    });
    return items;
  }

  function addProductToForm(root, product) {
    for (var i = 0; i < formItems.length; i++) {
      if (formItems[i].product_id === product.id) {
        alert("Этот товар уже добавлен");
        return;
      }
    }
    formItems.push({
      product_id: product.id,
      name: product.name,
      sku: product.sku,
      code: product.code,
      image_url: product.image_url,
      is_kit: product.is_kit,
      quantity: 1,
      unit_price: "",
      line_sum: "",
    });
    refreshItemsDom(root);
  }

  function runProductSearch(root) {
    var name = root.querySelector("#whWoSearchName").value.trim();
    var sku = root.querySelector("#whWoSearchSku").value.trim();
    var code = root.querySelector("#whWoSearchCode").value.trim();
    if (!name && !sku && !code) {
      root.querySelector("#whWoSearchResults").innerHTML = '<p class="wh-msg">Укажите хотя бы один параметр поиска.</p>';
      return;
    }
    var url =
      "/api/warehouse/writeoffs/products/search?name=" + encodeURIComponent(name) +
      "&sku=" + encodeURIComponent(sku) +
      "&code=" + encodeURIComponent(code);
    fetchJson(url)
      .then(function (data) {
        var list = data.products || [];
        var wrap = root.querySelector("#whWoSearchResults");
        if (!list.length) {
          wrap.innerHTML = '<p class="wh-msg">Ничего не найдено.</p>';
          return;
        }
        wrap.innerHTML = list
          .map(function (p) {
            var tag = p.is_kit ? ' <span class="wh-badge wh-badge-admin">комплект</span>' : "";
            return (
              '<button type="button" class="wh-rc-search-item">' +
              productThumbCell(p.image_url, true) +
              '<span class="wh-rc-search-text">' + esc(p.name) + " · <code>" + esc(p.sku) + "</code> · " + esc(p.code) + tag + "</span></button>"
            );
          })
          .join("");
        wrap.querySelectorAll(".wh-rc-search-item").forEach(function (btn, i) {
          btn.addEventListener("click", function () {
            addProductToForm(root, list[i]);
          });
        });
      })
      .catch(function (err) {
        root.querySelector("#whWoSearchResults").innerHTML =
          '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function applyPriceType(root, priceTypeId) {
    var items = collectItemsFromDom(root);
    if (!items.length) return;
    var ids = items.map(function (it) {
      return it.product_id;
    });
    fetchJson("/api/warehouse/writeoffs/price-by-type", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ price_type_id: priceTypeId, product_ids: ids }),
    })
      .then(function (data) {
        var prices = data.prices || {};
        root.querySelectorAll(".wh-rc-item-row").forEach(function (rowEl) {
          var pid = String(rowEl.getAttribute("data-product-id"));
          if (!prices[pid]) return;
          rowEl.querySelector(".wh-rc-price").value = prices[pid];
          syncLineFromPrice(rowEl);
        });
        var menu = root.querySelector("#whWoPriceMenu");
        if (menu) menu.classList.add("hidden");
      })
      .catch(function (err) {
        alert(err.message || "Не удалось расценить");
      });
  }

  function renderList() {
    var root = panelEl();
    root.innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    fetchJson("/api/warehouse/writeoffs" + filtersQuery())
      .then(function (data) {
        var rows = (data.writeoffs || [])
          .map(function (r) {
            return (
              '<tr data-id="' + esc(r.id) + '">' +
              "<td>" + esc(r.display_name) + "</td>" +
              "<td>" + esc(r.total_quantity) + "</td>" +
              "<td>" + esc(r.warehouse_name || "—") + "</td>" +
              "<td>" + esc(r.comment_short || "—") + "</td>" +
              "<td>" + esc(formatTs(r.created_at_ts)) + "</td></tr>"
            );
          })
          .join("");
        root.innerHTML =
          '<div class="wh-crm-toolbar">' +
          '<input type="search" id="whWoQuickSearch" class="wh-crm-search" placeholder="Быстрый поиск…" value="' + esc(listFilters.q || "") + '" />' +
          '<button type="button" class="wh-btn" id="whWoToggleFilter">Фильтр</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whWoCreate">+ Списание</button>' +
          "</div>" +
          renderFilterPanel() +
          '<div id="whWoListWrap">' +
          (rows
            ? '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
              "<th>Название</th><th>Кол-во</th><th>Склад</th><th>Комментарий</th><th>Дата</th>" +
              "</tr></thead><tbody>" + rows + "</tbody></table>"
            : '<p class="wh-msg">Списания не найдены.</p>') +
          "</div>";
        bindListEvents(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function bindListEvents(root) {
    root.querySelector("#whWoCreate").addEventListener("click", function () {
      editingId = null;
      formItems = [];
      renderForm(null);
    });
    root.querySelector("#whWoToggleFilter").addEventListener("click", function () {
      filterPanelOpen = !filterPanelOpen;
      var fp = root.querySelector("#whWoFilters");
      if (fp) fp.classList.toggle("hidden", !filterPanelOpen);
    });
    root.querySelector("#whWoApplyFilter").addEventListener("click", function () {
      listFilters = readFilterPanel(root);
      renderList();
    });
    root.querySelector("#whWoResetFilter").addEventListener("click", function () {
      listFilters = {};
      filterPanelOpen = false;
      renderList();
    });
    root.querySelector("#whWoQuickSearch").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        listFilters.q = e.target.value.trim();
        renderList();
      }
    });
    root.querySelectorAll("tbody tr[data-id]").forEach(function (tr) {
      tr.style.cursor = "pointer";
      tr.addEventListener("click", function () {
        editingId = parseInt(tr.getAttribute("data-id"), 10);
        renderForm(editingId);
      });
    });
  }

  function renderForm(writeoffId) {
    var root = panelEl();
    root.innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    var load = writeoffId
      ? fetchJson("/api/warehouse/writeoffs/" + writeoffId).then(function (d) {
          return d.writeoff;
        })
      : Promise.resolve({
          title: "",
          warehouse_id: defaultWarehouseId(),
          comment: "",
          items: [],
        });
    load
      .then(function (r) {
        formItems = (r.items || []).map(function (it) {
          return {
            product_id: it.product_id,
            name: it.name,
            sku: it.sku,
            code: it.code || "",
            image_url: it.image_url,
            is_kit: it.is_kit,
            quantity: it.quantity,
            unit_price: it.unit_price || "",
            line_sum: it.line_sum || "",
          };
        });
        root.innerHTML =
          '<div class="wh-crm-form-toolbar">' +
          '<button type="button" class="wh-btn" id="whWoBack">&larr; К списку</button>' +
          (writeoffId ? '<button type="button" class="wh-btn wh-btn-danger" id="whWoDelete">Удалить</button>' : "") +
          '<button type="button" class="wh-btn wh-btn-primary" id="whWoSave">Сохранить</button>' +
          "</div>" +
          '<p class="wh-msg" id="whWoFormMsg"></p>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Списание</h4>' +
          '<div class="wh-form-row">' +
          '<div><label>Название</label><input type="text" id="whWoTitle" value="' + esc(r.title) + '" placeholder="Например, поставка от 15.06" /></div>' +
          '<div><label>Склад</label><select id="whWoWarehouse">' + warehouseOptions(r.warehouse_id) + "</select></div>" +
          "</div>" +
          '<p class="wh-muted">Отображается как: <strong>Списание {название}</strong></p></section>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Добавление товаров</h4>' +
          '<div class="wh-rc-search-grid">' +
          '<div><label>Название</label><input type="text" id="whWoSearchName" /></div>' +
          '<div><label>Артикул</label><input type="text" id="whWoSearchSku" /></div>' +
          '<div><label>Код</label><input type="text" id="whWoSearchCode" /></div>' +
          '<div class="wh-rc-search-btn-wrap"><button type="button" class="wh-btn" id="whWoSearchBtn">Найти</button></div>' +
          "</div>" +
          '<div id="whWoSearchResults" class="wh-rc-search-results"></div></section>' +
          '<section class="wh-crm-section"><div class="wh-crm-section-head">' +
          '<h4 class="wh-crm-section-title">Товары</h4>' +
          '<div class="wh-rc-price-type-wrap">' +
          '<button type="button" class="wh-btn wh-btn-sm" id="whWoPriceBtn">Расценить ▾</button>' +
          '<div class="wh-rc-price-menu hidden" id="whWoPriceMenu">' + priceTypeMenuHtml() + "</div></div></div>" +
          '<div id="whWoItems" class="wh-rc-items">' + renderItemsBlock() + "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Комментарий</h4>' +
          '<textarea id="whWoComment" class="wh-rc-comment" rows="3" placeholder="Необязательно">' + esc(r.comment) + "</textarea></section>";

        bindItemRowEvents(root);
        root.querySelector("#whWoBack").addEventListener("click", function () {
          editingId = null;
          formItems = [];
          renderList();
        });
        root.querySelector("#whWoSearchBtn").addEventListener("click", function () {
          runProductSearch(root);
        });
        ["whWoSearchName", "whWoSearchSku", "whWoSearchCode"].forEach(function (id) {
          root.querySelector("#" + id).addEventListener("keydown", function (e) {
            if (e.key === "Enter") runProductSearch(root);
          });
        });
        root.querySelector("#whWoPriceBtn").addEventListener("click", function (e) {
          e.stopPropagation();
          root.querySelector("#whWoPriceMenu").classList.toggle("hidden");
        });
        root.querySelectorAll(".wh-rc-price-type-opt").forEach(function (btn) {
          btn.addEventListener("click", function () {
            applyPriceType(root, parseInt(btn.getAttribute("data-price-type-id"), 10));
          });
        });
        document.addEventListener("click", function closeMenu(e) {
          if (!root.contains(e.target)) return;
          if (!e.target.closest(".wh-rc-price-type-wrap")) {
            var menu = root.querySelector("#whWoPriceMenu");
            if (menu) menu.classList.add("hidden");
          }
        });
        root.querySelector("#whWoSave").addEventListener("click", function () {
          saveWriteoff(root, writeoffId);
        });
        if (writeoffId) {
          root.querySelector("#whWoDelete").addEventListener("click", function () {
            if (!confirm("Удалить списание? Остатки будут скорректированы.")) return;
            fetchJson("/api/warehouse/writeoffs/" + writeoffId, { method: "DELETE" })
              .then(function () {
                editingId = null;
                formItems = [];
                renderList();
              })
              .catch(function (err) {
                var msg = root.querySelector("#whWoFormMsg");
                msg.className = "wh-msg wh-msg-error";
                msg.textContent = err.message;
              });
          });
        }
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function saveWriteoff(root, writeoffId) {
    var msg = root.querySelector("#whWoFormMsg");
    msg.textContent = "";
    msg.className = "wh-msg";
    var body = {
      title: root.querySelector("#whWoTitle").value.trim(),
      warehouse_id: root.querySelector("#whWoWarehouse").value,
      comment: root.querySelector("#whWoComment").value.trim(),
      items: collectItemsFromDom(root),
    };
    var req = writeoffId
      ? fetchJson("/api/warehouse/writeoffs/" + writeoffId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      : fetchJson("/api/warehouse/writeoffs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
    req
      .then(function () {
        msg.className = "wh-msg wh-msg-ok";
        msg.textContent = "Сохранено.";
        if (!writeoffId) {
          editingId = null;
          formItems = [];
          renderList();
        }
      })
      .catch(function (err) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Ошибка сохранения";
      });
  }

  function renderWriteoffs(tab, item) {
    preparePanel(tab, item);
    editingId = null;
    formItems = [];
    listFilters = {};
    filterPanelOpen = false;
    loadMeta()
      .then(function () {
        renderList();
      })
      .catch(function (err) {
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  global.WhWriteoffs = {
    renderWriteoffs: renderWriteoffs,
  };
})(window);
