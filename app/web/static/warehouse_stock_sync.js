(function (global) {
  var state = {
    meta: null,
    items: [],
    sort: { col: "sku", dir: 1 },
    search: "",
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

  function formBody(data) {
    var params = new URLSearchParams();
    Object.keys(data || {}).forEach(function (key) {
      params.set(key, String(data[key]));
    });
    return params.toString();
  }

  function mpCheckboxes(items) {
    var rows = items || [];
    if (!rows.length) {
      return '<p class="wh-muted">Маркетплейсы не заданы.</p>';
    }
    return (
      '<div class="wh-stock-sync-mp-list">' +
      rows
        .map(function (mp) {
          var checked = mp.sync_enabled ? " checked" : "";
          var disabled = mp.configured ? "" : " disabled";
          var hint = mp.configured ? "токен задан" : "токен не задан";
          return (
            '<label class="wh-stock-sync-mp-item">' +
            '<input type="checkbox" data-mp-sync="' +
            esc(mp.name) +
            '"' +
            checked +
            disabled +
            " /> " +
            esc(mp.title) +
            ' <span class="wh-muted">(' +
            hint +
            ")</span></label>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  function warehouseOptions(selectedId) {
    var warehouses = (state.meta && state.meta.warehouses) || [];
    return warehouses
      .map(function (wh) {
        var sel = String(selectedId || "") === String(wh.id) ? " selected" : "";
        var label = wh.name + (wh.code ? " (" + wh.code + ")" : "");
        if (wh.is_default) label += " · основной";
        return '<option value="' + esc(wh.id) + '"' + sel + ">" + esc(label) + "</option>";
      })
      .join("");
  }

  function filteredItems() {
    var q = String(state.search || "").trim().toLowerCase();
    var rows = state.items.slice();
    if (q) {
      rows = rows.filter(function (row) {
        return (
          String(row.sku || "").toLowerCase().indexOf(q) !== -1 ||
          String(row.name || "").toLowerCase().indexOf(q) !== -1
        );
      });
    }
    var col = state.sort.col;
    var dir = state.sort.dir;
    rows.sort(function (a, b) {
      var av = a[col];
      var bv = b[col];
      if (typeof av === "boolean") av = av ? 1 : 0;
      if (typeof bv === "boolean") bv = bv ? 1 : 0;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av || "").localeCompare(String(bv || ""), "ru") * dir;
    });
    return rows;
  }

  function renderInventoryTable() {
    var body = panelEl().querySelector("#whStockSyncBody");
    var meta = panelEl().querySelector("#whStockSyncInventoryMeta");
    if (!body) return;
    var rows = filteredItems();
    if (meta) meta.textContent = "Строк: " + rows.length + " / " + state.items.length;
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="7" class="wh-muted">Нет остатков для отображения.</td></tr>';
      return;
    }
    body.innerHTML = rows
      .map(function (row) {
        return (
          "<tr>" +
          "<td><code>" +
          esc(row.sku) +
          "</code></td>" +
          "<td>" +
          esc(row.name) +
          "</td>" +
          '<td class="num">' +
          esc(row.stock) +
          "</td>" +
          '<td class="num">' +
          esc(row.reserve) +
          "</td>" +
          '<td class="num">' +
          esc(row.available) +
          "</td>" +
          "<td>" +
          (row.is_top ? "да" : "—") +
          "</td>" +
          "<td>" +
          '<button type="button" class="wh-btn wh-btn-sm wh-stock-sync-edit" data-sku="' +
          esc(row.sku) +
          '" data-stock="' +
          esc(row.stock) +
          '">Изменить</button> ' +
          '<button type="button" class="wh-btn wh-btn-sm wh-stock-sync-del" data-sku="' +
          esc(row.sku) +
          '">Удалить</button>' +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function loadInventory() {
    return fetchJson("/api/warehouse/stock-sync/inventory").then(function (data) {
      state.items = data.items || [];
      renderInventoryTable();
    });
  }

  function loadStatus() {
    var el = panelEl().querySelector("#whStockSyncStatus");
    if (!el) return Promise.resolve();
    el.textContent = "Загрузка…";
    return fetchJson("/api/warehouse/stock-sync/status")
      .then(function (data) {
        el.textContent = JSON.stringify(data, null, 2);
      })
      .catch(function (err) {
        el.textContent = err.message || String(err);
      });
  }

  function bindEvents(root) {
    var warehouseSelect = root.querySelector("#whStockSyncWarehouse");
    var saveWhBtn = root.querySelector("#whStockSyncSaveWarehouse");
    var msg = root.querySelector("#whStockSyncMsg");
    var search = root.querySelector("#whStockSyncSearch");

    if (saveWhBtn && warehouseSelect) {
      saveWhBtn.addEventListener("click", function () {
        var warehouseId = parseInt(warehouseSelect.value || "", 10);
        if (!warehouseId) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = "Выберите склад.";
          return;
        }
        msg.className = "wh-msg";
        msg.textContent = "Сохраняем…";
        var flags = {};
        root.querySelectorAll("[data-mp-sync]").forEach(function (cb) {
          if (cb.disabled) return;
          flags[cb.getAttribute("data-mp-sync")] = !!cb.checked;
        });
        fetchJson("/api/warehouse/stock-sync/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ warehouse_id: warehouseId, marketplace_sync: flags }),
        })
          .then(function (data) {
            state.meta = state.meta || {};
            state.meta.source_warehouse_id = data.source_warehouse_id;
            state.meta.source_warehouse = data.source_warehouse;
            if (data.marketplace_sync && state.meta.marketplace_sync) {
              state.meta.marketplace_sync.forEach(function (mp) {
                if (Object.prototype.hasOwnProperty.call(data.marketplace_sync, mp.name)) {
                  mp.sync_enabled = !!data.marketplace_sync[mp.name];
                }
              });
            }
            msg.className = "wh-msg wh-msg-ok";
            var flagsText = "";
            if (data.marketplace_sync) {
              var off = Object.keys(data.marketplace_sync).filter(function (name) {
                return !data.marketplace_sync[name];
              });
              flagsText = off.length
                ? " Отключены в цикле: " + off.join(", ") + "."
                : " Все указанные МП участвуют в цикле заказов и остатков.";
            }
            msg.textContent =
              "Склад синхронизации: " +
              ((data.source_warehouse && data.source_warehouse.name) || "#" + data.source_warehouse_id) +
              "." +
              flagsText;
            return loadInventory().then(loadStatus);
          })
          .catch(function (err) {
            msg.className = "wh-msg wh-msg-error";
            msg.textContent = err.message || String(err);
          });
      });
    }

    if (search) {
      search.addEventListener("input", function () {
        state.search = search.value || "";
        renderInventoryTable();
      });
    }

    root.querySelector("#whStockSyncReload") &&
      root.querySelector("#whStockSyncReload").addEventListener("click", function () {
        loadInventory().catch(function (err) {
          alert(err.message || String(err));
        });
      });

    root.querySelector("#whStockSyncRefreshStatus") &&
      root.querySelector("#whStockSyncRefreshStatus").addEventListener("click", function () {
        loadStatus();
      });

    root.querySelectorAll("[data-sync-mode]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var mode = btn.getAttribute("data-sync-mode") || "auto";
        var out = root.querySelector("#whStockSyncResult");
        out.textContent = "Выполняется…";
        fetch("/api/warehouse/stock-sync/run", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
          body: formBody({ mode: mode }),
        })
          .then(function (r) {
            return r.json().then(function (data) {
              if (!r.ok) throw new Error((data && data.detail) || "HTTP " + r.status);
              return data;
            });
          })
          .then(function (data) {
            out.textContent = JSON.stringify(data, null, 2);
            if (data && data.ok === false) {
              alert("Ошибка синхронизации: " + ((data && data.error) || "unknown"));
            }
            return loadStatus();
          })
          .catch(function (err) {
            out.textContent = err.message || String(err);
            alert(err.message || String(err));
          });
      });
    });

    root.querySelector("#whStockSyncImport") &&
      root.querySelector("#whStockSyncImport").addEventListener("click", function () {
        var url = (root.querySelector("#whStockSyncSheetUrl").value || "").trim();
        var inventoryMeta = root.querySelector("#whStockSyncInventoryMeta");
        inventoryMeta.textContent = "Импорт из таблицы…";
        fetch("/api/warehouse/stock-sync/import-sheet", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
          body: formBody({ url: url }),
        })
          .then(function (r) {
            return r.json().then(function (data) {
              if (!r.ok) throw new Error((data && data.detail) || "HTTP " + r.status);
              return data;
            });
          })
          .then(function (r) {
            inventoryMeta.textContent =
              "Импорт: обновлено " +
              (r.updated != null ? r.updated : "—") +
              ", SKU в таблице: " +
              (r.sku_in_sheet != null ? r.sku_in_sheet : "—");
            return loadInventory();
          })
          .catch(function (err) {
            inventoryMeta.textContent = err.message || String(err);
            alert(err.message || String(err));
          });
      });

    root.querySelector("#whStockSyncImportTops") &&
      root.querySelector("#whStockSyncImportTops").addEventListener("click", function () {
        var url = (root.querySelector("#whStockSyncSheetUrl").value || "").trim();
        var inventoryMeta = root.querySelector("#whStockSyncInventoryMeta");
        inventoryMeta.textContent = "Импорт топов…";
        fetch("/api/warehouse/stock-sync/import-tops", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
          body: formBody({ url: url }),
        })
          .then(function (r) {
            return r.json().then(function (data) {
              if (!r.ok) throw new Error((data && data.detail) || "HTTP " + r.status);
              return data;
            });
          })
          .then(function (r) {
            inventoryMeta.textContent =
              "Топы: в листе " +
              (r.sheet_skus != null ? r.sheet_skus : "—") +
              ", помечено " +
              (r.marked_top_existing != null ? r.marked_top_existing : "—");
            return loadInventory();
          })
          .catch(function (err) {
            inventoryMeta.textContent = err.message || String(err);
            alert(err.message || String(err));
          });
      });

    root.querySelector("#whStockSyncSet") &&
      root.querySelector("#whStockSyncSet").addEventListener("click", function () {
        var sku = prompt("Артикул (SKU)");
        if (!sku) return;
        var stockRaw = prompt("Остаток", "0");
        if (stockRaw == null) return;
        var stock = parseInt(stockRaw, 10);
        if (isNaN(stock) || stock < 0) {
          alert("Остаток должен быть числом ≥ 0");
          return;
        }
        fetch("/api/warehouse/stock-sync/stock", {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
          body: formBody({ sku: sku, stock: stock }),
        })
          .then(function (r) {
            return r.json().then(function (data) {
              if (!r.ok) throw new Error((data && data.detail) || "HTTP " + r.status);
              return data;
            });
          })
          .then(function () {
            return loadInventory();
          })
          .catch(function (err) {
            alert(err.message || String(err));
          });
      });

    root.addEventListener("click", function (e) {
      var editBtn = e.target.closest(".wh-stock-sync-edit");
      if (editBtn) {
        var sku = editBtn.getAttribute("data-sku");
        var current = editBtn.getAttribute("data-stock") || "0";
        var stockRaw = prompt("Новый остаток для " + sku, current);
        if (stockRaw == null) return;
        var stock = parseInt(stockRaw, 10);
        if (isNaN(stock) || stock < 0) {
          alert("Остаток должен быть числом ≥ 0");
          return;
        }
        fetch("/api/warehouse/stock-sync/stock", {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
          body: formBody({ sku: sku, stock: stock }),
        })
          .then(function (r) {
            return r.json().then(function (data) {
              if (!r.ok) throw new Error((data && data.detail) || "HTTP " + r.status);
              return data;
            });
          })
          .then(function () {
            return loadInventory();
          })
          .catch(function (err) {
            alert(err.message || String(err));
          });
        return;
      }
      var delBtn = e.target.closest(".wh-stock-sync-del");
      if (delBtn) {
        var delSku = delBtn.getAttribute("data-sku");
        if (!confirm("Удалить остаток " + delSku + "?")) return;
        fetch("/api/warehouse/stock-sync/stock?sku=" + encodeURIComponent(delSku), {
          method: "DELETE",
          credentials: "include",
        })
          .then(function (r) {
            return r.json().then(function (data) {
              if (!r.ok) throw new Error((data && data.detail) || "HTTP " + r.status);
              return data;
            });
          })
          .then(function () {
            return loadInventory();
          })
          .catch(function (err) {
            alert(err.message || String(err));
          });
      }
    });

    root.querySelector("#whStockSyncHead") &&
      root.querySelector("#whStockSyncHead").addEventListener("click", function (e) {
        var th = e.target.closest("[data-col]");
        if (!th) return;
        var col = th.getAttribute("data-col");
        if (!col) return;
        if (state.sort.col === col) state.sort.dir = -state.sort.dir;
        else {
          state.sort.col = col;
          state.sort.dir = 1;
        }
        renderInventoryTable();
      });
  }

  function render(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-placeholder">Загрузка...</p>';
    fetchJson("/api/warehouse/stock-sync/meta")
      .then(function (meta) {
        state.meta = meta || {};
        var enabled = meta.stock_sync_enabled ? "включена" : "выключена (STOCK_SYNC_ENABLED)";
        var sourceName =
          (meta.source_warehouse && meta.source_warehouse.name) ||
          (meta.source_warehouse_id ? "#" + meta.source_warehouse_id : "не выбран");
        root.innerHTML =
          '<div class="wh-stock-sync">' +
          '<div class="wh-tools-card">' +
          "<h3>Склад-источник для маркетплейсов</h3>" +
          '<p class="wh-muted">Остатки с выбранного склада уходят в Ozon / Wildberries / Яндекс Маркет. Сейчас: <b>' +
          esc(sourceName) +
          "</b>. Пуш остатков: " +
          esc(enabled) +
          ". Автоцикл резервов — процесс run_sync.py.</p>" +
          '<div class="wh-stock-sync-row">' +
          '<label>Склад <select id="whStockSyncWarehouse">' +
          warehouseOptions(meta.source_warehouse_id) +
          "</select></label>" +
          '<button type="button" class="wh-btn wh-btn-primary" id="whStockSyncSaveWarehouse">Сохранить</button>' +
          '<span id="whStockSyncMsg" class="wh-msg"></span>' +
          "</div>" +
          '<div class="wh-stock-sync-mp">' +
          "<h4>Маркетплейсы в цикле заказов и остатков</h4>" +
          '<p class="wh-muted">Снимите галочку — заказы и пуш остатков с этого МП не идут. Токен в .env не трогается: FBS, ярлыки и прочее продолжают работать.</p>' +
          mpCheckboxes(meta.marketplace_sync) +
          "</div>" +
          "</div>" +
          '<div class="wh-stock-sync-grid">' +
          '<div class="wh-tools-card">' +
          "<h3>Статус синхронизации</h3>" +
          '<p class="wh-muted">Автоцикл — процесс <code>run_sync.py</code> (резервы и пуш). Кнопки справа — ручной запуск.</p>' +
          '<pre id="whStockSyncStatus" class="wh-mono-block">—</pre>' +
          '<button type="button" class="wh-btn" id="whStockSyncRefreshStatus">Обновить статус</button>' +
          "</div>" +
          '<div class="wh-tools-card">' +
          "<h3>Запуск синка</h3>" +
          '<p class="wh-muted">Режимы: auto / delta / full.</p>' +
          '<div class="wh-stock-sync-row">' +
          '<button type="button" class="wh-btn wh-btn-primary" data-sync-mode="auto">auto</button>' +
          '<button type="button" class="wh-btn" data-sync-mode="delta">delta</button>' +
          '<button type="button" class="wh-btn" data-sync-mode="full">full</button>' +
          "</div>" +
          '<pre id="whStockSyncResult" class="wh-mono-block"></pre>' +
          "</div>" +
          "</div>" +
          '<div class="wh-tools-card">' +
          "<h3>Остатки (склад синхронизации)</h3>" +
          '<div class="wh-stock-sync-row">' +
          '<button type="button" class="wh-btn" id="whStockSyncReload">Обновить</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whStockSyncSet">Задать остаток</button>' +
          '<input type="search" id="whStockSyncSearch" class="wh-input" placeholder="Поиск SKU / название" />' +
          '<input type="text" id="whStockSyncSheetUrl" class="wh-input wh-input-wide" placeholder="URL Google Sheets (пусто = DEFAULT_STOCKS_SHEET_URL)" value="' +
          esc(meta.default_stocks_sheet_url || "") +
          '" />' +
          '<button type="button" class="wh-btn" id="whStockSyncImport">Импорт stocks</button>' +
          '<button type="button" class="wh-btn" id="whStockSyncImportTops">Импорт tops</button>' +
          '<span id="whStockSyncInventoryMeta" class="wh-muted"></span>' +
          "</div>" +
          '<div class="wh-table-wrap">' +
          '<table class="wh-employees-table">' +
          '<thead><tr id="whStockSyncHead">' +
          '<th data-col="sku">Артикул</th><th data-col="name">Название</th>' +
          '<th data-col="stock" class="num">На складе</th><th data-col="reserve" class="num">Резерв</th>' +
          '<th data-col="available" class="num">Доступно</th><th data-col="is_top">Топ</th><th></th>' +
          "</tr></thead>" +
          '<tbody id="whStockSyncBody"></tbody>' +
          "</table></div></div></div>";
        bindEvents(root);
        return Promise.all([loadInventory(), loadStatus()]);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message || String(err)) + "</p>";
      });
  }

  global.WhStockSync = { render: render };
})(window);
