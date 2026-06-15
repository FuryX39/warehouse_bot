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
  var employeesCache = [];
  var editingEmployeeId = null;

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

  function fetchJson(url, options) {
    return fetch(url, Object.assign({ credentials: "include" }, options || {})).then(function (r) {
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

  function collectPermissionsFromEditor(container) {
    var perms = {};
    var sections = container.querySelectorAll("[data-perm-section]");
    sections.forEach(function (sectionEl) {
      var sectionId = sectionEl.getAttribute("data-perm-section");
      var items = [];
      sectionEl.querySelectorAll("input[data-perm-item]:checked").forEach(function (cb) {
        items.push(cb.getAttribute("data-perm-item"));
      });
      if (items.length) perms[sectionId] = items;
    });
    return perms;
  }

  function buildPermissionsEditor(container, employee) {
    container.innerHTML = "";
    if (employee.is_admin) {
      var note = document.createElement("p");
      note.className = "wh-msg";
      note.textContent = "У администратора полный доступ ко всем разделам.";
      container.appendChild(note);
      return;
    }
    permissionsSchema.forEach(function (section) {
      var block = document.createElement("div");
      block.className = "wh-perm-section";
      block.setAttribute("data-perm-section", section.id);
      var title = document.createElement("div");
      title.className = "wh-perm-section-title";
      title.textContent = section.title;
      block.appendChild(title);
      var itemsWrap = document.createElement("div");
      itemsWrap.className = "wh-perm-items";
      var allowed = (employee.permissions && employee.permissions[section.id]) || [];
      section.items.forEach(function (item) {
        var label = document.createElement("label");
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.setAttribute("data-perm-item", item.id);
        cb.checked = allowed.indexOf(item.id) >= 0;
        label.appendChild(cb);
        label.appendChild(document.createTextNode(item.title));
        itemsWrap.appendChild(label);
      });
      block.appendChild(itemsWrap);
      container.appendChild(block);
    });
  }

  function renderEmployeeEditor(employee) {
    editingEmployeeId = employee.id;
    var editor = document.createElement("div");
    editor.className = "wh-employee-editor";
    editor.id = "whEmployeeEditor";

    var heading = document.createElement("h3");
    heading.textContent = "Редактирование: " + (employee.display_name || employee.login);
    editor.appendChild(heading);

    var row1 = document.createElement("div");
    row1.className = "wh-form-row";
    row1.innerHTML =
      '<div><label>Логин</label><input type="text" id="whEditLogin" value="' +
      escapeAttr(employee.login) +
      '" /></div>' +
      '<div><label>Имя</label><input type="text" id="whEditDisplayName" value="' +
      escapeAttr(employee.display_name || "") +
      '" /></div>' +
      '<div><label>Новый пароль</label><input type="password" id="whEditPassword" placeholder="Оставьте пустым, чтобы не менять" autocomplete="new-password" /></div>';
    editor.appendChild(row1);

    var checks = document.createElement("div");
    checks.className = "wh-form-checks";
    checks.innerHTML =
      '<label><input type="checkbox" id="whEditIsAdmin"' +
      (employee.is_admin ? " checked" : "") +
      " /> Администратор</label>" +
      '<label><input type="checkbox" id="whEditIsActive"' +
      (employee.is_active ? " checked" : "") +
      " /> Активен</label>";
    editor.appendChild(checks);

    var permTitle = document.createElement("div");
    permTitle.className = "wh-perm-section-title";
    permTitle.textContent = "Доступ к разделам";
    editor.appendChild(permTitle);

    var permGrid = document.createElement("div");
    permGrid.className = "wh-permissions-grid";
    permGrid.id = "whPermGrid";
    editor.appendChild(permGrid);
    buildPermissionsEditor(permGrid, employee);

    var adminCb = editor.querySelector("#whEditIsAdmin");
    adminCb.addEventListener("change", function () {
      var draft = Object.assign({}, employee, { is_admin: adminCb.checked });
      buildPermissionsEditor(permGrid, draft);
    });

    var actions = document.createElement("div");
    actions.className = "wh-form-actions";
    var saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.className = "wh-btn wh-btn-primary";
    saveBtn.textContent = "Сохранить";
    var cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "wh-btn";
    cancelBtn.textContent = "Отмена";
    actions.appendChild(saveBtn);
    actions.appendChild(cancelBtn);
    editor.appendChild(actions);

    var msg = document.createElement("p");
    msg.className = "wh-msg";
    msg.id = "whEditMsg";
    editor.appendChild(msg);

    saveBtn.addEventListener("click", function () {
      msg.textContent = "";
      msg.className = "wh-msg";
      var body = {
        login: editor.querySelector("#whEditLogin").value.trim(),
        display_name: editor.querySelector("#whEditDisplayName").value.trim(),
        password: editor.querySelector("#whEditPassword").value,
        is_admin: editor.querySelector("#whEditIsAdmin").checked,
        is_active: editor.querySelector("#whEditIsActive").checked,
        permissions: collectPermissionsFromEditor(permGrid),
      };
      if (!body.password) delete body.password;
      fetchJson("/api/warehouse/employees/" + employee.id, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (data) {
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = "Сохранено.";
          var idx = -1;
          for (var i = 0; i < employeesCache.length; i++) {
            if (employeesCache[i].id === employee.id) {
              idx = i;
              break;
            }
          }
          if (idx >= 0) employeesCache[idx] = data.employee;
          renderEmployeeListTable();
          if (employee.id === sessionUser.id) {
            return loadSession().then(function () {
              render();
            });
          }
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || "Ошибка сохранения";
        });
    });

    cancelBtn.addEventListener("click", function () {
      editingEmployeeId = null;
      editor.remove();
    });

    var old = document.getElementById("whEmployeeEditor");
    if (old) old.remove();
    contentPanelEl.appendChild(editor);
  }

  function escapeAttr(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  function renderEmployeeListTable() {
    var table = document.createElement("table");
    table.className = "wh-employees-table";
    table.innerHTML =
      "<thead><tr>" +
      "<th>Логин</th><th>Имя</th><th>Роль</th><th>Статус</th><th>Обновлён</th><th></th>" +
      "</tr></thead>";
    var tbody = document.createElement("tbody");
    employeesCache.forEach(function (emp) {
      var tr = document.createElement("tr");
      if (!emp.is_active) tr.className = "inactive";
      var role = emp.is_admin
        ? '<span class="wh-badge wh-badge-admin">Админ</span>'
        : "Сотрудник";
      var status = emp.is_active
        ? "Активен"
        : '<span class="wh-badge wh-badge-inactive">Неактивен</span>';
      tr.innerHTML =
        "<td>" +
        escapeAttr(emp.login) +
        "</td><td>" +
        escapeAttr(emp.display_name || "—") +
        "</td><td>" +
        role +
        "</td><td>" +
        status +
        "</td><td>" +
        formatTs(emp.updated_at_ts) +
        "</td><td></td>";
      var editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "wh-btn";
      editBtn.textContent = "Изменить";
      editBtn.addEventListener("click", function () {
        renderEmployeeEditor(emp);
      });
      tr.lastElementChild.appendChild(editBtn);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);

    var wrap = document.getElementById("whEmployeesTableWrap");
    if (wrap) {
      wrap.innerHTML = "";
      wrap.appendChild(table);
    } else {
      contentPanelEl.innerHTML = "";
      var intro = document.createElement("p");
      intro.className = "wh-msg";
      intro.textContent = "Все учётные записи новой панели. Изменения применяются сразу после сохранения.";
      contentPanelEl.appendChild(intro);
      var tableWrap = document.createElement("div");
      tableWrap.id = "whEmployeesTableWrap";
      tableWrap.appendChild(table);
      contentPanelEl.appendChild(tableWrap);
    }

    if (editingEmployeeId != null) {
      for (var j = 0; j < employeesCache.length; j++) {
        if (employeesCache[j].id === editingEmployeeId) {
          renderEmployeeEditor(employeesCache[j]);
          break;
        }
      }
    }
  }

  function renderEmployeeList(tab, item) {
    contentTitleEl.textContent = item.title;
    contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    contentPlaceholderEl.hidden = true;
    contentPanelEl.hidden = false;
    contentPanelEl.innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";

    fetchJson("/api/warehouse/employees")
      .then(function (data) {
        employeesCache = data.employees || [];
        renderEmployeeListTable();
      })
      .catch(function (err) {
        contentPanelEl.innerHTML =
          '<p class="wh-msg wh-msg-error">' + escapeAttr(err.message || "Ошибка загрузки") + "</p>";
      });
  }

  function renderContent(tab, item) {
    if (tab.id === "employees" && item.id === "employee-list") {
      renderEmployeeList(tab, item);
      return;
    }
    if (tab.id === "employees" && item.id === "employee-create") {
      showPlaceholder(
        tab,
        item,
        "Форма добавления сотрудника будет реализована позже. Пока учётные записи можно редактировать в «Списке сотрудников»."
      );
      return;
    }
    if (tab.id === "employees" && item.id === "employee-roles") {
      showPlaceholder(
        tab,
        item,
        "Раздел «Роли» пока не реализован. Здесь будет управление ролями и наборами прав."
      );
      return;
    }
    if (tab.id === "crm" && item.id === "counterparties" && window.WhCrm) {
      window.WhCrm.renderCounterparties(tab, item);
      return;
    }
    if (tab.id === "products" && item.id === "price-types" && window.WhCrm) {
      window.WhCrm.renderPriceTypes(tab, item);
      return;
    }
    if (tab.id === "warehouse" && item.id === "warehouses" && window.WhStorage) {
      window.WhStorage.renderWarehouses(tab, item);
      return;
    }
    if (tab.id === "products" && item.id === "catalog" && window.WhProducts) {
      window.WhProducts.renderCatalog(tab, item);
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

  boot();

  window.WH_SHELL = {
    fetchJson: fetchJson,
    contentPanelEl: contentPanelEl,
    contentTitleEl: contentTitleEl,
    contentBreadcrumbEl: contentBreadcrumbEl,
    contentPlaceholderEl: contentPlaceholderEl,
  };
})();
