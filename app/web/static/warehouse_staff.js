(function (global) {
  var CONFIGURE_VALUE = "__configure__";
  var permissionsSchema = [];
  var meta = { groups: [] };
  var rolesCache = [];
  var employeesCache = [];
  var listFilters = {};
  var listPage = 1;
  var filterPanelOpen = false;
  var editingEmployeeId = null;
  var editingRoleId = null;
  var showEmployeeForm = false;

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
    if (card) card.classList.remove("wh-content-card--wide");
  }

  function formatTs(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1000).toLocaleString("ru-RU");
    } catch (e) {
      return String(ts);
    }
  }

  function loadPermissionsSchema() {
    if (permissionsSchema.length) return Promise.resolve(permissionsSchema);
    return fetchJson("/api/warehouse/permissions-schema").then(function (data) {
      permissionsSchema = data.schema || [];
      return permissionsSchema;
    });
  }

  function loadRoles() {
    return fetchJson("/api/warehouse/roles").then(function (data) {
      rolesCache = data.roles || [];
      return rolesCache;
    });
  }

  function loadMeta() {
    return fetchJson("/api/warehouse/employees/meta").then(function (data) {
      meta.groups = data.groups || [];
      return meta;
    });
  }

  function loadEmployees() {
    return fetchJson("/api/warehouse/employees" + filtersQuery()).then(function (data) {
      employeesCache = data.employees || [];
      return employeesCache;
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
    var f = {};
    var q = root.querySelector("#whStaffQuickSearch");
    if (q && q.value.trim()) f.q = q.value.trim();
    root.querySelectorAll("[data-filter]").forEach(function (el) {
      var key = el.getAttribute("data-filter");
      var val = el.value.trim();
      if (val) f[key] = val;
    });
    return f;
  }

  function filterField(key, label, type, items) {
    var val = esc(listFilters[key] || "");
    if (type === "select") {
      var opts = '<option value="">—</option>';
      (items || []).forEach(function (it) {
        var sel = String(listFilters[key] || "") === String(it.id) ? " selected" : "";
        opts += '<option value="' + esc(it.id) + '"' + sel + ">" + esc(it.name) + "</option>";
      });
      return (
        '<div><label>' + esc(label) + '</label><select data-filter="' + key + '">' + opts + "</select></div>"
      );
    }
    if (type === "status") {
      var statuses = [
        { v: "", t: "—" },
        { v: "1", t: "Активен" },
        { v: "0", t: "Неактивен" },
      ];
      var statusOpts = statuses
        .map(function (s) {
          var sel = listFilters.is_active === s.v ? " selected" : "";
          return '<option value="' + esc(s.v) + '"' + sel + ">" + esc(s.t) + "</option>";
        })
        .join("");
      return (
        '<div><label>' + esc(label) + '</label><select data-filter="is_active">' + statusOpts + "</select></div>"
      );
    }
    return (
      '<div><label>' + esc(label) + '</label><input type="text" data-filter="' + key + '" value="' + val + '" /></div>'
    );
  }

  function renderFilterPanel() {
    return (
      '<div class="wh-crm-filters' + (filterPanelOpen ? "" : " hidden") + '" id="whStaffFilters">' +
      '<h4 class="wh-crm-section-title">Фильтр по полям</h4>' +
      '<div class="wh-crm-filter-grid">' +
      filterField("display_name", "Имя", "text") +
      filterField("login", "Логин", "text") +
      filterField("group_id", "Группа", "select", meta.groups) +
      filterField("telegram_nick", "Ник в Telegram", "text") +
      filterField("role_id", "Роль", "select", rolesCache) +
      filterField("is_active", "Статус", "status") +
      "</div>" +
      '<div class="wh-form-actions">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whStaffApplyFilter">Применить</button>' +
      '<button type="button" class="wh-btn" id="whStaffResetFilter">Сбросить</button>' +
      "</div></div>"
    );
  }

  function buildSelectOptions(items, selectedId) {
    var html = '<option value="">—</option>';
    (items || []).forEach(function (it) {
      var sel = String(selectedId || "") === String(it.id) ? " selected" : "";
      html += '<option value="' + esc(it.id) + '"' + sel + ">" + esc(it.name) + "</option>";
    });
    html += '<option value="' + CONFIGURE_VALUE + '">Настроить…</option>';
    return html;
  }

  function bindConfigureSelect(selectEl, onConfigure) {
    if (!selectEl) return;
    selectEl.addEventListener("change", function () {
      if (selectEl.value === CONFIGURE_VALUE) {
        var prev = selectEl.getAttribute("data-prev") || "";
        selectEl.value = prev;
        onConfigure();
      } else {
        selectEl.setAttribute("data-prev", selectEl.value);
      }
    });
  }

  function openModal(title, bodyHtml, onSave) {
    var backdrop = document.createElement("div");
    backdrop.className = "wh-modal-backdrop";
    backdrop.innerHTML =
      '<div class="wh-modal wh-modal-wide" role="dialog">' +
      '<div class="wh-modal-header"><h3>' + esc(title) + '</h3><button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body">' + bodyHtml + "</div>" +
      '<div class="wh-modal-footer">' +
      '<button type="button" class="wh-btn wh-btn-primary wh-modal-save">Сохранить</button>' +
      '<button type="button" class="wh-btn wh-modal-cancel">Отмена</button>' +
      "</div></div>";
    document.body.appendChild(backdrop);
    function close() {
      backdrop.remove();
    }
    backdrop.querySelector(".wh-modal-close").addEventListener("click", close);
    backdrop.querySelector(".wh-modal-cancel").addEventListener("click", close);
    backdrop.addEventListener("click", function (e) {
      if (e.target === backdrop) close();
    });
    backdrop.querySelector(".wh-modal-save").addEventListener("click", function () {
      onSave(backdrop, close);
    });
    return backdrop;
  }

  function modalDictEditor(title, onDone) {
    var rows = (meta.groups || []).map(function (item) {
      return (
        '<div class="wh-modal-row wh-crm-dict-row" data-id="' + esc(item.id) + '">' +
        '<input type="text" class="wh-crm-dict-name" value="' + esc(item.name) + '" placeholder="Название" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button></div>'
      );
    });
    openModal(
      title,
      '<div id="whStaffGroupRows">' + rows.join("") + "</div>" +
        '<button type="button" class="wh-btn wh-btn-sm" id="whStaffAddGroupRow">+ Добавить</button>',
      function (backdrop, close) {
        var items = [];
        backdrop.querySelectorAll(".wh-crm-dict-row").forEach(function (row) {
          var name = row.querySelector(".wh-crm-dict-name").value.trim();
          if (!name) return;
          var item = { name: name };
          var id = row.getAttribute("data-id");
          if (id) item.id = parseInt(id, 10);
          items.push(item);
        });
        fetchJson("/api/warehouse/employees/groups", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: items }),
        })
          .then(function (data) {
            meta.groups = data.groups || [];
            close();
            if (onDone) onDone();
          })
          .catch(function (err) {
            alert(err.message || "Ошибка сохранения");
          });
      }
    );
    var backdrop = document.querySelector(".wh-modal-backdrop:last-of-type");
    if (!backdrop) return;
    backdrop.querySelector("#whStaffAddGroupRow").addEventListener("click", function () {
      var row = document.createElement("div");
      row.className = "wh-modal-row wh-crm-dict-row";
      row.innerHTML =
        '<input type="text" class="wh-crm-dict-name" placeholder="Название" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>';
      backdrop.querySelector("#whStaffGroupRows").appendChild(row);
    });
    backdrop.addEventListener("click", function (e) {
      if (e.target.classList.contains("wh-crm-dict-remove")) {
        e.target.closest(".wh-crm-dict-row").remove();
      }
    });
  }

  function bindEmployeeGroupSelects(root) {
    ["#whStaffNewGroup", "#whStaffEditGroup"].forEach(function (sel) {
      var el = root.querySelector(sel);
      if (!el) return;
      bindConfigureSelect(el, function () {
        modalDictEditor("Группы сотрудников", function () {
          if (showEmployeeForm) renderEmployeesList();
          else if (editingEmployeeId != null) renderEmployeeEditor();
        });
      });
    });
  }

  function collectPermissionsFromEditor(container) {
    var perms = {};
    container.querySelectorAll("[data-perm-section]").forEach(function (sectionEl) {
      var sectionId = sectionEl.getAttribute("data-perm-section");
      var items = [];
      sectionEl.querySelectorAll("input[data-perm-item]:checked").forEach(function (cb) {
        items.push(cb.getAttribute("data-perm-item"));
      });
      if (items.length) perms[sectionId] = items;
    });
    return perms;
  }

  function buildPermissionsEditor(container, permissions, readOnly) {
    container.innerHTML = "";
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
      var allowed = (permissions && permissions[section.id]) || [];
      section.items.forEach(function (item) {
        var label = document.createElement("label");
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.setAttribute("data-perm-item", item.id);
        cb.checked = allowed.indexOf(item.id) >= 0;
        if (readOnly) cb.disabled = true;
        label.appendChild(cb);
        label.appendChild(document.createTextNode(item.title));
        itemsWrap.appendChild(label);
      });
      block.appendChild(itemsWrap);
      container.appendChild(block);
    });
  }

  function roleLabels(emp) {
    if (!emp.roles || !emp.roles.length) return "—";
    return emp.roles
      .map(function (role) {
        if (role.is_admin) {
          return '<span class="wh-badge wh-badge-admin">' + esc(role.name) + "</span>";
        }
        return esc(role.name);
      })
      .join(", ");
  }

  function roleIdSelected(selectedIds, roleId) {
    var target = String(roleId);
    for (var i = 0; i < selectedIds.length; i++) {
      if (String(selectedIds[i]) === target) return true;
    }
    return false;
  }

  function buildRoleChecks(selectedIds, inputName) {
    var html = '<div class="wh-role-checks">';
    rolesCache.forEach(function (role) {
      var checked = roleIdSelected(selectedIds, role.id) ? " checked" : "";
      html +=
        '<label><input type="checkbox" name="' +
        esc(inputName) +
        '" value="' +
        esc(role.id) +
        '"' +
        checked +
        " /> " +
        esc(role.name) +
        (role.is_admin ? ' <span class="wh-badge wh-badge-admin">полный доступ</span>' : "") +
        "</label>";
    });
    html += "</div>";
    return html;
  }

  function collectRoleIds(root, inputName) {
    var ids = [];
    root.querySelectorAll('input[name="' + inputName + '"]:checked').forEach(function (cb) {
      ids.push(parseInt(cb.value, 10));
    });
    return ids;
  }

  function renderEmployeesList() {
    var root = panelEl();
    var p = global.WH_PAGER;
    var sliced = p ? p.slice(employeesCache, listPage) : { items: employeesCache, state: { page: 1 } };
    listPage = sliced.state.page;
    var rows = sliced.items
      .map(function (emp) {
        var status = emp.is_active
          ? "Активен"
          : '<span class="wh-badge wh-badge-inactive">Неактивен</span>';
        return (
          '<tr data-id="' +
          emp.id +
          '"' +
          (emp.is_active ? "" : ' class="inactive"') +
          ">" +
          "<td>" +
          esc(emp.display_name || "—") +
          "</td>" +
          "<td>" +
          esc(emp.login) +
          "</td>" +
          "<td>" +
          esc(emp.group_name || "—") +
          "</td>" +
          "<td>" +
          esc(emp.telegram_nick || "—") +
          "</td>" +
          "<td>" +
          roleLabels(emp) +
          "</td>" +
          "<td>" +
          status +
          "</td>" +
          "<td>" +
          formatTs(emp.updated_at_ts) +
          "</td>" +
          '<td><button type="button" class="wh-btn wh-btn-sm whStaffEditEmployee">Изменить</button></td>' +
          "</tr>"
        );
      })
      .join("");

    var formHtml = "";
    if (showEmployeeForm) {
      formHtml =
        '<section class="wh-crm-section" id="whStaffEmployeeForm">' +
        "<h4 class=\"wh-crm-section-title\">Новый сотрудник</h4>" +
        '<div class="wh-form-row">' +
        '<div><label>Имя</label><input type="text" id="whStaffNewName" /></div>' +
        '<div><label>Логин</label><input type="text" id="whStaffNewLogin" autocomplete="off" /></div>' +
        '<div><label>Пароль</label><input type="password" id="whStaffNewPassword" autocomplete="new-password" /></div>' +
        '<div><label>Группа</label><select id="whStaffNewGroup" data-prev="">' +
        buildSelectOptions(meta.groups, null) +
        "</select></div>" +
        '<div><label>Ник в Telegram</label><input type="text" id="whStaffNewTelegram" placeholder="@username" /></div>' +
        "</div>" +
        '<div><label>Роли</label>' +
        buildRoleChecks([], "whStaffNewRole") +
        "</div>" +
        '<div class="wh-form-actions">' +
        '<button type="button" class="wh-btn wh-btn-primary" id="whStaffCreateEmployee">Сохранить</button>' +
        '<button type="button" class="wh-btn" id="whStaffCancelCreate">Отмена</button>' +
        "</div>" +
        '<p class="wh-msg" id="whStaffCreateMsg"></p>' +
        "</section>";
    }

    root.innerHTML =
      '<div class="wh-crm-toolbar">' +
      '<input type="search" id="whStaffQuickSearch" class="wh-crm-search" placeholder="Быстрый поиск…" value="' +
      esc(listFilters.q || "") +
      '" />' +
      '<button type="button" class="wh-btn" id="whStaffToggleFilter">Фильтр</button>' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whStaffAddEmployee">+ Добавить сотрудника</button>' +
      "</div>" +
      renderFilterPanel() +
      formHtml +
      '<div id="whStaffListWrap">' +
      (rows
        ? '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
          "<th>Имя</th><th>Логин</th><th>Группа</th><th>Telegram</th><th>Роли</th><th>Статус</th><th>Обновлён</th><th></th>" +
          "</tr></thead><tbody>" +
          rows +
          "</tbody></table>"
        : '<p class="wh-msg">Сотрудники не найдены.</p>') +
      "</div>" +
      (p ? p.html(sliced.state) : "") +
      '<div id="whStaffEmployeeEditor"></div>';

    root.querySelector("#whStaffToggleFilter").addEventListener("click", function () {
      filterPanelOpen = !filterPanelOpen;
      var fp = root.querySelector("#whStaffFilters");
      if (fp) fp.classList.toggle("hidden", !filterPanelOpen);
    });
    root.querySelector("#whStaffApplyFilter").addEventListener("click", function () {
      listFilters = readFilterPanel(root);
      listPage = 1;
      loadEmployees()
        .then(function () {
          renderEmployeesList();
        })
        .catch(function (err) {
          alert(err.message || "Ошибка загрузки");
        });
    });
    root.querySelector("#whStaffResetFilter").addEventListener("click", function () {
      listFilters = {};
      filterPanelOpen = false;
      listPage = 1;
      loadEmployees()
        .then(function () {
          renderEmployeesList();
        })
        .catch(function (err) {
          alert(err.message || "Ошибка загрузки");
        });
    });
    root.querySelector("#whStaffQuickSearch").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        listFilters = readFilterPanel(root);
        listPage = 1;
        loadEmployees()
          .then(function () {
            renderEmployeesList();
          })
          .catch(function (err) {
            alert(err.message || "Ошибка загрузки");
          });
      }
    });

    root.querySelector("#whStaffAddEmployee").addEventListener("click", function () {
      showEmployeeForm = true;
      editingEmployeeId = null;
      renderEmployeesList();
    });

    if (showEmployeeForm) {
      bindEmployeeGroupSelects(root);
      root.querySelector("#whStaffCancelCreate").addEventListener("click", function () {
        showEmployeeForm = false;
        renderEmployeesList();
      });
      root.querySelector("#whStaffCreateEmployee").addEventListener("click", function () {
        var msg = root.querySelector("#whStaffCreateMsg");
        msg.textContent = "";
        msg.className = "wh-msg";
        fetchJson("/api/warehouse/employees", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: root.querySelector("#whStaffNewName").value.trim(),
            login: root.querySelector("#whStaffNewLogin").value.trim(),
            password: root.querySelector("#whStaffNewPassword").value,
            group_id: root.querySelector("#whStaffNewGroup").value || null,
            telegram_nick: root.querySelector("#whStaffNewTelegram").value.trim(),
            role_ids: collectRoleIds(root, "whStaffNewRole"),
            is_active: true,
          }),
        })
          .then(function () {
            showEmployeeForm = false;
            return loadEmployees().then(renderEmployeesList);
          })
          .catch(function (err) {
            msg.className = "wh-msg wh-msg-error";
            msg.textContent = err.message || "Ошибка создания";
          });
      });
    }

    root.querySelectorAll(".whStaffEditEmployee").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var tr = btn.closest("tr[data-id]");
        editingEmployeeId = parseInt(tr.getAttribute("data-id"), 10);
        showEmployeeForm = false;
        renderEmployeeEditor();
      });
    });

    if (global.WH_PAGER) {
      global.WH_PAGER.bind(root, function (delta) {
        listPage += delta;
        renderEmployeesList();
      });
    }

    if (editingEmployeeId != null) renderEmployeeEditor();
  }

  function renderEmployeeEditor() {
    var root = panelEl();
    var editorHost = root.querySelector("#whStaffEmployeeEditor");
    if (!editorHost) return;
    var emp = null;
    for (var i = 0; i < employeesCache.length; i++) {
      if (employeesCache[i].id === editingEmployeeId) {
        emp = employeesCache[i];
        break;
      }
    }
    if (!emp) {
      editorHost.innerHTML = "";
      return;
    }

    editorHost.innerHTML =
      '<section class="wh-employee-editor">' +
      "<h3>Редактирование: " +
      esc(emp.display_name || emp.login) +
      "</h3>" +
      '<div class="wh-form-row">' +
      '<div><label>Имя</label><input type="text" id="whStaffEditName" value="' +
      esc(emp.display_name) +
      '" /></div>' +
      '<div><label>Логин</label><input type="text" id="whStaffEditLogin" value="' +
      esc(emp.login) +
      '" /></div>' +
      '<div><label>Новый пароль</label><input type="password" id="whStaffEditPassword" placeholder="Оставьте пустым, чтобы не менять" autocomplete="new-password" /></div>' +
      '<div><label>Группа</label><select id="whStaffEditGroup" data-prev="' + esc(emp.group_id || "") + '">' +
      buildSelectOptions(meta.groups, emp.group_id) +
      "</select></div>" +
      '<div><label>Ник в Telegram</label><input type="text" id="whStaffEditTelegram" value="' +
      esc(emp.telegram_nick) +
      '" placeholder="@username" /></div>' +
      "</div>" +
      '<label><input type="checkbox" id="whStaffEditActive"' +
      (emp.is_active ? " checked" : "") +
      " /> Активен</label>" +
      '<div class="wh-crm-section"><h4 class="wh-crm-section-title">Роли</h4>' +
      buildRoleChecks(emp.role_ids || [], "whStaffEditRole") +
      "</div>" +
      '<div class="wh-form-actions">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whStaffSaveEmployee">Сохранить</button>' +
      '<button type="button" class="wh-btn" id="whStaffCancelEmployee">Отмена</button>' +
      "</div>" +
      '<p class="wh-msg" id="whStaffEditMsg"></p>' +
      "</section>";

    bindEmployeeGroupSelects(editorHost);

    editorHost.querySelector("#whStaffCancelEmployee").addEventListener("click", function () {
      editingEmployeeId = null;
      editorHost.innerHTML = "";
    });

    editorHost.querySelector("#whStaffSaveEmployee").addEventListener("click", function () {
      var msg = editorHost.querySelector("#whStaffEditMsg");
      msg.textContent = "";
      msg.className = "wh-msg";
      var body = {
        display_name: editorHost.querySelector("#whStaffEditName").value.trim(),
        login: editorHost.querySelector("#whStaffEditLogin").value.trim(),
        password: editorHost.querySelector("#whStaffEditPassword").value,
        group_id: editorHost.querySelector("#whStaffEditGroup").value || null,
        telegram_nick: editorHost.querySelector("#whStaffEditTelegram").value.trim(),
        is_active: editorHost.querySelector("#whStaffEditActive").checked,
        role_ids: collectRoleIds(editorHost, "whStaffEditRole"),
      };
      if (!body.password) delete body.password;
      fetchJson("/api/warehouse/employees/" + emp.id, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (data) {
          for (var j = 0; j < employeesCache.length; j++) {
            if (employeesCache[j].id === emp.id) {
              employeesCache[j] = data.employee;
              break;
            }
          }
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = "Сохранено.";
          if (global.WH_SHELL && global.WH_SHELL.reloadSession) {
            global.WH_SHELL.reloadSession();
          }
          return loadEmployees().then(function () {
            renderEmployeesList();
          });
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || "Ошибка сохранения";
        });
    });
  }

  function renderRolesList() {
    var root = panelEl();
    var rows = rolesCache
      .map(function (role) {
        return (
          '<tr data-id="' +
          role.id +
          '">' +
          "<td>" +
          esc(role.name) +
          (role.is_system ? ' <span class="wh-badge wh-badge-admin">системная</span>' : "") +
          "</td>" +
          "<td>" +
          esc(role.description || "—") +
          "</td>" +
          "<td>" +
          esc(role.member_count || 0) +
          "</td>" +
          "<td>" +
          formatTs(role.updated_at_ts) +
          "</td>" +
          '<td><button type="button" class="wh-btn wh-btn-sm whStaffEditRole">Открыть</button></td>' +
          "</tr>"
        );
      })
      .join("");

    root.innerHTML =
      '<div class="wh-crm-toolbar">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whStaffAddRole">+ Добавить роль</button>' +
      "</div>" +
      '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
      "<th>Название</th><th>Описание</th><th>Сотрудников</th><th>Обновлена</th><th></th>" +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table>" +
      '<div id="whStaffRoleEditor"></div>';

    root.querySelector("#whStaffAddRole").addEventListener("click", function () {
      editingRoleId = "new";
      renderRoleEditor();
    });

    root.querySelectorAll(".whStaffEditRole").forEach(function (btn) {
      btn.addEventListener("click", function () {
        editingRoleId = parseInt(btn.closest("tr[data-id]").getAttribute("data-id"), 10);
        renderRoleEditor();
      });
    });

    if (editingRoleId != null) renderRoleEditor();
  }

  function renderRoleEditor() {
    var root = panelEl();
    var host = root.querySelector("#whStaffRoleEditor");
    if (!host) return;

    if (editingRoleId === "new") {
      host.innerHTML =
        '<section class="wh-employee-editor">' +
        "<h3>Новая роль</h3>" +
        '<div class="wh-form-row">' +
        '<div class="wh-form-wide"><label>Название</label><input type="text" id="whStaffRoleName" /></div>' +
        '<div class="wh-form-wide"><label>Описание</label><input type="text" id="whStaffRoleDesc" /></div>' +
        '<div class="wh-form-wide"><label>Комментарий</label><input type="text" id="whStaffRoleComment" /></div>' +
        "</div>" +
        '<h4 class="wh-crm-section-title">Доступ к разделам и подразделам</h4>' +
        '<div class="wh-permissions-grid" id="whStaffRolePerms"></div>' +
        '<div class="wh-form-actions">' +
        '<button type="button" class="wh-btn wh-btn-primary" id="whStaffSaveRole">Сохранить</button>' +
        '<button type="button" class="wh-btn" id="whStaffCancelRole">Отмена</button>' +
        "</div>" +
        '<p class="wh-msg" id="whStaffRoleMsg"></p>' +
        "</section>";
      buildPermissionsEditor(host.querySelector("#whStaffRolePerms"), {}, false);
      host.querySelector("#whStaffCancelRole").addEventListener("click", function () {
        editingRoleId = null;
        host.innerHTML = "";
      });
      host.querySelector("#whStaffSaveRole").addEventListener("click", function () {
        saveRole(host, null);
      });
      return;
    }

    fetchJson("/api/warehouse/roles/" + editingRoleId)
      .then(function (data) {
        var role = data.role;
        var membersHtml = (role.members || []).length
          ? '<ul class="wh-role-members">' +
            role.members
              .map(function (m) {
                return "<li>" + esc(m.display_name || m.login) + " (" + esc(m.login) + ")</li>";
              })
              .join("") +
            "</ul>"
          : '<p class="wh-msg">В этой роли пока нет сотрудников.</p>';

        host.innerHTML =
          '<section class="wh-employee-editor">' +
          "<h3>Роль: " +
          esc(role.name) +
          "</h3>" +
          '<div class="wh-form-row">' +
          '<div class="wh-form-wide"><label>Название</label><input type="text" id="whStaffRoleName" value="' +
          esc(role.name) +
          '"' +
          (role.is_system ? " readonly" : "") +
          " /></div>" +
          '<div class="wh-form-wide"><label>Описание</label><input type="text" id="whStaffRoleDesc" value="' +
          esc(role.description) +
          '" /></div>' +
          '<div class="wh-form-wide"><label>Комментарий</label><input type="text" id="whStaffRoleComment" value="' +
          esc(role.comment) +
          '" /></div>' +
          "</div>" +
          '<div class="wh-crm-section"><h4 class="wh-crm-section-title">Сотрудники в роли</h4>' +
          membersHtml +
          "</div>" +
          '<h4 class="wh-crm-section-title">Доступ к разделам и подразделам</h4>' +
          (role.is_admin
            ? '<p class="wh-msg">У роли «Админ» полный доступ ко всем разделам.</p>'
            : '<div class="wh-permissions-grid" id="whStaffRolePerms"></div>') +
          '<div class="wh-form-actions">' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whStaffSaveRole">Сохранить</button>' +
          (role.is_system
            ? ""
            : '<button type="button" class="wh-btn wh-btn-danger" id="whStaffDeleteRole">Удалить</button>') +
          '<button type="button" class="wh-btn" id="whStaffCancelRole">Отмена</button>' +
          "</div>" +
          '<p class="wh-msg" id="whStaffRoleMsg"></p>' +
          "</section>";

        if (!role.is_admin) {
          buildPermissionsEditor(host.querySelector("#whStaffRolePerms"), role.permissions || {}, false);
        }

        host.querySelector("#whStaffCancelRole").addEventListener("click", function () {
          editingRoleId = null;
          host.innerHTML = "";
        });
        host.querySelector("#whStaffSaveRole").addEventListener("click", function () {
          saveRole(host, role.id);
        });
        if (!role.is_system) {
          host.querySelector("#whStaffDeleteRole").addEventListener("click", function () {
            if (!confirm("Удалить роль «" + role.name + "»?")) return;
            fetchJson("/api/warehouse/roles/" + role.id, { method: "DELETE" })
              .then(function () {
                editingRoleId = null;
                return loadRoles().then(renderRolesList);
              })
              .catch(function (err) {
                var msg = host.querySelector("#whStaffRoleMsg");
                msg.className = "wh-msg wh-msg-error";
                msg.textContent = err.message || "Ошибка удаления";
              });
          });
        }
      })
      .catch(function (err) {
        host.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function saveRole(host, roleId) {
    var msg = host.querySelector("#whStaffRoleMsg");
    msg.textContent = "";
    msg.className = "wh-msg";
    var body = {
      name: host.querySelector("#whStaffRoleName").value.trim(),
      description: host.querySelector("#whStaffRoleDesc").value.trim(),
      comment: host.querySelector("#whStaffRoleComment").value.trim(),
    };
    var permGrid = host.querySelector("#whStaffRolePerms");
    if (permGrid) body.permissions = collectPermissionsFromEditor(permGrid);
    var req = roleId
      ? fetchJson("/api/warehouse/roles/" + roleId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      : fetchJson("/api/warehouse/roles", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
    req
      .then(function () {
        editingRoleId = null;
        return loadRoles().then(renderRolesList);
      })
      .catch(function (err) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Ошибка сохранения";
      });
  }

  function renderEmployees(tab, item) {
    preparePanel(tab, item);
    editingEmployeeId = null;
    showEmployeeForm = false;
    listPage = 1;
    panelEl().innerHTML = '<p class="wh-msg">Загрузка…</p>';
    Promise.all([loadMeta(), loadRoles(), loadEmployees()])
      .then(function () {
        renderEmployeesList();
      })
      .catch(function (err) {
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function renderRoles(tab, item) {
    preparePanel(tab, item);
    editingRoleId = null;
    var card = document.querySelector(".wh-content-card");
    if (card) card.classList.add("wh-content-card--wide");
    panelEl().innerHTML = '<p class="wh-msg">Загрузка…</p>';
    Promise.all([loadPermissionsSchema(), loadRoles()])
      .then(function () {
        renderRolesList();
      })
      .catch(function (err) {
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  var scheduleYear = 0;
  var scheduleMonth = 0;
  var scheduleUserId = null;
  var scheduleEmployees = [];
  var scheduleDaysCache = {};
  var scheduleLoadSeq = 0;

  var SCHEDULE_MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
  ];
  var SCHEDULE_WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  function schedulePad2(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function scheduleDayKey(year, month, day) {
    return year + "-" + schedulePad2(month) + "-" + schedulePad2(day);
  }

  function buildScheduleCalendarCells(year, month) {
    var first = new Date(year, month - 1, 1);
    var startDow = (first.getDay() + 6) % 7;
    var daysInMonth = new Date(year, month, 0).getDate();
    var cells = [];
    var i;
    for (i = 0; i < startDow; i++) cells.push(null);
    for (i = 1; i <= daysInMonth; i++) cells.push(i);
    while (cells.length % 7 !== 0) cells.push(null);
    return cells;
  }

  function shiftScheduleMonth(delta) {
    var now = new Date();
    if (!scheduleYear || !scheduleMonth) {
      scheduleYear = now.getFullYear();
      scheduleMonth = now.getMonth() + 1;
    }
    scheduleMonth += delta;
    while (scheduleMonth < 1) {
      scheduleMonth += 12;
      scheduleYear -= 1;
    }
    while (scheduleMonth > 12) {
      scheduleMonth -= 12;
      scheduleYear += 1;
    }
  }

  function loadScheduleEmployees() {
    return fetchJson("/api/warehouse/employees?is_active=1").then(function (data) {
      var all = data.employees || [];
      var packers = all.filter(function (emp) {
        return /упаков/i.test(String(emp.group_name || ""));
      });
      scheduleEmployees = packers.length ? packers : all;
      return scheduleEmployees;
    });
  }

  function scheduleShortName(name) {
    var parts = String(name || "").trim().split(/\s+/);
    if (!parts.length || !parts[0]) return "—";
    if (parts.length === 1) return parts[0];
    return parts[0] + " " + parts[1].charAt(0) + ".";
  }

  function updateScheduleMonthTitle(root) {
    var title = root.querySelector("#whStaffCalTitle");
    if (title) {
      title.textContent = SCHEDULE_MONTH_NAMES[scheduleMonth - 1] + " " + scheduleYear;
    }
  }

  function applyScheduleSelectionHighlight(root) {
    root.querySelectorAll(".wh-staff-cal-name").forEach(function (el) {
      var uid = parseInt(el.getAttribute("data-user-id"), 10);
      el.classList.toggle("wh-staff-cal-name--selected", scheduleUserId && uid === scheduleUserId);
    });
  }

  function renderScheduleDayCell(dayNum) {
    var key = scheduleDayKey(scheduleYear, scheduleMonth, dayNum);
    var info = scheduleDaysCache[key] || {};
    var staff = info.staff || [];
    var hasShift = staff.length > 0;
    var namesHtml = staff
      .map(function (s) {
        var selected = scheduleUserId === s.id ? " wh-staff-cal-name--selected" : "";
        return (
          '<span class="wh-staff-cal-name' +
          selected +
          '" data-user-id="' +
          esc(s.id) +
          '">' +
          esc(scheduleShortName(s.name)) +
          "</span>"
        );
      })
      .join("");
    var shiftClass = hasShift ? " wh-staff-cal-cell--has-shift" : "";
    return (
      '<button type="button" class="wh-task-cal-cell wh-staff-cal-cell' +
      shiftClass +
      '" data-date="' +
      esc(key) +
      '">' +
      '<span class="wh-task-cal-day">' +
      esc(dayNum) +
      "</span>" +
      '<div class="wh-staff-cal-names">' +
      namesHtml +
      "</div>" +
      "</button>"
    );
  }

  function bindScheduleDayClicks(root) {
    root.querySelectorAll(".wh-staff-cal-cell[data-date]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var date = btn.getAttribute("data-date");
        var msg = root.querySelector("#whStaffScheduleMsg");
        if (!date) return;
        if (!scheduleUserId) {
          if (msg) {
            msg.className = "wh-msg wh-msg-error";
            msg.textContent = "Сначала выберите упаковщика в списке выше.";
          }
          return;
        }
        if (msg) {
          msg.textContent = "";
          msg.className = "wh-msg";
        }
        btn.disabled = true;
        fetchJson("/api/warehouse/employees/schedule/toggle", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_id: scheduleUserId, date: date }),
        })
          .then(function () {
            return refreshScheduleGrid(root);
          })
          .catch(function (err) {
            btn.disabled = false;
            if (msg) {
              msg.className = "wh-msg wh-msg-error";
              msg.textContent = err.message || "Ошибка сохранения";
            }
          });
      });
    });
  }

  function refreshScheduleGrid(root) {
    var grid = root.querySelector("#whStaffScheduleGrid");
    if (!grid) return Promise.resolve();
    grid.innerHTML = '<div class="wh-task-cal-weekday" style="grid-column:1/-1">Загрузка…</div>';
    return fetchJson(
      "/api/warehouse/employees/schedule/calendar?year=" +
        encodeURIComponent(scheduleYear) +
        "&month=" +
        encodeURIComponent(scheduleMonth)
    )
      .then(function (data) {
        scheduleDaysCache = data.days || {};
        updateScheduleMonthTitle(root);
        var cells = buildScheduleCalendarCells(scheduleYear, scheduleMonth);
        var weekdayHead = SCHEDULE_WEEKDAY_NAMES.map(function (name) {
          return '<div class="wh-task-cal-weekday">' + esc(name) + "</div>";
        }).join("");
        var cellHtml = cells
          .map(function (dayNum) {
            if (!dayNum) {
              return '<div class="wh-task-cal-cell wh-task-cal-cell--empty"></div>';
            }
            return renderScheduleDayCell(dayNum);
          })
          .join("");
        grid.innerHTML = weekdayHead + cellHtml;
        bindScheduleDayClicks(root);
        applyScheduleSelectionHighlight(root);
      })
      .catch(function (err) {
        grid.innerHTML =
          '<div class="wh-task-cal-weekday" style="grid-column:1/-1;color:#c00">' +
          esc(err.message) +
          "</div>";
      });
  }

  function mountScheduleCalendar(root) {
    var calWrap = root.querySelector("#whStaffScheduleCal");
    if (!calWrap || calWrap.querySelector("#whStaffScheduleGrid")) return;
    calWrap.innerHTML =
      '<div class="wh-task-cal-toolbar">' +
      '<button type="button" class="wh-btn" id="whStaffCalPrev">&larr;</button>' +
      '<h3 id="whStaffCalTitle"></h3>' +
      '<button type="button" class="wh-btn" id="whStaffCalNext">&rarr;</button>' +
      '<button type="button" class="wh-btn" id="whStaffCalToday">Сегодня</button>' +
      "</div>" +
      '<div class="wh-task-cal-grid" id="whStaffScheduleGrid"></div>' +
      '<p class="wh-muted">Календарь показывает все смены упаковщиков. Выберите человека и кликните по дню, чтобы назначить или снять смену.</p>';
    calWrap.querySelector("#whStaffCalPrev").addEventListener("click", function () {
      shiftScheduleMonth(-1);
      refreshScheduleGrid(root);
    });
    calWrap.querySelector("#whStaffCalNext").addEventListener("click", function () {
      shiftScheduleMonth(1);
      refreshScheduleGrid(root);
    });
    calWrap.querySelector("#whStaffCalToday").addEventListener("click", function () {
      var now = new Date();
      scheduleYear = now.getFullYear();
      scheduleMonth = now.getMonth() + 1;
      refreshScheduleGrid(root);
    });
    updateScheduleMonthTitle(root);
  }

  function renderScheduleEmployeePicker(root) {
    var listEl = root.querySelector("#whStaffScheduleList");
    if (!listEl) return;
    if (!scheduleEmployees.length) {
      listEl.innerHTML = '<p class="wh-msg">Нет активных упаковщиков. Добавьте сотрудников или укажите группу «Упаковщики».</p>';
      return;
    }
    listEl.innerHTML = scheduleEmployees
      .map(function (emp) {
        var name = emp.display_name || emp.login || "—";
        var active = scheduleUserId === emp.id ? " wh-staff-schedule-pick--active" : "";
        return (
          '<button type="button" class="wh-staff-schedule-pick' +
          active +
          '" data-id="' +
          esc(emp.id) +
          '">' +
          esc(name) +
          "</button>"
        );
      })
      .join("");
    listEl.querySelectorAll(".wh-staff-schedule-pick").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = parseInt(btn.getAttribute("data-id"), 10) || null;
        scheduleUserId = scheduleUserId === id ? null : id;
        renderScheduleEmployeePicker(root);
        applyScheduleSelectionHighlight(root);
        var msg = root.querySelector("#whStaffScheduleMsg");
        if (msg) {
          msg.textContent = "";
          msg.className = "wh-msg";
        }
      });
    });
  }

  function renderSchedule(tab, item) {
    preparePanel(tab, item);
    var card = document.querySelector(".wh-content-card");
    if (card) card.classList.add("wh-content-card--wide");
    var root = panelEl();
    var now = new Date();
    if (!scheduleYear) scheduleYear = now.getFullYear();
    if (!scheduleMonth) scheduleMonth = now.getMonth() + 1;
    var loadSeq = ++scheduleLoadSeq;
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    loadScheduleEmployees()
      .then(function () {
        if (loadSeq !== scheduleLoadSeq) return;
        root.innerHTML =
          '<div class="wh-staff-schedule">' +
          '<p class="wh-muted wh-task-summary-hint">График смен упаковщиков. В каждом дне отображаются все назначенные на смену. На одну дату можно назначить нескольких человек.</p>' +
          '<div class="wh-staff-schedule-picker-wrap">' +
          '<h4 class="wh-crm-section-title">Упаковщики</h4>' +
          '<div class="wh-staff-schedule-list" id="whStaffScheduleList"></div>' +
          "</div>" +
          '<div id="whStaffScheduleCal"></div>' +
          '<p class="wh-msg" id="whStaffScheduleMsg"></p>' +
          "</div>";
        mountScheduleCalendar(root);
        renderScheduleEmployeePicker(root);
        return refreshScheduleGrid(root);
      })
      .catch(function (err) {
        if (loadSeq !== scheduleLoadSeq) return;
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  global.WhStaff = {
    renderEmployees: renderEmployees,
    renderRoles: renderRoles,
    renderSchedule: renderSchedule,
  };
})(window);

(function (global) {
  function currentMonthRange() {
    var now = new Date();
    var from = new Date(now.getFullYear(), now.getMonth(), 1);
    var to = new Date(now.getFullYear(), now.getMonth() + 1, 0);
    function iso(value) {
      var month = String(value.getMonth() + 1);
      var day = String(value.getDate());
      if (month.length < 2) month = "0" + month;
      if (day.length < 2) day = "0" + day;
      return value.getFullYear() + "-" + month + "-" + day;
    }
    return { date_from: iso(from), date_to: iso(to) };
  }

  var filters = currentMonthRange();
  var rows = [];
  var employees = [];
  var taskTypes = [];
  var totalQuantity = 0;
  var totalPay = "";
  var page = 1;

  function shell() {
    return global.WH_SHELL || {};
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function ensureStyles() {
    if (document.getElementById("whProductivityStyles")) return;
    var style = document.createElement("style");
    style.id = "whProductivityStyles";
    style.textContent =
      ".wh-prod-shell{display:grid;gap:16px}" +
      ".wh-prod-filter-card{padding:16px;border:1px solid #dfe5ee;border-radius:10px;background:#fff;box-shadow:0 1px 2px rgba(15,23,42,.04)}" +
      ".wh-prod-search-row{display:grid;grid-template-columns:minmax(180px,1fr) auto auto auto auto;gap:10px;align-items:center}" +
      ".wh-prod-search{box-sizing:border-box;width:100%;height:38px;padding:0 12px;border:1px solid #cbd5e1;border-radius:7px;background:#fff;font:inherit}" +
      ".wh-prod-filter-grid{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;margin-top:14px;padding-top:14px;border-top:1px solid #edf0f4}" +
      ".wh-prod-field{min-width:0}.wh-prod-field--wide{grid-column:span 2}" +
      ".wh-prod-field label{display:block;margin:0 0 5px;color:#526071;font-size:12px;font-weight:600}" +
      ".wh-prod-field input,.wh-prod-field select{box-sizing:border-box;width:100%;height:36px;padding:0 10px;border:1px solid #cbd5e1;border-radius:7px;background:#fff;color:#182230;font:inherit}" +
      ".wh-prod-field input:focus,.wh-prod-field select:focus,.wh-prod-search:focus{border-color:#5989d7;outline:2px solid rgba(47,111,211,.12)}" +
      ".wh-productivity-summary{display:grid;grid-template-columns:repeat(3,minmax(160px,240px));gap:12px}" +
      ".wh-prod-stat{padding:14px 16px;border:1px solid #dfe5ee;border-radius:10px;background:#f8fafc}" +
      ".wh-prod-stat-label{display:block;margin-bottom:3px;color:#64748b;font-size:12px}" +
      ".wh-prod-stat-value{color:#172033;font-size:24px;font-weight:700;line-height:1.2}" +
      ".wh-prod-table-wrap{overflow:auto;border:1px solid #dfe5ee;border-radius:10px;background:#fff}" +
      ".wh-productivity-table{width:100%;min-width:860px;margin:0!important;border:0!important;border-collapse:separate!important;border-spacing:0}" +
      ".wh-productivity-table thead th{position:sticky;top:0;z-index:1;padding:11px 14px;border-bottom:1px solid #dfe5ee;background:#f5f7fa;color:#536174;font-size:12px;font-weight:700;text-align:left;white-space:nowrap}" +
      ".wh-productivity-table tbody td{padding:14px;border-bottom:1px solid #edf0f4;vertical-align:middle}" +
      ".wh-productivity-table tbody tr:last-child td{border-bottom:0}.wh-productivity-table tbody tr:hover td{background:#f8fbff}" +
      ".wh-prod-main-row.is-excluded td,.wh-productivity-table tbody tr.wh-prod-detail-row.is-excluded .wh-prod-detail-cell{background:#eceff3!important;color:#8a93a0}" +
      ".wh-productivity-table tbody tr.wh-prod-main-row.is-excluded:hover td{background:#e4e8ee!important}" +
      ".wh-prod-main-row.is-excluded .wh-prod-date,.wh-prod-main-row.is-excluded .wh-prod-employee,.wh-prod-main-row.is-excluded .wh-prod-qty-number,.wh-prod-main-row.is-excluded .wh-prod-qty-unit,.wh-prod-main-row.is-excluded .wh-prod-task-data,.wh-prod-main-row.is-excluded .wh-prod-pay{color:#8a93a0}" +
      ".wh-prod-main-row.is-excluded .wh-prod-task-badge{background:#e2e6ec;color:#6b7280}" +
      ".wh-prod-main-row.is-excluded .wh-prod-edit,.wh-prod-main-row.is-excluded .wh-prod-details-toggle{color:#6b7280}" +
      ".wh-prod-date{display:inline-block;min-width:82px;color:#334155;font-variant-numeric:tabular-nums;white-space:nowrap}" +
      ".wh-prod-employee{color:#172033;font-weight:600}" +
      ".wh-prod-edit{display:block;margin-top:4px;padding:0;border:0;background:transparent;color:#2f6fd3;font:inherit;font-size:12px;cursor:pointer}.wh-prod-edit:hover{text-decoration:underline}" +
      ".wh-productivity-qty{width:120px;text-align:center!important;white-space:nowrap}" +
      ".wh-prod-qty-number{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}.wh-prod-qty-unit{margin-left:4px;color:#64748b;font-size:12px}" +
      ".wh-prod-pay-cell{width:130px;text-align:right!important;white-space:nowrap}" +
      ".wh-prod-pay{color:#1d4d3b;font-size:16px;font-weight:700;font-variant-numeric:tabular-nums}" +
      ".wh-prod-pay--empty{color:#94a3b8;font-size:13px;font-weight:500}.wh-prod-pay--off{color:#6b7280;font-size:12px;font-weight:600}" +
      ".wh-prod-task{min-width:320px}.wh-prod-task-badge{display:inline-block;margin-bottom:5px;padding:3px 8px;border-radius:999px;background:#e8f0ff;color:#285ea8;font-size:11px;font-weight:700}" +
      ".wh-prod-task-badge--wb_fbo,.wh-prod-task-badge--wb_fbo_new,.wh-prod-task-badge--ym_fbo{background:#eee9ff;color:#6045a8}.wh-prod-task-badge--vseinstrumenti{background:#e4f6eb;color:#287544}.wh-prod-task-badge--rework{background:#fff3e0;color:#9a5b12}" +
      ".wh-prod-task-data{color:#526071;font-size:13px;line-height:1.4}.wh-prod-empty{padding:40px 20px;text-align:center;color:#64748b}" +
      ".wh-prod-details-toggle{display:inline-flex;align-items:center;gap:5px;margin-top:7px;padding:3px 0;border:0;background:transparent;color:#2f6fd3;font:inherit;font-size:12px;font-weight:600;cursor:pointer}.wh-prod-details-toggle:hover{text-decoration:underline}" +
      ".wh-prod-details-caret{display:inline-block;font-size:11px;transition:transform .15s ease}.wh-prod-details-toggle[aria-expanded=true] .wh-prod-details-caret{transform:rotate(90deg)}" +
      ".wh-prod-detail-row[hidden]{display:none}.wh-productivity-table tbody .wh-prod-detail-cell{padding:0!important;background:#f8fafc!important}" +
      ".wh-prod-detail-panel{padding:12px 16px 16px 48px;border-bottom:1px solid #dfe5ee}.wh-prod-detail-status{padding:8px 0;color:#64748b;font-size:13px}" +
      ".wh-prod-detail-table{width:100%;border-collapse:collapse;font-size:13px}.wh-prod-detail-table th{padding:7px 10px;border-bottom:1px solid #dbe3ee;color:#64748b;font-size:11px;text-align:left;white-space:nowrap}.wh-prod-detail-table td{padding:9px 10px!important;border-bottom:1px solid #e8edf3!important;background:transparent!important;vertical-align:top!important}.wh-prod-detail-table tr:last-child td{border-bottom:0!important}" +
      ".wh-prod-detail-time{width:150px;font-variant-numeric:tabular-nums;white-space:nowrap}.wh-prod-detail-qty{width:80px;text-align:right!important;font-weight:700;font-variant-numeric:tabular-nums}.wh-prod-detail-product{color:#526071}.wh-prod-detail-ref{font-weight:600;color:#253247}" +
      ".wh-prod-reassign-info{margin:0 0 14px;color:#526071;line-height:1.5}.wh-prod-reassign-field{margin-bottom:12px}.wh-prod-reassign-field label{display:block;margin-bottom:6px;font-size:12px;font-weight:600;color:#526071}.wh-prod-reassign-field select,.wh-prod-reassign-field input[type=date],.wh-prod-reassign-field input[type=number],.wh-prod-reassign-field textarea{box-sizing:border-box;width:100%;padding:0 10px;border:1px solid #cbd5e1;border-radius:7px;background:#fff;font:inherit}.wh-prod-reassign-field select,.wh-prod-reassign-field input[type=date],.wh-prod-reassign-field input[type=number]{height:38px}.wh-prod-reassign-field textarea{min-height:88px;padding:8px 10px;resize:vertical}" +
      ".wh-prod-rate-list{display:grid;gap:12px}.wh-prod-rate-row{display:grid;grid-template-columns:minmax(140px,1fr) 140px;gap:10px;align-items:center}.wh-prod-rate-row label{margin:0;color:#334155;font-size:14px;font-weight:600}.wh-prod-rate-row input{box-sizing:border-box;width:100%;height:38px;padding:0 10px;border:1px solid #cbd5e1;border-radius:7px;background:#fff;font:inherit}" +
      ".wh-prod-rate-hint{margin:0 0 14px;color:#64748b;font-size:13px;line-height:1.45}" +
      ".wh-prod-drop{position:relative}.wh-prod-drop-toggle{box-sizing:border-box;width:100%;height:38px;padding:0 10px;border:1px solid #cbd5e1;border-radius:7px;background:#fff;text-align:left;font:inherit;cursor:pointer}.wh-prod-drop-toggle:after{content:'▾';float:right;color:#64748b}.wh-prod-drop-menu{position:absolute;z-index:5;left:0;right:0;top:40px;max-height:240px;overflow:auto;padding:6px 0;border:1px solid #cbd5e1;border-radius:7px;background:#fff;box-shadow:0 8px 20px rgba(15,23,42,.12)}.wh-prod-drop-option{display:flex;align-items:center;gap:8px;padding:6px 10px;font-size:14px;cursor:pointer}.wh-prod-drop-option:hover{background:#f8fbff}.wh-prod-packers{margin-top:4px;color:#64748b;font-size:12px;line-height:1.35}" +
      "@media(max-width:900px){.wh-prod-filter-grid{grid-template-columns:repeat(2,minmax(140px,1fr))}.wh-prod-field--wide{grid-column:span 2}}" +
      "@media(max-width:620px){.wh-prod-search-row{grid-template-columns:1fr 1fr}.wh-prod-search{grid-column:1/-1}.wh-prod-filter-grid{grid-template-columns:1fr}.wh-prod-field--wide{grid-column:auto}.wh-productivity-summary{grid-template-columns:1fr 1fr}.wh-prod-detail-panel{padding-left:10px}.wh-prod-rate-row{grid-template-columns:1fr}}";
    document.head.appendChild(style);
  }

  function query() {
    var parts = [];
    Object.keys(filters).forEach(function (key) {
      if (filters[key] !== "" && filters[key] != null) {
        parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(filters[key]));
      }
    });
    return parts.length ? "?" + parts.join("&") : "";
  }

  function load() {
    return Promise.all([
      shell().fetchJson("/api/warehouse/employees/productivity" + query()),
      shell().fetchJson("/api/warehouse/employees"),
    ]).then(function (result) {
      var data = result[0] || {};
      rows = data.rows || [];
      totalQuantity = Number(data.total_quantity || 0);
      totalPay = data.total_pay == null ? "" : String(data.total_pay);
      taskTypes = data.task_types || [];
      employees = (result[1] && result[1].employees) || [];
    });
  }

  function optionList(items, selected, valueKey, nameKey) {
    var html = '<option value="">Все</option>';
    (items || []).forEach(function (item) {
      var value = item[valueKey];
      var selectedAttr = String(selected || "") === String(value) ? " selected" : "";
      html +=
        '<option value="' +
        esc(value) +
        '"' +
        selectedAttr +
        ">" +
        esc(item[nameKey] || item.login || value) +
        "</option>";
    });
    return html;
  }

  function filterHtml() {
    return (
      '<div class="wh-prod-filter-card">' +
      '<div class="wh-prod-search-row">' +
      '<input type="search" id="whProductivityQ" class="wh-prod-search" placeholder="Поиск по сотруднику и данным задачи" value="' +
      esc(filters.q || "") +
      '" />' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whProductivityApply">Применить</button>' +
      '<button type="button" class="wh-btn" id="whProductivityReset">Сбросить</button>' +
      '<button type="button" class="wh-btn" id="whProductivityAdd">Добавить строку</button>' +
      '<button type="button" class="wh-btn" id="whProductivitySettings">Настройки</button>' +
      "</div>" +
      '<div class="wh-prod-filter-grid" id="whProductivityFilters">' +
      '<div class="wh-prod-field"><label>Дата с</label><input type="date" data-filter="date_from" value="' +
      esc(filters.date_from || "") +
      '" /></div>' +
      '<div class="wh-prod-field"><label>Дата по</label><input type="date" data-filter="date_to" value="' +
      esc(filters.date_to || "") +
      '" /></div>' +
      '<div class="wh-prod-field"><label>Сотрудник</label><select data-filter="user_id">' +
      optionList(employees, filters.user_id, "id", "display_name") +
      "</select></div>" +
      '<div class="wh-prod-field"><label>Тип задачи</label><select data-filter="task_type">' +
      optionList(taskTypes, filters.task_type, "id", "name") +
      "</select></div>" +
      '<div class="wh-prod-field wh-prod-field--wide"><label>Данные задачи</label><input type="text" data-filter="task_query" value="' +
      esc(filters.task_query || "") +
      '" placeholder="№ задания, поставка, заказ, склад" /></div>' +
      '<div class="wh-prod-field"><label>Количество от</label><input type="number" min="0" data-filter="min_quantity" value="' +
      esc(filters.min_quantity || "") +
      '" /></div>' +
      '<div class="wh-prod-field"><label>Количество до</label><input type="number" min="0" data-filter="max_quantity" value="' +
      esc(filters.max_quantity || "") +
      '" /></div>' +
      "</div></div>"
    );
  }

  function readFilters(root) {
    var next = {};
    var q = root.querySelector("#whProductivityQ");
    if (q && q.value.trim()) next.q = q.value.trim();
    root.querySelectorAll("[data-filter]").forEach(function (el) {
      if (el.value.trim()) next[el.getAttribute("data-filter")] = el.value.trim();
    });
    return next;
  }

  function employeeOptions(selectedId) {
    return employees
      .map(function (employee) {
        var selected = String(employee.id) === String(selectedId) ? " selected" : "";
        var name = employee.display_name || employee.login || "Сотрудник #" + employee.id;
        if (employee.is_active === false) name += " (неактивен)";
        return '<option value="' + esc(employee.id) + '"' + selected + ">" + esc(name) + "</option>";
      })
      .join("");
  }

  function packersToggleLabel(selectedIds) {
    var names = employees
      .filter(function (employee) {
        return selectedIds.indexOf(Number(employee.id)) >= 0;
      })
      .map(function (employee) {
        return employee.display_name || employee.login || "Сотрудник #" + employee.id;
      });
    if (!names.length) return "Не выбрано";
    if (names.length === 1) return names[0];
    return names[0] + " и ещё " + (names.length - 1);
  }

  function openPackersEditor(row, root) {
    var selected = (row.packer_user_ids || []).map(Number);
    var others = employees.filter(function (employee) {
      return Number(employee.id) !== Number(row.user_id) && employee.is_active !== false;
    });
    var backdrop = document.createElement("div");
    backdrop.className = "wh-modal-backdrop";
    backdrop.innerHTML =
      '<div class="wh-modal" role="dialog" aria-modal="true">' +
      '<div class="wh-modal-header"><h3>Упаковщики задания</h3><button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body">' +
      '<p class="wh-prod-reassign-info"><strong>' +
      esc(row.employee) +
      "</strong><br>" +
      esc(row.task_type_name) +
      " · " +
      esc(row.task_data) +
      "<br>Выработка делится поровну между этим сотрудником и выбранными. Чужие операции по этому заданию не делятся.</p>" +
      '<div class="wh-prod-reassign-field"><label>Дополнительные упаковщики</label>' +
      '<div class="wh-prod-drop"><button type="button" class="wh-prod-drop-toggle" id="whProdPackersToggle">' +
      esc(packersToggleLabel(selected)) +
      '</button><div class="wh-prod-drop-menu" id="whProdPackersMenu" hidden>' +
      others
        .map(function (employee) {
          var checked = selected.indexOf(Number(employee.id)) >= 0 ? " checked" : "";
          return (
            '<label class="wh-prod-drop-option"><input type="checkbox" value="' +
            esc(employee.id) +
            '"' +
            checked +
            " /> " +
            esc(employee.display_name || employee.login || "Сотрудник #" + employee.id) +
            "</label>"
          );
        })
        .join("") +
      (others.length ? "" : '<div class="wh-prod-drop-option">Нет других сотрудников</div>') +
      "</div></div></div>" +
      '<p class="wh-msg" id="whProdPackersMsg"></p></div>' +
      '<div class="wh-modal-footer"><button type="button" class="wh-btn wh-btn-primary wh-modal-save">Сохранить</button>' +
      '<button type="button" class="wh-btn wh-modal-cancel">Отмена</button></div></div>';
    document.body.appendChild(backdrop);

    function close() {
      backdrop.remove();
    }
    function currentIds() {
      return Array.prototype.slice
        .call(backdrop.querySelectorAll("#whProdPackersMenu input[type=checkbox]:checked"))
        .map(function (input) {
          return parseInt(input.value, 10);
        })
        .filter(function (id) {
          return id > 0;
        });
    }
    backdrop.querySelector(".wh-modal-close").addEventListener("click", close);
    backdrop.querySelector(".wh-modal-cancel").addEventListener("click", close);
    backdrop.addEventListener("click", function (event) {
      if (event.target === backdrop) close();
    });
    var toggle = backdrop.querySelector("#whProdPackersToggle");
    var menu = backdrop.querySelector("#whProdPackersMenu");
    toggle.addEventListener("click", function () {
      menu.hidden = !menu.hidden;
    });
    menu.addEventListener("change", function () {
      toggle.textContent = packersToggleLabel(currentIds());
    });
    backdrop.querySelector(".wh-modal-save").addEventListener("click", function () {
      var save = backdrop.querySelector(".wh-modal-save");
      var msg = backdrop.querySelector("#whProdPackersMsg");
      save.disabled = true;
      msg.className = "wh-msg";
      msg.textContent = "Сохранение…";
      shell()
        .fetchJson("/api/warehouse/employees/productivity/packers", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            date: row.date,
            task_type: row.task_type,
            task_id: row.task_id,
            user_id: row.user_id,
            user_ids: currentIds(),
          }),
        })
        .then(function () {
          close();
          return refresh(root);
        })
        .catch(function (error) {
          save.disabled = false;
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = error.message || "Не удалось сохранить упаковщиков";
        });
    });
  }

  function openEmployeeEditor(row, root) {
    var backdrop = document.createElement("div");
    backdrop.className = "wh-modal-backdrop";
    backdrop.innerHTML =
      '<div class="wh-modal" role="dialog" aria-modal="true">' +
      '<div class="wh-modal-header"><h3>Изменить сотрудника</h3><button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body">' +
      '<p class="wh-prod-reassign-info"><strong>' +
      esc(row.task_type_name) +
      "</strong><br>" +
      esc(row.task_data) +
      "<br>" +
      esc(row.quantity) +
      " шт. за " +
      esc(row.date) +
      "</p>" +
      '<div class="wh-prod-reassign-field"><label for="whProdNewEmployee">Сотрудник</label><select id="whProdNewEmployee">' +
      employeeOptions(row.user_id) +
      "</select></div>" +
      '<p class="wh-msg" id="whProdReassignMsg"></p>' +
      "</div>" +
      '<div class="wh-modal-footer"><button type="button" class="wh-btn wh-btn-primary wh-modal-save">Сохранить</button>' +
      '<button type="button" class="wh-btn wh-modal-cancel">Отмена</button></div></div>';
    document.body.appendChild(backdrop);

    function close() {
      backdrop.remove();
    }
    backdrop.querySelector(".wh-modal-close").addEventListener("click", close);
    backdrop.querySelector(".wh-modal-cancel").addEventListener("click", close);
    backdrop.addEventListener("click", function (event) {
      if (event.target === backdrop) close();
    });
    var select = backdrop.querySelector("#whProdNewEmployee");
    select.focus();
    backdrop.querySelector(".wh-modal-save").addEventListener("click", function () {
      var toUserId = parseInt(select.value, 10);
      var msg = backdrop.querySelector("#whProdReassignMsg");
      if (!toUserId || String(toUserId) === String(row.user_id)) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите другого сотрудника.";
        return;
      }
      var save = backdrop.querySelector(".wh-modal-save");
      save.disabled = true;
      msg.className = "wh-msg";
      msg.textContent = "Сохранение…";
      shell()
        .fetchJson("/api/warehouse/employees/productivity/employee", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            date: row.date,
            task_type: row.task_type,
            task_id: row.task_id,
            from_user_id: row.user_id,
            to_user_id: toUserId,
          }),
        })
        .then(function () {
          close();
          return refresh(root);
        })
        .catch(function (error) {
          save.disabled = false;
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = error.message || "Не удалось изменить сотрудника";
        });
    });
  }

  function openDateEditor(row, root) {
    var backdrop = document.createElement("div");
    backdrop.className = "wh-modal-backdrop";
    backdrop.innerHTML =
      '<div class="wh-modal" role="dialog" aria-modal="true">' +
      '<div class="wh-modal-header"><h3>Изменить дату</h3><button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body">' +
      '<p class="wh-prod-reassign-info"><strong>' +
      esc(row.task_type_name) +
      "</strong><br>" +
      esc(row.task_data) +
      "<br>" +
      esc(row.quantity) +
      " шт. · время операций сохранится</p>" +
      '<div class="wh-prod-reassign-field"><label for="whProdNewDate">Дата</label>' +
      '<input type="date" id="whProdNewDate" value="' +
      esc(row.date) +
      '" /></div>' +
      '<p class="wh-msg" id="whProdDateMsg"></p>' +
      "</div>" +
      '<div class="wh-modal-footer"><button type="button" class="wh-btn wh-btn-primary wh-modal-save">Сохранить</button>' +
      '<button type="button" class="wh-btn wh-modal-cancel">Отмена</button></div></div>';
    document.body.appendChild(backdrop);

    function close() {
      backdrop.remove();
    }
    backdrop.querySelector(".wh-modal-close").addEventListener("click", close);
    backdrop.querySelector(".wh-modal-cancel").addEventListener("click", close);
    backdrop.addEventListener("click", function (event) {
      if (event.target === backdrop) close();
    });
    var input = backdrop.querySelector("#whProdNewDate");
    input.focus();
    backdrop.querySelector(".wh-modal-save").addEventListener("click", function () {
      var toDate = String(input.value || "").trim();
      var msg = backdrop.querySelector("#whProdDateMsg");
      if (!toDate || toDate === String(row.date)) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Укажите другую дату.";
        return;
      }
      var save = backdrop.querySelector(".wh-modal-save");
      save.disabled = true;
      msg.className = "wh-msg";
      msg.textContent = "Сохранение…";
      shell()
        .fetchJson("/api/warehouse/employees/productivity/date", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            date: row.date,
            to_date: toDate,
            task_type: row.task_type,
            task_id: row.task_id,
            user_id: row.user_id,
          }),
        })
        .then(function () {
          close();
          return refresh(root);
        })
        .catch(function (error) {
          save.disabled = false;
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = error.message || "Не удалось изменить дату";
        });
    });
  }

  function todayIso() {
    var now = new Date();
    var month = String(now.getMonth() + 1);
    var day = String(now.getDate());
    if (month.length < 2) month = "0" + month;
    if (day.length < 2) day = "0" + day;
    return now.getFullYear() + "-" + month + "-" + day;
  }

  function openAddRow(root) {
    var backdrop = document.createElement("div");
    backdrop.className = "wh-modal-backdrop";
    backdrop.innerHTML =
      '<div class="wh-modal" role="dialog" aria-modal="true">' +
      '<div class="wh-modal-header"><h3>Добавить строку</h3><button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body">' +
      '<p class="wh-prod-reassign-info">Тип задания будет <strong>Доработка</strong>. Подробности вписываются как комментарий.</p>' +
      '<div class="wh-prod-reassign-field"><label for="whProdAddEmployee">Сотрудник</label><select id="whProdAddEmployee">' +
      employeeOptions(employees[0] && employees[0].id) +
      "</select></div>" +
      '<div class="wh-prod-reassign-field"><label for="whProdAddDate">Дата</label>' +
      '<input type="date" id="whProdAddDate" value="' +
      esc(todayIso()) +
      '" /></div>' +
      '<div class="wh-prod-reassign-field"><label for="whProdAddQty">Количество</label>' +
      '<input type="number" min="1" step="1" id="whProdAddQty" value="" /></div>' +
      '<div class="wh-prod-reassign-field"><label for="whProdAddComment">Подробности задания</label>' +
      '<textarea id="whProdAddComment" maxlength="2000" placeholder="Комментарий к доработке"></textarea></div>' +
      '<p class="wh-msg" id="whProdAddMsg"></p>' +
      "</div>" +
      '<div class="wh-modal-footer"><button type="button" class="wh-btn wh-btn-primary wh-modal-save">Добавить</button>' +
      '<button type="button" class="wh-btn wh-modal-cancel">Отмена</button></div></div>';
    document.body.appendChild(backdrop);

    function close() {
      backdrop.remove();
    }
    backdrop.querySelector(".wh-modal-close").addEventListener("click", close);
    backdrop.querySelector(".wh-modal-cancel").addEventListener("click", close);
    backdrop.addEventListener("click", function (event) {
      if (event.target === backdrop) close();
    });
    var qty = backdrop.querySelector("#whProdAddQty");
    qty.focus();
    backdrop.querySelector(".wh-modal-save").addEventListener("click", function () {
      var msg = backdrop.querySelector("#whProdAddMsg");
      var userId = parseInt(backdrop.querySelector("#whProdAddEmployee").value, 10);
      var eventDate = String(backdrop.querySelector("#whProdAddDate").value || "").trim();
      var quantity = parseInt(qty.value, 10);
      var comment = String(backdrop.querySelector("#whProdAddComment").value || "").trim();
      if (!userId) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите сотрудника.";
        return;
      }
      if (!eventDate) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Укажите дату.";
        return;
      }
      if (!quantity || quantity < 1) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Укажите количество больше нуля.";
        return;
      }
      var save = backdrop.querySelector(".wh-modal-save");
      save.disabled = true;
      msg.className = "wh-msg";
      msg.textContent = "Сохранение…";
      shell()
        .fetchJson("/api/warehouse/employees/productivity/rows", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            date: eventDate,
            user_id: userId,
            quantity: quantity,
            comment: comment,
          }),
        })
        .then(function () {
          close();
          return refresh(root);
        })
        .catch(function (error) {
          save.disabled = false;
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = error.message || "Не удалось добавить строку";
        });
    });
  }

  function payHtml(row) {
    if (row.counted === false) {
      return '<div class="wh-prod-pay wh-prod-pay--off">не учитывается</div>';
    }
    if (row.pay == null || row.pay === "") {
      return '<div class="wh-prod-pay wh-prod-pay--empty"></div>';
    }
    return '<div class="wh-prod-pay">' + esc(row.pay) + " ₽</div>";
  }

  function openSettings(root) {
    var backdrop = document.createElement("div");
    backdrop.className = "wh-modal-backdrop";
    backdrop.innerHTML =
      '<div class="wh-modal" role="dialog" aria-modal="true">' +
      '<div class="wh-modal-header"><h3>Настройки выработки</h3><button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body">' +
      '<p class="wh-prod-rate-hint">Оплата за одну штуку в рублях. Если поле пустое, сумма по заданию не показывается.</p>' +
      '<div class="wh-prod-rate-list" id="whProdRateList"><div class="wh-prod-detail-status">Загрузка…</div></div>' +
      '<p class="wh-msg" id="whProdSettingsMsg"></p>' +
      "</div>" +
      '<div class="wh-modal-footer"><button type="button" class="wh-btn wh-btn-primary wh-modal-save">Сохранить</button>' +
      '<button type="button" class="wh-btn wh-modal-cancel">Отмена</button></div></div>';
    document.body.appendChild(backdrop);

    function close() {
      backdrop.remove();
    }
    backdrop.querySelector(".wh-modal-close").addEventListener("click", close);
    backdrop.querySelector(".wh-modal-cancel").addEventListener("click", close);
    backdrop.addEventListener("click", function (event) {
      if (event.target === backdrop) close();
    });
    var list = backdrop.querySelector("#whProdRateList");
    var save = backdrop.querySelector(".wh-modal-save");
    save.disabled = true;
    shell()
      .fetchJson("/api/warehouse/employees/productivity/settings")
      .then(function (data) {
        var rates = (data && data.rates) || [];
        list.innerHTML = rates
          .map(function (item) {
            return (
              '<div class="wh-prod-rate-row"><label for="whProdRate-' +
              esc(item.id) +
              '">' +
              esc(item.name) +
              '</label><input type="number" min="0" step="0.01" id="whProdRate-' +
              esc(item.id) +
              '" data-rate-id="' +
              esc(item.id) +
              '" value="' +
              esc(item.rate || "") +
              '" placeholder="пусто" /></div>'
            );
          })
          .join("");
        save.disabled = false;
      })
      .catch(function (error) {
        list.innerHTML =
          '<div class="wh-prod-detail-status wh-msg-error">' +
          esc(error.message || "Не удалось загрузить настройки") +
          "</div>";
      });
    save.addEventListener("click", function () {
      var msg = backdrop.querySelector("#whProdSettingsMsg");
      var payload = [];
      backdrop.querySelectorAll("[data-rate-id]").forEach(function (input) {
        payload.push({
          id: input.getAttribute("data-rate-id"),
          rate: String(input.value || "").trim(),
        });
      });
      save.disabled = true;
      msg.className = "wh-msg";
      msg.textContent = "Сохранение…";
      shell()
        .fetchJson("/api/warehouse/employees/productivity/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rates: payload }),
        })
        .then(function () {
          close();
          return refresh(root);
        })
        .catch(function (error) {
          save.disabled = false;
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = error.message || "Не удалось сохранить настройки";
        });
    });
  }

  function setRowCounted(row, root, counted) {
    return shell()
      .fetchJson("/api/warehouse/employees/productivity/counted", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          date: row.date,
          task_type: row.task_type,
          task_id: row.task_id,
          user_id: row.user_id,
          counted: counted,
        }),
      })
      .then(function () {
        return refresh(root);
      });
  }

  function detailReference(row, detail) {
    var reference = String(detail.reference || "").trim();
    if (!reference) return "Строка #" + detail.line_id;
    if (row.task_type === "rework") {
      return String(detail.reference || detail.product_name || "Доработка").trim() || "Доработка";
    }
    if (row.task_type === "fbs" || row.task_type === "vseinstrumenti") {
      return "Заказ " + reference;
    }
    return "Грузоместо " + reference;
  }

  function detailProduct(detail) {
    var name = String(detail.product_name || "").trim();
    var sku = String(detail.sku || "").trim();
    if (name && sku) return esc(name) + '<br><span class="wh-muted">' + esc(sku) + "</span>";
    return esc(name || sku || "Товар не указан");
  }

  function renderDetailPanel(row, data) {
    var details = (data && data.details) || [];
    if (!details.length) {
      return '<div class="wh-prod-detail-status">Детализация для этой строки не найдена.</div>';
    }
    var body = details
      .map(function (detail) {
        return (
          "<tr><td class=\"wh-prod-detail-time\">" +
          esc(detail.completed_at) +
          '</td><td><span class="wh-prod-detail-ref">' +
          esc(detailReference(row, detail)) +
          '</span></td><td class="wh-prod-detail-product">' +
          detailProduct(detail) +
          '</td><td class="wh-prod-detail-qty">' +
          esc(detail.quantity) +
          " шт.</td></tr>"
        );
      })
      .join("");
    return (
      '<div class="wh-prod-detail-status">Операций: ' +
      esc(data.row_count || details.length) +
      " · всего: " +
      esc(data.quantity || 0) +
      " шт.</div>" +
      '<table class="wh-prod-detail-table"><thead><tr><th>Выполнено</th><th>Заказ / грузоместо</th><th>Товар</th><th>Количество</th></tr></thead><tbody>' +
      body +
      "</tbody></table>"
    );
  }

  function toggleDetails(button, row) {
    var mainRow = button.closest("tr");
    var detailRow = mainRow && mainRow.nextElementSibling;
    if (!detailRow || !detailRow.classList.contains("wh-prod-detail-row")) return;
    var expanded = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", expanded ? "false" : "true");
    detailRow.hidden = expanded;
    if (expanded || detailRow.getAttribute("data-loaded") === "true") return;

    var panel = detailRow.querySelector(".wh-prod-detail-panel");
    panel.innerHTML = '<div class="wh-prod-detail-status">Загрузка детализации…</div>';
    var params = [
      "date=" + encodeURIComponent(row.date),
      "task_type=" + encodeURIComponent(row.task_type),
      "task_id=" + encodeURIComponent(row.task_id),
      "user_id=" + encodeURIComponent(row.user_id),
    ].join("&");
    shell()
      .fetchJson("/api/warehouse/employees/productivity/details?" + params)
      .then(function (data) {
        detailRow.setAttribute("data-loaded", "true");
        panel.innerHTML = renderDetailPanel(row, data);
      })
      .catch(function (error) {
        panel.innerHTML =
          '<div class="wh-prod-detail-status wh-msg-error">' +
          esc(error.message || "Не удалось загрузить детализацию") +
          "</div>";
      });
  }

  function renderRows(root) {
    var pager = global.WH_PAGER;
    var sliced = pager ? pager.slice(rows, page) : { items: rows, state: null };
    page = sliced.state ? sliced.state.page : 1;
    var body = sliced.items
      .map(function (row, index) {
        var dateParts = String(row.date || "").split("-");
        var shownDate =
          dateParts.length === 3
            ? dateParts[2] + "." + dateParts[1] + "." + dateParts[0]
            : row.date;
        var excluded = row.counted === false;
        return (
          '<tr class="wh-prod-main-row' +
          (excluded ? " is-excluded" : "") +
          '"><td><span class="wh-prod-date">' +
          esc(shownDate) +
          '</span><button type="button" class="wh-prod-edit" data-edit="date" data-row-index="' +
          esc(index) +
          '">Изменить</button></td><td><span class="wh-prod-employee">' +
          esc(row.employee) +
          "</span>" +
          (row.packers && row.packers.length
            ? '<div class="wh-prod-packers">ещё: ' +
              esc(row.packers.map(function (item) { return item.display_name; }).join(", ")) +
              "</div>"
            : "") +
          (row.shared_from
            ? '<div class="wh-prod-packers">' + esc(row.shared_from ? "доля от " + row.shared_from : "") + "</div>"
            : "") +
          '<button type="button" class="wh-prod-edit" data-edit="employee" data-row-index="' +
          esc(index) +
          '">Изменить</button>' +
          (row.has_own_work === false
            ? ""
            : '<button type="button" class="wh-prod-edit" data-edit="packers" data-row-index="' +
              esc(index) +
              '">Упаковщики</button>') +
          '</td><td class="wh-productivity-qty">' +
          '<span class="wh-prod-qty-number">' +
          esc(row.quantity) +
          '</span><span class="wh-prod-qty-unit">шт.</span>' +
          '</td><td class="wh-prod-pay-cell">' +
          payHtml(row) +
          '</td><td class="wh-prod-task"><span class="wh-prod-task-badge wh-prod-task-badge--' +
          esc(row.task_type) +
          '">' +
          esc(row.task_type_name) +
          '</span><div class="wh-prod-task-data">' +
          esc(row.task_data) +
          '</div><button type="button" class="wh-prod-details-toggle" aria-expanded="false" data-row-index="' +
          esc(index) +
          '"><span class="wh-prod-details-caret">▶</span>Детализация</button>' +
          '<button type="button" class="wh-prod-edit" data-edit="counted" data-row-index="' +
          esc(index) +
          '">' +
          (excluded ? "Учитывать" : "Не учитывать") +
          "</button></td></tr>" +
          '<tr class="wh-prod-detail-row' +
          (excluded ? " is-excluded" : "") +
          '" data-row-index="' +
          esc(index) +
          '" hidden><td class="wh-prod-detail-cell" colspan="5"><div class="wh-prod-detail-panel"></div></td></tr>'
        );
      })
      .join("");
    root.innerHTML =
      '<div class="wh-prod-shell">' +
      filterHtml() +
      '<div class="wh-productivity-summary"><div class="wh-prod-stat"><span class="wh-prod-stat-label">Всего упаковано</span><strong class="wh-prod-stat-value">' +
      esc(totalQuantity) +
      ' шт.</strong></div><div class="wh-prod-stat"><span class="wh-prod-stat-label">К оплате</span><strong class="wh-prod-stat-value">' +
      (totalPay === "" ? "—" : esc(totalPay) + " ₽") +
      '</strong></div><div class="wh-prod-stat"><span class="wh-prod-stat-label">Строк в отчёте</span><strong class="wh-prod-stat-value">' +
      esc(rows.length) +
      "</strong></div></div>" +
      (body
        ? '<div class="wh-prod-table-wrap"><table class="wh-productivity-table"><thead><tr>' +
          "<th>Дата</th><th>Сотрудник</th><th>Выработка</th><th>Оплата</th><th>Задача</th>" +
          "</tr></thead><tbody>" +
          body +
          "</tbody></table></div>"
        : '<div class="wh-prod-empty">По выбранным фильтрам данных нет.</div>') +
      (pager && sliced.state ? pager.html(sliced.state) : "") +
      "</div>";

    root.querySelectorAll(".wh-prod-edit").forEach(function (button) {
      button.addEventListener("click", function () {
        var row = sliced.items[parseInt(button.getAttribute("data-row-index"), 10)];
        if (!row) return;
        if (button.getAttribute("data-edit") === "date") openDateEditor(row, root);
        else if (button.getAttribute("data-edit") === "employee") openEmployeeEditor(row, root);
        else if (button.getAttribute("data-edit") === "packers") openPackersEditor(row, root);
        else if (button.getAttribute("data-edit") === "counted") {
          button.disabled = true;
          setRowCounted(row, root, row.counted === false).catch(function (error) {
            button.disabled = false;
            window.alert(error.message || "Не удалось изменить учёт строки");
          });
        }
      });
    });
    root.querySelectorAll(".wh-prod-details-toggle").forEach(function (button) {
      button.addEventListener("click", function () {
        var row = sliced.items[parseInt(button.getAttribute("data-row-index"), 10)];
        if (row) toggleDetails(button, row);
      });
    });
    root.querySelector("#whProductivityApply").addEventListener("click", function () {
      filters = readFilters(root);
      page = 1;
      refresh(root);
    });
    root.querySelector("#whProductivityReset").addEventListener("click", function () {
      filters = currentMonthRange();
      page = 1;
      refresh(root);
    });
    root.querySelector("#whProductivitySettings").addEventListener("click", function () {
      openSettings(root);
    });
    root.querySelector("#whProductivityAdd").addEventListener("click", function () {
      openAddRow(root);
    });
    root.querySelector("#whProductivityQ").addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      filters = readFilters(root);
      page = 1;
      refresh(root);
    });
    if (pager) {
      pager.bind(root, function (delta) {
        page += delta;
        renderRows(root);
      });
    }
  }

  function refresh(root) {
    root.innerHTML = '<p class="wh-msg">Загрузка выработки…</p>';
    load()
      .then(function () {
        renderRows(root);
      })
      .catch(function (error) {
        root.innerHTML =
          '<p class="wh-msg wh-msg-error">' +
          esc(error.message || "Не удалось загрузить выработку") +
          "</p>";
      });
  }

  function render(tab, item) {
    ensureStyles();
    shell().contentTitleEl.textContent = item.title;
    shell().contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    shell().contentPlaceholderEl.hidden = true;
    var root = shell().contentPanelEl;
    root.hidden = false;
    var card = document.querySelector(".wh-content-card");
    if (card) card.classList.add("wh-content-card--wide");
    refresh(root);
  }

  global.WhProductivity = { render: render };
})(window);
