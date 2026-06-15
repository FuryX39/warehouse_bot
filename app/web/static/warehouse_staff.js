(function (global) {
  var CONFIGURE_VALUE = "__configure__";
  var permissionsSchema = [];
  var meta = { groups: [] };
  var rolesCache = [];
  var employeesCache = [];
  var listFilters = {};
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
    var rows = employeesCache
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
      '<div id="whStaffEmployeeEditor"></div>';

    root.querySelector("#whStaffToggleFilter").addEventListener("click", function () {
      filterPanelOpen = !filterPanelOpen;
      var fp = root.querySelector("#whStaffFilters");
      if (fp) fp.classList.toggle("hidden", !filterPanelOpen);
    });
    root.querySelector("#whStaffApplyFilter").addEventListener("click", function () {
      listFilters = readFilterPanel(root);
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

  global.WhStaff = {
    renderEmployees: renderEmployees,
    renderRoles: renderRoles,
  };
})(window);
