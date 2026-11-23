(function (global) {
  var state = { status: "active", days: 7, page: 1, list: null, advertId: 0, detailDays: 30 };

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

  function formatInt(value) {
    if (value == null || value === "") return "—";
    var n = Number(value);
    if (Number.isNaN(n)) return esc(value);
    return Math.round(n).toLocaleString("ru-RU");
  }

  function formatMoney(value) {
    if (value == null || value === "") return "—";
    var n = Number(value);
    if (Number.isNaN(n)) return esc(value);
    return n.toLocaleString("ru-RU", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + " ₽";
  }

  function formatPct(value) {
    if (value == null || value === "") return "—";
    var n = Number(value);
    if (Number.isNaN(n)) return esc(value);
    return n.toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%";
  }

  function statusBadge(row) {
    var status = Number(row && row.status);
    var cls = "wh-badge";
    if (status === 9) cls += " wh-badge-shipped";
    else if (status === 11) cls += " wh-badge-not-shipped";
    else if (status === 7) cls += " wh-badge-inactive";
    else cls += " wh-badge-admin";
    return '<span class="' + cls + '">' + esc((row && row.status_label) || status) + "</span>";
  }

  function kpiHtml(stats) {
    stats = stats || {};
    var items = [
      ["Расход", formatMoney(stats.spend)],
      ["Заказы", formatInt(stats.orders)],
      ["Показы", formatInt(stats.views)],
      ["Клики", formatInt(stats.clicks)],
      ["CTR", formatPct(stats.ctr)],
      ["CPC", stats.cpc != null ? formatMoney(stats.cpc) : "—"],
      ["ДРР", formatPct(stats.drr)],
      ["Сумма заказов", formatMoney(stats.gmv)],
    ];
    return (
      '<div class="wh-bidder-kpis">' +
      items
        .map(function (item) {
          return (
            '<div class="wh-bidder-kpi"><div class="wh-bidder-kpi-label">' +
            esc(item[0]) +
            '</div><div class="wh-bidder-kpi-value">' +
            item[1] +
            "</div></div>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  function toolbarHtml(kind) {
    var days = kind === "detail" ? state.detailDays : state.days;
    return (
      '<div class="wh-job-status-filter-bar wh-bidder-toolbar">' +
      (kind === "detail"
        ? '<button type="button" class="wh-btn" id="whBidderBack">← К списку</button>'
        : '<h4 class="wh-crm-section-title">Кампании WB</h4>') +
      '<div class="wh-bidder-filters">' +
      (kind === "detail"
        ? ""
        : '<label class="wh-job-status-filter-wrap">Статус <select id="whBidderStatus" class="wh-job-status-filter">' +
          '<option value="active"' +
          (state.status === "active" ? " selected" : "") +
          ">Активные и пауза</option>" +
          '<option value="running"' +
          (state.status === "running" ? " selected" : "") +
          ">Идут показы</option>" +
          '<option value="paused"' +
          (state.status === "paused" ? " selected" : "") +
          ">Пауза</option>" +
          '<option value="finished"' +
          (state.status === "finished" ? " selected" : "") +
          ">Завершённые</option>" +
          '<option value="ready"' +
          (state.status === "ready" ? " selected" : "") +
          ">Готовы к запуску</option>" +
          '<option value="all"' +
          (state.status === "all" ? " selected" : "") +
          ">Все</option>" +
          "</select></label>") +
      '<label class="wh-job-status-filter-wrap">Период <select id="whBidderDays" class="wh-job-status-filter">' +
      [7, 14, 30]
        .map(function (n) {
          return (
            '<option value="' +
            n +
            '"' +
            (Number(days) === n ? " selected" : "") +
            "> " +
            n +
            " дн.</option>"
          );
        })
        .join("") +
      "</select></label>" +
      '<button type="button" class="wh-btn" id="whBidderReload">Обновить</button>' +
      "</div></div>"
    );
  }

  function statsCells(stats, hasStats) {
    if (!hasStats) {
      return "<td class=\"wh-num\">—</td><td class=\"wh-num\">—</td><td class=\"wh-num\">—</td>" +
        "<td class=\"wh-num\">—</td><td class=\"wh-num\">—</td><td class=\"wh-num\">—</td><td class=\"wh-num\">—</td>";
    }
    stats = stats || {};
    return (
      '<td class="wh-num">' +
      formatInt(stats.views) +
      '</td><td class="wh-num">' +
      formatInt(stats.clicks) +
      '</td><td class="wh-num">' +
      formatInt(stats.orders) +
      '</td><td class="wh-num">' +
      formatMoney(stats.spend) +
      '</td><td class="wh-num">' +
      formatPct(stats.ctr) +
      '</td><td class="wh-num">' +
      formatMoney(stats.cpc) +
      '</td><td class="wh-num">' +
      formatPct(stats.drr) +
      "</td>"
    );
  }

  function renderTable(campaigns) {
    if (!campaigns || !campaigns.length) {
      return '<p class="wh-muted">Кампаний нет.</p>';
    }
    var pager = global.WH_PAGER;
    var sliced = pager ? pager.slice(campaigns, state.page) : { items: campaigns, state: { page: 1 } };
    state.page = sliced.state.page;
    var body = sliced.items
      .map(function (row) {
        return (
          '<tr class="wh-bidder-row" data-advert="' +
          esc(row.advert_id) +
          '">' +
          "<td>" +
          esc(row.name) +
          "</td>" +
          "<td>" +
          statusBadge(row) +
          "</td>" +
          "<td>" +
          esc((row.payment_type || "").toUpperCase()) +
          "</td>" +
          '<td class="wh-num">' +
          formatMoney(row.budget) +
          "</td>" +
          statsCells(row.stats, row.has_stats) +
          "</tr>"
        );
      })
      .join("");
    return (
      '<div class="wh-table-scroll"><table class="wh-employees-table wh-bidder-table">' +
      "<thead><tr>" +
      "<th>Кампания</th><th>Статус</th><th>Оплата</th><th class=\"wh-num\">Бюджет</th>" +
      "<th class=\"wh-num\">Показы</th><th class=\"wh-num\">Клики</th><th class=\"wh-num\">Заказы</th>" +
      "<th class=\"wh-num\">Расход</th><th class=\"wh-num\">CTR</th><th class=\"wh-num\">CPC</th><th class=\"wh-num\">ДРР</th>" +
      "</tr></thead><tbody>" +
      body +
      "</tbody></table></div>" +
      (pager ? pager.html(sliced.state) : "")
    );
  }

  function bindList(root, tab, item) {
    var statusEl = root.querySelector("#whBidderStatus");
    var daysEl = root.querySelector("#whBidderDays");
    var reload = root.querySelector("#whBidderReload");
    if (statusEl) {
      statusEl.addEventListener("change", function () {
        state.status = statusEl.value;
        state.page = 1;
        loadList(tab, item);
      });
    }
    if (daysEl) {
      daysEl.addEventListener("change", function () {
        state.days = Number(daysEl.value) || 7;
        state.page = 1;
        loadList(tab, item);
      });
    }
    if (reload) {
      reload.addEventListener("click", function () {
        loadList(tab, item);
      });
    }
    root.querySelectorAll(".wh-bidder-row").forEach(function (tr) {
      tr.addEventListener("click", function () {
        var id = Number(tr.getAttribute("data-advert") || 0);
        if (id) loadDetail(tab, item, id);
      });
    });
    var pager = global.WH_PAGER;
    if (pager) {
      pager.bind(root, function (delta) {
        state.page += delta;
        paintList(tab, item);
      });
    }
  }

  function paintList(tab, item) {
    var root = panelEl();
    var data = state.list || { campaigns: [], totals: {}, begin: "", end: "", count: 0 };
    root.innerHTML =
      '<div class="wh-route-card">' +
      toolbarHtml("list") +
      '<p class="wh-muted">Период ' +
      esc(data.begin) +
      " — " +
      esc(data.end) +
      ". Нажмите строку, чтобы открыть детализацию. Статистика на списке — у активных и на паузе.</p>" +
      kpiHtml(data.totals) +
      renderTable(data.campaigns || []) +
      "</div>";
    bindList(root, tab, item);
  }

  function loadList(tab, item) {
    var root = panelEl();
    root.innerHTML = '<p class="wh-placeholder">Загрузка кампаний WB…</p>';
    fetchJson(
      "/api/warehouse/marketplaces/bidder/campaigns?status=" +
        encodeURIComponent(state.status) +
        "&days=" +
        encodeURIComponent(state.days)
    )
      .then(function (data) {
        state.list = data;
        state.advertId = 0;
        paintList(tab, item);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message || err) + "</p>";
      });
  }

  function formatTs(value) {
    if (!value) return "—";
    return esc(String(value).replace("T", " ").slice(0, 19));
  }

  function miniTable(rows, columns) {
    if (!rows || !rows.length) return '<p class="wh-muted">Нет данных.</p>';
    var head = columns
      .map(function (col) {
        return "<th" + (col.num ? ' class="wh-num"' : "") + ">" + esc(col.title) + "</th>";
      })
      .join("");
    var body = rows
      .map(function (row) {
        return (
          "<tr>" +
          columns
            .map(function (col) {
              var value = typeof col.value === "function" ? col.value(row) : row[col.key];
              return "<td" + (col.num ? ' class="wh-num"' : "") + ">" + value + "</td>";
            })
            .join("") +
          "</tr>"
        );
      })
      .join("");
    return (
      '<div class="wh-table-scroll"><table class="wh-employees-table">' +
      "<thead><tr>" +
      head +
      "</tr></thead><tbody>" +
      body +
      "</tbody></table></div>"
    );
  }

  function paintDetail(tab, item, data) {
    var root = panelEl();
    var campaign = data.campaign || {};
    var ts = campaign.timestamps || {};
    var placements = campaign.placements || {};
    var placeBits = [];
    if (placements.search) placeBits.push("поиск");
    if (placements.recommendations) placeBits.push("рекомендации");
    root.innerHTML =
      '<div class="wh-route-card">' +
      toolbarHtml("detail") +
      "<h3 class=\"wh-bidder-detail-title\">" +
      esc(campaign.name) +
      "</h3>" +
      '<p class="wh-muted">ID ' +
      esc(campaign.advert_id) +
      " · " +
      statusBadge(campaign) +
      " · " +
      esc((campaign.payment_type || "").toUpperCase()) +
      " · бюджет " +
      formatMoney(campaign.budget) +
      " · период " +
      esc(data.begin) +
      " — " +
      esc(data.end) +
      "</p>" +
      kpiHtml(campaign.stats) +
      '<div class="wh-bidder-meta">' +
      "<div>Создана: " +
      formatTs(ts.created) +
      "</div><div>Запущена: " +
      formatTs(ts.started) +
      "</div><div>Изменена: " +
      formatTs(ts.updated) +
      "</div><div>Завершена: " +
      formatTs(ts.ended) +
      "</div><div>Площадки: " +
      esc(placeBits.join(", ") || "—") +
      "</div></div>" +
      "<h4>По дням</h4>" +
      miniTable(campaign.days || [], [
        { title: "Дата", value: function (row) { return esc(row.date); } },
        { title: "Показы", num: true, value: function (row) { return formatInt(row.views); } },
        { title: "Клики", num: true, value: function (row) { return formatInt(row.clicks); } },
        { title: "Заказы", num: true, value: function (row) { return formatInt(row.orders); } },
        { title: "Расход", num: true, value: function (row) { return formatMoney(row.spend); } },
        { title: "CTR", num: true, value: function (row) { return formatPct(row.ctr); } },
        { title: "ДРР", num: true, value: function (row) { return formatPct(row.drr); } },
      ]) +
      "<h4>Товары в статистике</h4>" +
      miniTable(campaign.nm_stats || [], [
        { title: "nmID", value: function (row) { return "<code>" + esc(row.nm_id) + "</code>"; } },
        { title: "Название", value: function (row) { return esc(row.name); } },
        { title: "Показы", num: true, value: function (row) { return formatInt(row.views); } },
        { title: "Клики", num: true, value: function (row) { return formatInt(row.clicks); } },
        { title: "Заказы", num: true, value: function (row) { return formatInt(row.orders); } },
        { title: "Расход", num: true, value: function (row) { return formatMoney(row.spend); } },
      ]) +
      "</div>";
    var back = root.querySelector("#whBidderBack");
    if (back) {
      back.addEventListener("click", function () {
        state.advertId = 0;
        if (state.list) paintList(tab, item);
        else loadList(tab, item);
      });
    }
    var daysEl = root.querySelector("#whBidderDays");
    if (daysEl) {
      daysEl.addEventListener("change", function () {
        state.detailDays = Number(daysEl.value) || 30;
        loadDetail(tab, item, campaign.advert_id);
      });
    }
    var reload = root.querySelector("#whBidderReload");
    if (reload) {
      reload.addEventListener("click", function () {
        loadDetail(tab, item, campaign.advert_id);
      });
    }
  }

  function loadDetail(tab, item, advertId) {
    var root = panelEl();
    state.advertId = advertId;
    root.innerHTML = '<p class="wh-placeholder">Загрузка детализации…</p>';
    fetchJson(
      "/api/warehouse/marketplaces/bidder/campaigns/" +
        encodeURIComponent(advertId) +
        "?days=" +
        encodeURIComponent(state.detailDays)
    )
      .then(function (data) {
        paintDetail(tab, item, data);
      })
      .catch(function (err) {
        root.innerHTML =
          '<p class="wh-msg wh-msg-error">' +
          esc(err.message || err) +
          '</p><p><button type="button" class="wh-btn" id="whBidderBack">← К списку</button></p>';
        var back = root.querySelector("#whBidderBack");
        if (back) {
          back.addEventListener("click", function () {
            state.advertId = 0;
            if (state.list) paintList(tab, item);
            else loadList(tab, item);
          });
        }
      });
  }

  function render(tab, item) {
    preparePanel(tab, item);
    if (state.advertId) loadDetail(tab, item, state.advertId);
    else loadList(tab, item);
  }

  global.WhBidder = { render: render };
})(window);
