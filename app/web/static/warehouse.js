(function () {
  var NAV = [
    {
      id: "warehouse",
      title: "Склад",
      items: [
        { id: "receipts", title: "Оприходования" },
        { id: "writeoffs", title: "Списания" },
        { id: "transfers", title: "Перемещения" },
        { id: "inventory", title: "Инвентаризация" },
        { id: "stock", title: "Остатки" },
        { id: "warehouses", title: "Склады" },
        { id: "bin-transfers", title: "Перемещения по ячейкам" },
        { id: "pick-waves", title: "Волны отбора" },
      ],
    },
    {
      id: "sales",
      title: "Продажи",
      items: [
        { id: "customer-orders", title: "Заказы покупателей" },
        { id: "commission-sales", title: "Комиссионные продажи" },
        { id: "customer-returns", title: "Возвраты покупателей" },
        { id: "customer-invoices", title: "Счета покупателям" },
        { id: "shipments", title: "Отгрузки" },
      ],
    },
    {
      id: "products",
      title: "Товары",
      items: [
        { id: "catalog", title: "Товары и услуги" },
        { id: "price-lists", title: "Прайс-листы" },
        { id: "serial-numbers", title: "Серийные номера" },
        { id: "marking-codes", title: "Коды маркировки" },
        { id: "marking", title: "Маркировка" },
      ],
    },
    {
      id: "reports",
      title: "Отчеты",
      items: [
        { id: "commissioner-report", title: "Отчет комиссионера" },
        { id: "sales-analysis", title: "Анализ продаж" },
        { id: "stock-by-warehouse", title: "Остатки товаров на складах" },
        { id: "stock-movement", title: "Движение товаров" },
        { id: "demand", title: "Потребность товаров" },
        { id: "sales-funnel", title: "Воронка продаж" },
      ],
    },
    {
      id: "marketplaces",
      title: "Маркетплейсы",
      items: [
        { id: "fbs", title: "FBS" },
        { id: "stock-sync", title: "Синхронизация остатков" },
        { id: "pick-lists", title: "Листы подбора" },
      ],
    },
    {
      id: "tasks",
      title: "Задачи",
      items: [
        { id: "task-list", title: "Список задач" },
        { id: "task-create", title: "Создать задачу" },
        { id: "assigned-to-me", title: "Назначенные мне" },
        { id: "created-by-me", title: "Поставленные мной" },
        { id: "in-progress", title: "В работе" },
        { id: "pending-review", title: "На проверке" },
        { id: "completed", title: "Выполненные" },
        { id: "task-templates", title: "Шаблоны задач" },
      ],
    },
  ];

  var mainTabsEl = document.getElementById("whMainTabs");
  var subnavTitleEl = document.getElementById("whSubnavTitle");
  var subnavListEl = document.getElementById("whSubnavList");
  var contentTitleEl = document.getElementById("whContentTitle");
  var contentBreadcrumbEl = document.getElementById("whContentBreadcrumb");
  var contentPlaceholderEl = document.getElementById("whContentPlaceholder");

  var activeTabId = NAV[0].id;
  var activeItemId = NAV[0].items[0].id;

  function findTab(tabId) {
    for (var i = 0; i < NAV.length; i++) {
      if (NAV[i].id === tabId) return NAV[i];
    }
    return NAV[0];
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
    if (tabId && findTab(tabId)) activeTabId = tabId;
    var tab = findTab(activeTabId);
    var item = findItem(tab, itemId);
    if (item) activeItemId = item.id;
    else if (tab.items[0]) activeItemId = tab.items[0].id;
  }

  function updateHash() {
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

  function renderContent(tab, item) {
    contentTitleEl.textContent = item.title;
    contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    contentPlaceholderEl.textContent =
      "Раздел «" +
      item.title +
      "» пока не реализован. Навигация подготовлена — функционал добавим позже.";
  }

  function render() {
    var tab = findTab(activeTabId);
    var item = findItem(tab, activeItemId) || tab.items[0];
    if (item) activeItemId = item.id;
    renderMainTabs();
    renderSubnav(tab);
    renderContent(tab, item);
  }

  parseHash();
  render();

  window.addEventListener("hashchange", function () {
    parseHash();
    render();
  });

  document.getElementById("btnWhLogout").addEventListener("click", function () {
    fetch("/api/logout", { method: "POST", credentials: "include" })
      .then(function () {
        window.location.href = "/login";
      })
      .catch(function () {
        window.location.href = "/login";
      });
  });
})();
