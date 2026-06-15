(function () {
  var NAV = [
    {
      id: "warehouse",
      num: "1",
      title: "Склад",
      items: [
        { num: "1.1", title: "Оприходования" },
        { num: "1.2", title: "Списания" },
        { num: "1.3", title: "Перемещения" },
        { num: "1.4", title: "Инвентаризация" },
        { num: "1.5", title: "Остатки" },
        { num: "1.6", title: "Склады" },
        { num: "1.7", title: "Перемещения по ячейкам" },
        { num: "1.8", title: "Волны отбора" },
      ],
    },
    {
      id: "sales",
      num: "2",
      title: "Продажи",
      items: [
        { num: "2.1", title: "Заказы покупателей" },
        { num: "2.2", title: "Комиссионные продажи" },
        { num: "2.3", title: "Возвраты покупателей" },
        { num: "2.4", title: "Счета покупателям" },
        { num: "2.5", title: "Отгрузки" },
      ],
    },
    {
      id: "products",
      num: "3",
      title: "Товары",
      items: [
        { num: "3.1", title: "Товары и услуги" },
        { num: "3.2", title: "Прайс-листы" },
        { num: "3.3", title: "Серийные номера" },
        { num: "3.4", title: "Коды маркировки" },
        { num: "3.5", title: "Маркировка" },
      ],
    },
    {
      id: "reports",
      num: "4",
      title: "Отчеты",
      items: [
        { num: "4.1", title: "Отчет комиссионера" },
        { num: "4.2", title: "Анализ продаж" },
        { num: "4.3", title: "Остатки товаров на складах" },
        { num: "4.4", title: "Движение товаров" },
        { num: "4.5", title: "Потребность товаров" },
        { num: "4.6", title: "Воронка продаж" },
      ],
    },
    {
      id: "marketplaces",
      num: "5",
      title: "Маркетплейсы",
      items: [
        { num: "5.1", title: "FBS" },
        { num: "5.2", title: "Синхронизация остатков" },
        { num: "5.3", title: "Листы подбора" },
      ],
    },
    {
      id: "tasks",
      num: "6",
      title: "Задачи",
      items: [
        { num: "6.1", title: "Список задач" },
        { num: "6.2", title: "Создать задачу" },
        { num: "6.3", title: "Назначенные мне" },
        { num: "6.4", title: "Поставленные мной" },
        { num: "6.5", title: "В работе" },
        { num: "6.6", title: "На проверке" },
        { num: "6.7", title: "Выполненные" },
        { num: "6.8", title: "Шаблоны задач" },
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
  var activeItemNum = NAV[0].items[0].num;

  function findTab(tabId) {
    for (var i = 0; i < NAV.length; i++) {
      if (NAV[i].id === tabId) return NAV[i];
    }
    return NAV[0];
  }

  function findItem(tab, itemNum) {
    if (!tab || !tab.items) return null;
    for (var i = 0; i < tab.items.length; i++) {
      if (tab.items[i].num === itemNum) return tab.items[i];
    }
    return tab.items[0] || null;
  }

  function parseHash() {
    var hash = (window.location.hash || "").replace(/^#/, "");
    if (!hash) return;
    var parts = hash.split("&");
    var tabId = null;
    var itemNum = null;
    parts.forEach(function (p) {
      var kv = p.split("=");
      if (kv[0] === "tab" && kv[1]) tabId = decodeURIComponent(kv[1]);
      if (kv[0] === "item" && kv[1]) itemNum = decodeURIComponent(kv[1]);
    });
    if (tabId && findTab(tabId)) activeTabId = tabId;
    var tab = findTab(activeTabId);
    var item = findItem(tab, itemNum);
    if (item) activeItemNum = item.num;
    else if (tab.items[0]) activeItemNum = tab.items[0].num;
  }

  function updateHash() {
    var next = "#tab=" + encodeURIComponent(activeTabId) + "&item=" + encodeURIComponent(activeItemNum);
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
      btn.textContent = tab.num + ". " + tab.title;
      btn.setAttribute("data-tab-id", tab.id);
      btn.addEventListener("click", function () {
        activeTabId = tab.id;
        var t = findTab(activeTabId);
        activeItemNum = t.items[0] ? t.items[0].num : "";
        render();
        updateHash();
      });
      mainTabsEl.appendChild(btn);
    });
  }

  function renderSubnav(tab) {
    subnavTitleEl.textContent = tab.num + ". " + tab.title;
    subnavListEl.innerHTML = "";
    tab.items.forEach(function (item) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "#";
      a.className = "wh-subnav-link" + (item.num === activeItemNum ? " active" : "");
      a.innerHTML =
        '<span class="wh-subnav-code">' +
        escapeHtml(item.num) +
        "</span>" +
        escapeHtml(item.title);
      a.addEventListener("click", function (e) {
        e.preventDefault();
        activeItemNum = item.num;
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
    contentBreadcrumbEl.textContent = tab.num + ". " + tab.title + " → " + item.num + " " + item.title;
    contentPlaceholderEl.textContent =
      "Раздел «" +
      item.title +
      "» пока не реализован. Навигация подготовлена — функционал добавим позже.";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function render() {
    var tab = findTab(activeTabId);
    var item = findItem(tab, activeItemNum) || tab.items[0];
    if (item) activeItemNum = item.num;
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
