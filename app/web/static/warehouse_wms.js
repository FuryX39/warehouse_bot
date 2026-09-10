(function (global) {
  var ordersMeta = { counterparties: [], statuses: [] };
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
          '<button type="button" class="wh-btn" id="whOrdersReload">Обновить</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whOrdersShip">Отгрузить выбранные</button>' +
          "</div>" +
          '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
          "<th></th><th>ЗК</th><th>Контрагент</th><th>Комментарий</th><th>Статус</th><th>Дата заказа</th>" +
          "</tr></thead><tbody id=\"whOrdersBody\"></tbody></table>" +
          '<div id="whOrdersPager"></div>' +
          '<div id="whOrderDetail" class="wh-msg" hidden></div>';

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
              var id = tr.getAttribute("data-id");
              fetchJson("/api/warehouse/orders/" + id).then(function (d) {
                var o = d.order;
                var lines = (o.lines || [])
                  .map(function (ln) {
                    return "<li>" + esc(ln.sku) + " × " + esc(ln.quantity) + " — " + esc(ln.name) + "</li>";
                  })
                  .join("");
                var box = panelEl().querySelector("#whOrderDetail");
                box.hidden = false;
                box.innerHTML =
                  "<h4>" +
                  esc(o.number) +
                  " · " +
                  statusLabel(o.status) +
                  "</h4>" +
                  "<p>Контрагент: " +
                  esc(o.counterparty_name || "—") +
                  "</p>" +
                  "<p>Комментарий: " +
                  esc(o.comment || "—") +
                  "</p>" +
                  "<p>Дата заказа: " +
                  esc(formatTs(o.created_at_ts)) +
                  "</p>" +
                  "<p>Обновлён: " +
                  esc(formatTs(o.updated_at_ts)) +
                  "</p><ul>" +
                  lines +
                  "</ul>";
              });
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
                .then(function () {
                  alert("Отгрузка волны проведена");
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
