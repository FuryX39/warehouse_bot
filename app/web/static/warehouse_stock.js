(function (global) {
  var meta = { warehouse_groups: [], product_groups: [], warehouses: [] };
  var listFilters = {};
  var filterPanelOpen = false;
  var viewMode = "products";
  var expanded = null;
  var breakdownCache = {};

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
    return fetchJson("/api/warehouse/stock/meta").then(function (data) {
      meta.warehouse_groups = data.warehouse_groups || [];
      meta.product_groups = data.product_groups || [];
      meta.warehouses = data.warehouses || [];
      return meta;
    });
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
      var val = el.type === "checkbox" ? (el.checked ? "1" : "") : el.value.trim();
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
    if (type === "kind") {
      var kindOpts =
        '<option value="">—</option>' +
        '<option value="product"' + (listFilters.kind === "product" ? " selected" : "") + ">Товар</option>" +
        '<option value="kit"' + (listFilters.kind === "kit" ? " selected" : "") + ">Комплект</option>";
      return '<div><label>' + esc(label) + '</label><select data-filter="kind">' + kindOpts + "</select></div>";
    }
    if (type === "checkbox") {
      var checked = listFilters[key] === "1" ? " checked" : "";
      return (
        '<div class="wh-stock-filter-check">' +
        '<label><input type="checkbox" data-filter="' + key + '"' + checked + " /> " + esc(label) + "</label></div>"
      );
    }
    return '<div><label>' + esc(label) + '</label><input type="text" data-filter="' + key + '" value="' + val + '" /></div>';
  }

  function renderFilterPanel() {
    var whFields =
      viewMode === "warehouses"
        ? filterField("warehouse_name", "Склад", "text") +
          filterField("warehouse_code", "Код склада", "text") +
          filterField("warehouse_group_id", "Группа складов", "select", meta.warehouse_groups) +
          filterField("warehouse_id", "Склад (точно)", "select", meta.warehouses) +
          filterField("hide_empty", "Скрывать пустые склады", "checkbox")
        : "";
    return (
      '<div class="wh-crm-filters' + (filterPanelOpen ? "" : " hidden") + '" id="whStockFilters">' +
      '<div class="wh-crm-filter-grid">' +
      filterField("kind", "Тип", "kind") +
      filterField("name", "Название", "text") +
      filterField("sku", "Артикул", "text") +
      filterField("code", "Код", "text") +
      filterField("group_id", "Группа товаров", "select", meta.product_groups) +
      filterField("warehouse_id", "Склад (остаток)", "select", meta.warehouses) +
      filterField("only_nonzero", "Только ненулевые", "checkbox") +
      whFields +
      "</div>" +
      '<div class="wh-crm-filter-actions">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whStockApplyFilter">Применить</button>' +
      '<button type="button" class="wh-btn" id="whStockResetFilter">Сбросить</button>' +
      "</div></div>"
    );
  }

  function metricLabel(metric) {
    if (metric === "reserve") return "Резерв";
    if (metric === "free") return "Свободный остаток";
    return "Полный остаток";
  }

  function productThumbCell(imageUrl) {
    var u = String(imageUrl || "").trim();
    if (!u) return '<td class="wh-cat-thumb-cell"></td>';
    return (
      '<td class="wh-cat-thumb-cell">' +
      '<img class="wh-cat-thumb" src="' + esc(u) + '" alt="" loading="lazy" ' +
      'onerror="this.remove()" /></td>'
    );
  }

  function qtyCell(sku, metric, value) {
    var cls = "wh-stock-qty";
    if (metric === "free" && Number(value) < 0) cls += " wh-stock-qty--negative";
    return (
      '<span class="' + cls + '">' + esc(value) + "</span>" +
      '<button type="button" class="wh-stock-drill" data-sku="' + esc(sku) + '" data-metric="' + esc(metric) + '" title="Детализация">🔍</button>'
    );
  }

  function renderProductsTable(items) {
    var rows = (items || [])
      .map(function (it) {
        var typeLabel = it.is_kit ? "Комплект" : "Товар";
        return (
          '<tr data-sku="' + esc(it.sku) + '">' +
          productThumbCell(it.image_url) +
          "<td>" + esc(it.name) + "</td>" +
          "<td>" + esc(typeLabel) + "</td>" +
          "<td>" + esc(it.sku) + "</td>" +
          "<td>" + esc(it.code || "—") + "</td>" +
          "<td>" + esc(it.group_name || "—") + "</td>" +
          "<td class=\"wh-stock-num\">" + qtyCell(it.sku, "full", it.full_stock) + "</td>" +
          "<td class=\"wh-stock-num\">" + qtyCell(it.sku, "reserve", it.reserve) + "</td>" +
          "<td class=\"wh-stock-num\">" + qtyCell(it.sku, "free", it.free_stock) + "</td>" +
          "</tr>"
        );
      })
      .join("");
    return (
      '<table class="wh-employees-table wh-crm-table wh-stock-table"><thead><tr>' +
      "<th></th><th>Название</th><th>Тип</th><th>Артикул</th><th>Код</th><th>Группа</th>" +
      "<th>Полный остаток</th><th>Резерв</th><th>Свободный остаток</th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table>"
    );
  }

  function renderWarehouseBlocks(warehouses) {
    if (!warehouses || !warehouses.length) {
      return '<p class="wh-msg">Нет данных по складам.</p>';
    }
    return warehouses
      .map(function (wh) {
        var lines = (wh.lines || [])
          .map(function (line) {
            return (
              '<tr data-sku="' + esc(line.sku) + '">' +
              productThumbCell(line.image_url) +
              "<td>" + esc(line.name) + "</td>" +
              "<td>" + (line.is_kit ? "Комплект" : "Товар") + "</td>" +
              "<td>" + esc(line.sku) + "</td>" +
              "<td>" + esc(line.code || "—") + "</td>" +
              "<td class=\"wh-stock-num\">" + esc(line.warehouse_stock) + "</td>" +
              "<td class=\"wh-stock-num\">" + qtyCell(line.sku, "full", line.full_stock) + "</td>" +
              "<td class=\"wh-stock-num\">" + qtyCell(line.sku, "reserve", line.reserve) + "</td>" +
              "<td class=\"wh-stock-num\">" + qtyCell(line.sku, "free", line.free_stock) + "</td>" +
              "</tr>"
            );
          })
          .join("");
        return (
          '<section class="wh-stock-wh-block">' +
          '<h3 class="wh-stock-wh-title">' + esc(wh.warehouse_name) + " <span class=\"wh-muted\">(" + esc(wh.warehouse_code) + ")</span></h3>" +
          '<table class="wh-employees-table wh-crm-table wh-stock-table"><thead><tr>' +
          "<th></th><th>Название</th><th>Тип</th><th>Артикул</th><th>Код</th><th>На складе</th>" +
          "<th>Полный остаток</th><th>Резерв</th><th>Свободный остаток</th>" +
          "</tr></thead><tbody>" + lines + "</tbody></table></section>"
        );
      })
      .join("");
  }

  function breakdownKey(sku, metric) {
    return sku + "|" + metric;
  }

  function renderBreakdownHtml(data) {
    var metric = data.metric;
    var lines = data.lines || [];
    if (!lines.length) {
      return '<div class="wh-stock-breakdown wh-stock-breakdown--empty">Нет данных</div>';
    }
    if (metric === "reserve") {
      var reserveRows = lines
        .map(function (line) {
          return (
            "<li><span class=\"wh-stock-bd-source\">" + esc(line.source) + "</span> " +
            "<span class=\"wh-stock-bd-order\">" + esc(line.external_order_id) + "</span>: " +
            "<strong>" + esc(line.quantity) + "</strong></li>"
          );
        })
        .join("");
      return (
        '<div class="wh-stock-breakdown">' +
        '<div class="wh-stock-breakdown-title">' + esc(metricLabel(metric)) + " — детализация</div>" +
        "<ul class=\"wh-stock-breakdown-list\">" + reserveRows + "</ul>" +
        '<div class="wh-stock-breakdown-total">Итого: <strong>' + esc(data.total) + "</strong></div></div>"
      );
    }
    if (metric === "full") {
      var fullRows = lines
        .map(function (line) {
          return (
            "<li>" + esc(line.warehouse_name) + " (" + esc(line.quantity) + ")</li>"
          );
        })
        .join("");
      return (
        '<div class="wh-stock-breakdown">' +
        '<div class="wh-stock-breakdown-title">' + esc(metricLabel(metric)) + " — по складам</div>" +
        "<ul class=\"wh-stock-breakdown-list\">" + fullRows + "</ul>" +
        '<div class="wh-stock-breakdown-total">Итого: <strong>' + esc(data.total) + "</strong></div></div>"
      );
    }
    var freeRows = lines
      .map(function (line) {
        return (
          "<li>" + esc(line.warehouse_name) + ": остаток " + esc(line.stock) +
          ", свободно " + esc(line.free) + "</li>"
        );
      })
      .join("");
    var note = data.note ? '<p class="wh-muted wh-stock-breakdown-note">' + esc(data.note) + "</p>" : "";
    return (
      '<div class="wh-stock-breakdown">' +
      '<div class="wh-stock-breakdown-title">' + esc(metricLabel(metric)) + " — по складам</div>" +
      note +
      "<ul class=\"wh-stock-breakdown-list\">" + freeRows + "</ul>" +
      '<div class="wh-stock-breakdown-total">Итого свободно: <strong>' + esc(data.total) + "</strong>" +
      (data.reserve_total != null ? " (резерв по товару: " + esc(data.reserve_total) + ")" : "") +
      "</div></div>"
    );
  }

  function removeBreakdownRows(root) {
    root.querySelectorAll(".wh-stock-breakdown-row").forEach(function (row) {
      row.remove();
    });
  }

  function insertBreakdownRow(anchorTr, html) {
    var colCount = anchorTr.children.length;
    var tr = document.createElement("tr");
    tr.className = "wh-stock-breakdown-row";
    tr.innerHTML = '<td colspan="' + colCount + '">' + html + "</td>";
    anchorTr.parentNode.insertBefore(tr, anchorTr.nextSibling);
  }

  function toggleBreakdown(root, btn) {
    var sku = btn.getAttribute("data-sku");
    var metric = btn.getAttribute("data-metric");
    var key = breakdownKey(sku, metric);
    var anchorTr = btn.closest("tr");
    if (!anchorTr) return;

    if (expanded === key) {
      expanded = null;
      removeBreakdownRows(root);
      return;
    }

    expanded = key;
    removeBreakdownRows(root);

    function show(html) {
      insertBreakdownRow(anchorTr, html);
    }

    if (breakdownCache[key]) {
      show(renderBreakdownHtml(breakdownCache[key]));
      return;
    }

    show('<div class="wh-stock-breakdown">Загрузка…</div>');
    fetchJson("/api/warehouse/stock/breakdown/" + encodeURIComponent(sku) + "?metric=" + encodeURIComponent(metric))
      .then(function (data) {
        breakdownCache[key] = data;
        removeBreakdownRows(root);
        if (expanded !== key) return;
        insertBreakdownRow(anchorTr, renderBreakdownHtml(data));
      })
      .catch(function (err) {
        removeBreakdownRows(root);
        if (expanded !== key) return;
        insertBreakdownRow(anchorTr, '<div class="wh-stock-breakdown wh-msg-error">' + esc(err.message) + "</div>");
      });
  }

  function bindListEvents(root) {
    root.querySelector("#whStockToggleFilter").addEventListener("click", function () {
      filterPanelOpen = !filterPanelOpen;
      var fp = root.querySelector("#whStockFilters");
      if (fp) fp.classList.toggle("hidden", !filterPanelOpen);
    });
    root.querySelector("#whStockApplyFilter").addEventListener("click", function () {
      listFilters = readFilterPanel(root);
      expanded = null;
      breakdownCache = {};
      renderList();
    });
    root.querySelector("#whStockResetFilter").addEventListener("click", function () {
      listFilters = {};
      filterPanelOpen = false;
      expanded = null;
      breakdownCache = {};
      renderList();
    });
    var quick = root.querySelector("#whStockQuickSearch");
    if (quick) {
      quick.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          listFilters.q = quick.value.trim();
          expanded = null;
          breakdownCache = {};
          renderList();
        }
      });
    }
    root.querySelectorAll(".wh-stock-view-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var mode = btn.getAttribute("data-view");
        if (mode === viewMode) return;
        viewMode = mode;
        expanded = null;
        breakdownCache = {};
        renderList();
      });
    });
    root.addEventListener("click", function (e) {
      var btn = e.target.closest(".wh-stock-drill");
      if (!btn || !root.contains(btn)) return;
      e.preventDefault();
      toggleBreakdown(root, btn);
    });
  }

  function renderList() {
    var root = panelEl();
    root.innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    var url =
      viewMode === "warehouses"
        ? "/api/warehouse/stock/warehouses" + filtersQuery()
        : "/api/warehouse/stock/products" + filtersQuery();
    fetchJson(url)
      .then(function (data) {
        var productsActive = viewMode === "products" ? " wh-btn-primary" : "";
        var warehousesActive = viewMode === "warehouses" ? " wh-btn-primary" : "";
        root.innerHTML =
          '<div class="wh-crm-toolbar">' +
          '<input type="search" id="whStockQuickSearch" class="wh-crm-search" placeholder="Быстрый поиск…" value="' + esc(listFilters.q || "") + '" />' +
          '<button type="button" class="wh-btn" id="whStockToggleFilter">Фильтр</button>' +
          '<div class="wh-stock-view-toggle">' +
          '<button type="button" class="wh-btn wh-stock-view-btn' + productsActive + '" data-view="products">По товарам</button>' +
          '<button type="button" class="wh-btn wh-stock-view-btn' + warehousesActive + '" data-view="warehouses">По складам</button>' +
          "</div></div>" +
          renderFilterPanel() +
          '<div id="whStockListWrap"></div>';
        var wrap = root.querySelector("#whStockListWrap");
        if (viewMode === "warehouses") {
          wrap.innerHTML = renderWarehouseBlocks(data.warehouses || []);
        } else {
          wrap.innerHTML = renderProductsTable(data.items || []);
        }
        bindListEvents(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function renderStock(tab, item) {
    preparePanel(tab, item);
    listFilters = {};
    filterPanelOpen = false;
    viewMode = "products";
    expanded = null;
    breakdownCache = {};
    loadMeta().then(renderList).catch(function (err) {
      panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
    });
  }

  global.WhStock = {
    renderStock: renderStock,
  };
})(window);
