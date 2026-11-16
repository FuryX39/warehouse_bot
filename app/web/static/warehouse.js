(function () {
  var NAV = [];
  var sessionUser = null;
  var permissionsSchema = [];

  var mainTabsEl = document.getElementById("whMainTabs");
  var subnavTitleEl = document.getElementById("whSubnavTitle");
  var subnavListEl = document.getElementById("whSubnavList");
  var contentTitleEl = document.getElementById("whContentTitle");
  var contentBreadcrumbEl = document.getElementById("whContentBreadcrumb");
  var contentPlaceholderEl = document.getElementById("whContentPlaceholder");
  var contentPanelEl = document.getElementById("whContentPanel");
  var userNameEl = document.getElementById("whUserName");

  var activeTabId = "";
  var activeItemId = "";

  function remapJcukenToQwerty(text) {
    var letters = "ёйцукенгшщзхъфывапролджэячсмитьбюЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ";
    var from = letters + "\"№;:?.,";
    var to = "`qwertyuiop[]asdfghjkl;'zxcvbnm,.~QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>@#$^&/?";
    var src = String(text || "");
    var hasRu = false;
    var i;
    for (i = 0; i < src.length; i++) {
      if (letters.indexOf(src.charAt(i)) >= 0) {
        hasRu = true;
        break;
      }
    }
    if (!hasRu) return src;
    var out = "";
    for (i = 0; i < src.length; i++) {
      var ch = src.charAt(i);
      var idx = from.indexOf(ch);
      out += idx >= 0 ? to.charAt(idx) : ch;
    }
    return out;
  }

  function apiDetailText(detail) {
    if (detail == null || detail === "") return "";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map(function (item) {
          if (typeof item === "string") return item;
          if (item && typeof item.msg === "string") return item.msg;
          return "";
        })
        .filter(Boolean)
        .join("; ");
    }
    if (typeof detail === "object" && typeof detail.msg === "string") return detail.msg;
    return String(detail);
  }

  function isNetworkFetchError(err) {
    var message = String((err && err.message) || err || "");
    return /Failed to fetch|Load failed|NetworkError|network/i.test(message);
  }

  function fetchJson(url, options) {
    return fetch(url, Object.assign({ credentials: "include", cache: "no-store" }, options || {}))
      .then(function (r) {
        return r.text().then(function (text) {
          var json = null;
          try {
            json = text ? JSON.parse(text) : null;
          } catch (e) {
            json = null;
          }
          if (!r.ok) {
            var msg =
              (json && json.detail != null ? apiDetailText(json.detail) : "") ||
              text ||
              "HTTP " + r.status;
            var err = new Error(msg);
            err.status = r.status;
            throw err;
          }
          return json;
        });
      })
      .catch(function (err) {
        if (err && err.status) throw err;
        if (isNetworkFetchError(err)) {
          throw new Error("Нет связи с сервером. Обновите страницу и повторите.");
        }
        throw err;
      });
  }

  function getTab(tabId) {
    for (var i = 0; i < NAV.length; i++) {
      if (NAV[i].id === tabId) return NAV[i];
    }
    return null;
  }

  function findTab(tabId) {
    return getTab(tabId) || NAV[0] || null;
  }

  function findItemStrict(tab, itemId) {
    if (!tab || !tab.items) return null;
    for (var i = 0; i < tab.items.length; i++) {
      if (tab.items[i].id === itemId) return tab.items[i];
    }
    return null;
  }

  function findItem(tab, itemId) {
    if (!tab || !tab.items) return null;
    for (var i = 0; i < tab.items.length; i++) {
      if (tab.items[i].id === itemId) return tab.items[i];
    }
    return tab.items[0] || null;
  }

  function parseHash() {
    var hash = (window.location.hash || "").replace(/^#/, "");
    if (!hash) return;
    var parts = hash.split("&");
    var tabId = null;
    var itemId = null;
    parts.forEach(function (p) {
      var kv = p.split("=");
      if (kv[0] === "tab" && kv[1]) tabId = decodeURIComponent(kv[1]);
      if (kv[0] === "item" && kv[1]) itemId = decodeURIComponent(kv[1]);
    });
    if (tabId && getTab(tabId)) activeTabId = tabId;
    var tab = getTab(activeTabId) || NAV[0];
    if (tab) activeTabId = tab.id;
    var item = findItemStrict(tab, itemId);
    if (item) activeItemId = item.id;
    else if (tab && tab.items[0]) activeItemId = tab.items[0].id;
  }

  function ensureActiveSelection() {
    if (!NAV.length) return;
    if (!getTab(activeTabId)) {
      activeTabId = NAV[0].id;
      activeItemId = NAV[0].items[0] ? NAV[0].items[0].id : "";
      return;
    }
    var tab = getTab(activeTabId);
    if (!findItemStrict(tab, activeItemId) && tab.items[0]) {
      activeItemId = tab.items[0].id;
    }
  }

  function updateHash() {
    if (!activeTabId || !activeItemId) return;
    var next = "#tab=" + encodeURIComponent(activeTabId) + "&item=" + encodeURIComponent(activeItemId);
    if (window.location.hash !== next) {
      window.location.hash = next;
    }
  }

  function renderMainTabs() {
    mainTabsEl.innerHTML = "";
    NAV.forEach(function (tab) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "wh-main-tab" + (tab.id === activeTabId ? " active" : "");
      btn.textContent = tab.title;
      btn.setAttribute("data-tab-id", tab.id);
      btn.addEventListener("click", function () {
        activeTabId = tab.id;
        var t = findTab(activeTabId);
        activeItemId = t.items[0] ? t.items[0].id : "";
        render();
        updateHash();
      });
      mainTabsEl.appendChild(btn);
    });
  }

  function renderSubnav(tab) {
    subnavTitleEl.textContent = tab.title;
    subnavListEl.innerHTML = "";
    tab.items.forEach(function (item) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "#";
      a.className = "wh-subnav-link" + (item.id === activeItemId ? " active" : "");
      a.textContent = item.title;
      a.addEventListener("click", function (e) {
        e.preventDefault();
        activeItemId = item.id;
        renderContent(tab, item);
        renderSubnav(tab);
        renderMainTabs();
        updateHash();
      });
      li.appendChild(a);
      subnavListEl.appendChild(li);
    });
  }

  function clearContentPanel() {
    contentPanelEl.hidden = true;
    contentPanelEl.innerHTML = "";
    contentPlaceholderEl.hidden = false;
    var card = document.querySelector(".wh-content-card");
    if (card) card.classList.remove("wh-content-card--wide");
  }

  function showPlaceholder(tab, item, text) {
    clearContentPanel();
    contentTitleEl.textContent = item.title;
    contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    contentPlaceholderEl.textContent =
      text ||
      "Раздел «" +
        item.title +
        "» пока не реализован. Навигация подготовлена — функционал добавим позже.";
  }

  function formatTs(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1000).toLocaleString("ru-RU");
    } catch (e) {
      return String(ts);
    }
  }

  function renderContent(tab, item) {
    if (tab.id === "employees" && item.id === "employees" && window.WhStaff) {
      window.WhStaff.renderEmployees(tab, item);
      return;
    }
    if (tab.id === "employees" && item.id === "productivity" && window.WhProductivity) {
      window.WhProductivity.render(tab, item);
      return;
    }
    if (tab.id === "employees" && item.id === "roles" && window.WhStaff) {
      window.WhStaff.renderRoles(tab, item);
      return;
    }
    if (tab.id === "employees" && item.id === "schedule" && window.WhStaff) {
      window.WhStaff.renderSchedule(tab, item);
      return;
    }
    if (tab.id === "crm" && item.id === "counterparties" && window.WhCrm) {
      window.WhCrm.renderCounterparties(tab, item);
      return;
    }
    if (tab.id === "products" && item.id === "price-types" && window.WhProducts) {
      window.WhProducts.renderPriceTypes(tab, item);
      return;
    }
    if (tab.id === "warehouse" && item.id === "warehouses" && window.WhStorage) {
      window.WhStorage.renderWarehouses(tab, item);
      return;
    }
    if (tab.id === "warehouse" && item.id === "stock" && window.WhStock) {
      window.WhStock.renderStock(tab, item);
      return;
    }
    if (tab.id === "warehouse" && item.id === "receipts" && window.WhReceipts) {
      window.WhReceipts.renderReceipts(tab, item);
      return;
    }
    if (tab.id === "warehouse" && item.id === "writeoffs" && window.WhWriteoffs) {
      window.WhWriteoffs.renderWriteoffs(tab, item);
      return;
    }
    if (tab.id === "warehouse" && item.id === "transfers" && window.WhTransfers) {
      window.WhTransfers.renderTransfers(tab, item);
      return;
    }
    if (tab.id === "tasks" && window.WhTasks) {
      window.WhTasks.renderTasks(tab, item);
      return;
    }
    if (tab.id === "admin" && window.WhAdmin) {
      window.WhAdmin.render(tab, item);
      return;
    }
    if (
      tab.id === "marketplaces" &&
      (item.id === "ozon-fbo-supplies" || item.id === "ozon-fbo-packing") &&
      window.WhOzonFbo
    ) {
      window.WhOzonFbo.render(tab, item);
      return;
    }
    if (tab.id === "marketplaces" && item.id === "wb-fbo-supplies" && window.WhWbFbo) {
      window.WhWbFbo.render(tab, item);
      return;
    }
    if (tab.id === "marketplaces" && item.id === "yandex-fbo-supplies" && window.WhYandexFbo) {
      window.WhYandexFbo.render(tab, item);
      return;
    }
    if (tab.id === "marketplaces" && item.id === "wb-fbo-new" && window.WhWbFboNew) {
      window.WhWbFboNew.render(tab, item);
      return;
    }
    if (tab.id === "marketplaces" && item.id === "other-platforms" && window.WhOtherPlatforms) {
      window.WhOtherPlatforms.render(tab, item);
      return;
    }
    if (tab.id === "marketplaces" && item.id === "fbs" && window.WhFbs) {
      window.WhFbs.render(tab, item);
      return;
    }
    if (tab.id === "marketplaces" && item.id === "route-sheets" && window.WhRouteSheets) {
      window.WhRouteSheets.render(tab, item);
      return;
    }
    if (tab.id === "marketplaces" && item.id === "stock-sync" && window.WhStockSync) {
      window.WhStockSync.render(tab, item);
      return;
    }
    if (tab.id === "marketplaces" && item.id === "repricer" && window.WhRepricer) {
      window.WhRepricer.render(tab, item);
      return;
    }
    if (tab.id === "reports" && window.WhReports) {
      window.WhReports.render(tab, item);
      return;
    }
    if (tab.id === "tools" && window.WhTools) {
      window.WhTools.render(tab, item);
      return;
    }
    if (tab.id === "products" && item.id === "catalog" && window.WhProducts) {
      window.WhProducts.renderCatalog(tab, item);
      return;
    }
    if (tab.id === "products" && item.id === "scanner" && window.WhProducts) {
      window.WhProducts.renderScanner(tab, item);
      return;
    }
    if (tab.id === "products" && item.id === "price-lists" && window.WhPriceLists) {
      window.WhPriceLists.render(tab, item);
      return;
    }
    if (tab.id === "products" && item.id === "marking-codes" && window.WhMarking) {
      window.WhMarking.render(tab, item);
      return;
    }
    if (window.WhWms && (
      item.id === "customer-orders" ||
      item.id === "shipments" ||
      item.id === "pick-waves" ||
      item.id === "inventory" ||
      item.id === "bin-transfers"
    )) {
      window.WhWms.render(tab, item);
      return;
    }
    showPlaceholder(tab, item);
  }


  function render() {
    if (!NAV.length) {
      mainTabsEl.innerHTML = "";
      subnavListEl.innerHTML = "";
      contentTitleEl.textContent = "Нет доступных разделов";
      contentBreadcrumbEl.textContent = "";
      clearContentPanel();
      var hint =
        isAdminUser(sessionUser)
          ? "Навигация пуста — обновите страницу. Если не помогло, проверьте WAREHOUSE_ADMIN_LOGIN в .env."
          : "У вашей учётной записи нет доступа ни к одному разделу. Обратитесь к администратору.";
      contentPlaceholderEl.hidden = false;
      contentPlaceholderEl.textContent = hint;
      return;
    }
    ensureActiveSelection();
    var tab = getTab(activeTabId) || NAV[0];
    if (!tab) return;
    activeTabId = tab.id;
    var item = findItemStrict(tab, activeItemId) || tab.items[0];
    if (item) activeItemId = item.id;
    renderMainTabs();
    renderSubnav(tab);
    renderContent(tab, item);
  }

  function isAdminUser(user) {
    return !!(user && (user.is_admin === true || user.is_admin === 1));
  }

  function setUserLabel() {
    if (!sessionUser) {
      userNameEl.textContent = "";
      return;
    }
    var name = sessionUser.display_name || sessionUser.login;
    userNameEl.textContent = name + (isAdminUser(sessionUser) ? " · администратор" : "");
  }

  function loadSession() {
    return fetchJson("/api/warehouse/session").then(function (data) {
      sessionUser = data.user;
      NAV = data.nav || [];
      permissionsSchema = data.permissions_schema || [];
      setUserLabel();
      if (!activeTabId && NAV[0]) {
        activeTabId = NAV[0].id;
        activeItemId = NAV[0].items[0] ? NAV[0].items[0].id : "";
      }
      ensureActiveSelection();
    });
  }

  function boot() {
    loadSession()
      .then(function () {
        parseHash();
        ensureActiveSelection();
        render();
        updateHash();
      })
      .catch(function (err) {
        if (err.status === 401) {
          window.location.href = "/warehouse/login";
          return;
        }
        contentPlaceholderEl.textContent = err.message || "Не удалось загрузить сессию";
      });
  }

  window.addEventListener("hashchange", function () {
    parseHash();
    render();
  });

  document.getElementById("btnWhLogout").addEventListener("click", function () {
    fetch("/api/warehouse/logout", { method: "POST", credentials: "include" })
      .then(function () {
        window.location.href = "/warehouse/login";
      })
      .catch(function () {
        window.location.href = "/warehouse/login";
      });
  });

  function jobStatusFilterHtml(selected, options) {
    selected = selected == null ? "active" : String(selected);
    var opts = options && options.length ? options : [
      { value: "active", label: "Кроме отменённых" },
      { value: "all", label: "Все" },
      { value: "open", label: "Открыто" },
      { value: "in_progress", label: "В работе" },
      { value: "done", label: "Выполнено" },
      { value: "cancelled", label: "Отменено" },
    ];
    function attr(value) {
      return String(value == null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;");
    }
    var html =
      '<label class="wh-job-status-filter-wrap">Статус <select class="wh-job-status-filter" aria-label="Статус задания">';
    opts.forEach(function (opt) {
      var value = opt.value != null ? String(opt.value) : "";
      var label = opt.label != null ? String(opt.label) : value;
      html +=
        '<option value="' +
        attr(value) +
        '"' +
        (value === selected ? " selected" : "") +
        ">" +
        attr(label) +
        "</option>";
    });
    return html + "</select></label>";
  }

  function jobsSectionHtml(title, selected) {
    return (
      '<div class="wh-job-status-filter-bar">' +
      '<h4 class="wh-crm-section-title">' +
      String(title || "Задания") +
      "</h4>" +
      jobStatusFilterHtml(selected) +
      "</div>"
    );
  }

  function filterJobsByStatus(jobs, filter) {
    jobs = jobs || [];
    var value = filter == null || filter === "" ? "active" : String(filter);
    if (value === "all") return jobs.slice();
    if (value === "active") {
      return jobs.filter(function (job) {
        return String((job && job.status) || "") !== "cancelled";
      });
    }
    return jobs.filter(function (job) {
      return String((job && job.status) || "") === value;
    });
  }

  function bindJobStatusFilter(root, onChange) {
    if (!root || typeof onChange !== "function") return;
    var sel = root.querySelector(".wh-job-status-filter");
    if (!sel) return;
    sel.addEventListener("change", function () {
      onChange(sel.value);
    });
  }

  function honestSignCheckbox(url, checked, id) {
    function attr(value) {
      return String(value == null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;");
    }
    return (
      '<label class="wh-chz" title="Отражено в 1С" onclick="event.stopPropagation()">' +
      '<input type="checkbox" class="wh-chz-input" data-url="' +
      attr(url) +
      '" data-id="' +
      attr(id) +
      '"' +
      (checked ? " checked" : "") +
      " /> ЧЗ</label>"
    );
  }

  function bindHonestSign(root, jobs) {
    if (!root) return;
    root._chzJobs = jobs;
    if (root.getAttribute("data-chz-bound") === "1") return;
    root.setAttribute("data-chz-bound", "1");
    root.addEventListener("click", function (e) {
      var t = e.target;
      if (t && t.classList && t.classList.contains("wh-chz-input")) e.stopPropagation();
      if (t && t.closest && t.closest(".wh-chz")) e.stopPropagation();
    });
    root.addEventListener("change", function (e) {
      var box = e.target;
      if (!box || !box.classList || !box.classList.contains("wh-chz-input")) return;
      var url = box.getAttribute("data-url");
      var id = Number(box.getAttribute("data-id") || 0);
      var checked = box.checked;
      fetchJson(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ honest_sign_1c: checked }),
      })
        .then(function () {
          var list = root._chzJobs;
          if (!list) return;
          list.forEach(function (job) {
            if (Number(job.id) === id) job.honest_sign_1c = checked;
          });
        })
        .catch(function (err) {
          box.checked = !checked;
          window.alert(err.message || "Не удалось сохранить ЧЗ");
        });
    });
  }

  boot();

  window.WH_SHELL = {
    fetchJson: fetchJson,
    isNetworkFetchError: isNetworkFetchError,
    remapJcukenToQwerty: remapJcukenToQwerty,
    jobStatusFilterHtml: jobStatusFilterHtml,
    jobsSectionHtml: jobsSectionHtml,
    filterJobsByStatus: filterJobsByStatus,
    bindJobStatusFilter: bindJobStatusFilter,
    honestSignCheckbox: honestSignCheckbox,
    bindHonestSign: bindHonestSign,
    contentPanelEl: contentPanelEl,
    contentTitleEl: contentTitleEl,
    contentBreadcrumbEl: contentBreadcrumbEl,
    contentPlaceholderEl: contentPlaceholderEl,
    reloadSession: function () {
      return loadSession().then(function () {
        render();
      });
    },
  };
})();
