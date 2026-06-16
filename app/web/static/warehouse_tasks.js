(function (global) {
  var meta = { task_types: [], custom_fields: [], assignees: [], document_types: [], current_user_id: null };
  var listFilters = {};
  var filterPanelOpen = false;
  var editingId = null;
  var formAssigneeIds = [];
  var formDocuments = [];
  var CONFIGURE_VALUE = "__configure__";

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

  function formatDay(val) {
    if (!val) return "—";
    var parts = String(val).split("-");
    if (parts.length !== 3) return String(val);
    return parts[2] + "." + parts[1] + "." + parts[0];
  }

  function loadMeta() {
    return fetchJson("/api/warehouse/tasks/meta").then(function (data) {
      meta.task_types = data.task_types || [];
      meta.custom_fields = data.custom_fields || [];
      meta.assignees = data.assignees || [];
      meta.document_types = data.document_types || [];
      meta.current_user_id = data.current_user_id != null ? data.current_user_id : null;
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
      var val = el.value.trim();
      if (val) out[key] = val;
    });
    return out;
  }

  function filterField(key, label, type, items) {
    var val = esc(listFilters[key] || "");
    if (type === "select") {
      var opts = '<option value="">—</option>';
      (items || []).forEach(function (it) {
        var id = it.id != null ? it.id : it.user_id;
        var name = it.name != null ? it.name : it.display_name || it.title;
        var sel = String(listFilters[key] || "") === String(id) ? " selected" : "";
        opts += '<option value="' + esc(id) + '"' + sel + ">" + esc(name) + "</option>";
      });
      return '<div><label>' + esc(label) + '</label><select data-filter="' + key + '">' + opts + "</select></div>";
    }
    if (type === "date") {
      return (
        '<div><label>' + esc(label) + '</label><input type="date" data-filter="' + key + '" value="' + val + '" /></div>'
      );
    }
    return '<div><label>' + esc(label) + '</label><input type="text" data-filter="' + key + '" value="' + val + '" /></div>';
  }

  function renderFilterPanel() {
    return (
      '<div class="wh-crm-filters' + (filterPanelOpen ? "" : " hidden") + '" id="whTkFilters">' +
      '<div class="wh-crm-filter-grid">' +
      filterField("comment", "Комментарий", "text") +
      filterField("task_type_id", "Тип задачи", "select", meta.task_types) +
      filterField("assignee_id", "Ответственный", "select", meta.assignees) +
      filterField("start_date_from", "Дата начала с", "date") +
      filterField("start_date_to", "Дата начала по", "date") +
      filterField("end_date_from", "Дата окончания с", "date") +
      filterField("end_date_to", "Дата окончания по", "date") +
      filterField("entity_type", "Тип документа", "select", meta.document_types) +
      filterField("entity_id", "ID документа", "text") +
      "</div>" +
      '<div class="wh-form-actions">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whTkApplyFilter">Применить</button>' +
      '<button type="button" class="wh-btn" id="whTkResetFilter">Сбросить</button>' +
      "</div></div>"
    );
  }

  function buildSelectOptions(items, selectedId) {
    var html = '<option value="">— выберите тип —</option>';
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
      '<button type="button" class="wh-btn wh-modal-cancel">Отмена</button></div></div>';
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

  function modalCustomFieldsEditor(onDone) {
    var rows = (meta.custom_fields || []).map(function (item) {
      return (
        '<div class="wh-modal-row wh-crm-dict-row" data-id="' + esc(item.id) + '">' +
        '<input type="text" class="wh-crm-dict-name" value="' + esc(item.name) + '" placeholder="Название" />' +
        '<input type="text" class="wh-crm-dict-comment" value="' + esc(item.comment || "") + '" placeholder="Комментарий" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button></div>'
      );
    });
    openModal(
      "Дополнительные поля",
      '<div id="whTkFieldRows">' + rows.join("") + "</div>" +
        '<button type="button" class="wh-btn wh-btn-sm" id="whTkFieldAdd">+ Добавить</button>',
      function (backdrop, close) {
        var items = [];
        backdrop.querySelectorAll(".wh-crm-dict-row").forEach(function (row) {
          var name = row.querySelector(".wh-crm-dict-name").value.trim();
          if (!name) return;
          var item = {
            name: name,
            comment: row.querySelector(".wh-crm-dict-comment").value.trim(),
          };
          var id = row.getAttribute("data-id");
          if (id) item.id = parseInt(id, 10);
          items.push(item);
        });
        fetchJson("/api/warehouse/tasks/custom-fields", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: items }),
        })
          .then(function (data) {
            meta.custom_fields = data.custom_fields || [];
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
    backdrop.querySelector("#whTkFieldAdd").addEventListener("click", function () {
      var row = document.createElement("div");
      row.className = "wh-modal-row wh-crm-dict-row";
      row.innerHTML =
        '<input type="text" class="wh-crm-dict-name" placeholder="Название" />' +
        '<input type="text" class="wh-crm-dict-comment" placeholder="Комментарий" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>';
      backdrop.querySelector("#whTkFieldRows").appendChild(row);
    });
    backdrop.addEventListener("click", function (e) {
      if (e.target.classList.contains("wh-crm-dict-remove")) {
        e.target.closest(".wh-crm-dict-row").remove();
      }
    });
  }

  function renderCustomFieldsBlock(task) {
    var values = {};
    (task.custom_fields || []).forEach(function (f) {
      values[f.field_id] = f.value || "";
    });
    if (!meta.custom_fields.length) {
      return (
        '<p class="wh-msg">Дополнительные поля не настроены. ' +
        '<button type="button" class="wh-btn wh-btn-sm" id="whTkConfigureFields">Настроить поля</button></p>'
      );
    }
    return (
      '<div class="wh-task-custom-fields">' +
      meta.custom_fields
        .map(function (field) {
          var hint = field.comment
            ? '<span class="wh-muted wh-task-field-hint">' + esc(field.comment) + "</span>"
            : "";
          return (
            '<label class="wh-task-custom-field">' +
            "<span>" + esc(field.name) + hint + "</span>" +
            '<input type="text" class="wh-task-custom-field-value" data-field-id="' + esc(field.id) + '" value="' +
            esc(values[field.id] || "") + '" /></label>'
          );
        })
        .join("") +
      '<div class="wh-form-actions"><button type="button" class="wh-btn wh-btn-sm" id="whTkConfigureFields">Настроить поля</button></div></div>'
    );
  }

  function collectCustomFieldsFromDom(root) {
    var items = [];
    root.querySelectorAll(".wh-task-custom-field-value").forEach(function (inp) {
      var fieldId = parseInt(inp.getAttribute("data-field-id"), 10);
      if (!fieldId) return;
      items.push({ field_id: fieldId, value: inp.value.trim() });
    });
    return items;
  }

  function modalTaskTypesEditor(onDone) {
    var rows = (meta.task_types || []).map(function (item) {
      return (
        '<div class="wh-modal-row wh-crm-dict-row" data-id="' + esc(item.id) + '">' +
        '<input type="text" class="wh-crm-dict-name" value="' + esc(item.name) + '" placeholder="Название" />' +
        '<input type="text" class="wh-crm-dict-comment" value="' + esc(item.comment || "") + '" placeholder="Комментарий" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button></div>'
      );
    });
    openModal(
      "Типы задач",
      '<div id="whTkTypeRows">' + rows.join("") + "</div>" +
        '<button type="button" class="wh-btn wh-btn-sm" id="whTkTypeAdd">+ Добавить</button>',
      function (backdrop, close) {
        var items = [];
        backdrop.querySelectorAll(".wh-crm-dict-row").forEach(function (row) {
          var name = row.querySelector(".wh-crm-dict-name").value.trim();
          if (!name) return;
          var item = {
            name: name,
            comment: row.querySelector(".wh-crm-dict-comment").value.trim(),
          };
          var id = row.getAttribute("data-id");
          if (id) item.id = parseInt(id, 10);
          items.push(item);
        });
        fetchJson("/api/warehouse/tasks/types", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: items }),
        })
          .then(function (data) {
            meta.task_types = data.task_types || [];
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
    backdrop.querySelector("#whTkTypeAdd").addEventListener("click", function () {
      var row = document.createElement("div");
      row.className = "wh-modal-row wh-crm-dict-row";
      row.innerHTML =
        '<input type="text" class="wh-crm-dict-name" placeholder="Название" />' +
        '<input type="text" class="wh-crm-dict-comment" placeholder="Комментарий" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>';
      backdrop.querySelector("#whTkTypeRows").appendChild(row);
    });
    backdrop.addEventListener("click", function (e) {
      if (e.target.classList.contains("wh-crm-dict-remove")) {
        e.target.closest(".wh-crm-dict-row").remove();
      }
    });
  }

  function renderAssigneesBlock() {
    if (!meta.assignees.length) {
      return '<p class="wh-msg">Нет активных сотрудников.</p>';
    }
    return (
      '<div class="wh-task-assignees">' +
      meta.assignees
        .map(function (a) {
          var checked = formAssigneeIds.indexOf(a.id) >= 0 ? " checked" : "";
          return (
            '<label class="wh-task-assignee">' +
            '<input type="checkbox" class="wh-task-assignee-cb" value="' + esc(a.id) + '"' + checked + " /> " +
            esc(a.display_name) +
            "</label>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  function renderDocumentsBlock() {
    if (!formDocuments.length) {
      return '<p class="wh-msg" id="whTkDocsEmpty">Документы не привязаны.</p>';
    }
    return formDocuments
      .map(function (doc, index) {
        return (
          '<div class="wh-task-doc-row" data-index="' + index + '">' +
          '<span class="wh-badge">' + esc(doc.entity_type_label || doc.entity_type) + "</span> " +
          "<span>" + esc(doc.label) + "</span>" +
          '<button type="button" class="wh-btn wh-btn-sm wh-task-doc-remove" title="Отвязать">&times;</button></div>'
        );
      })
      .join("");
  }

  function bindAssigneeEvents(root) {
    root.querySelectorAll(".wh-task-assignee-cb").forEach(function (cb) {
      cb.addEventListener("change", function () {
        var id = parseInt(cb.value, 10);
        if (!id) return;
        var idx = formAssigneeIds.indexOf(id);
        if (cb.checked && idx < 0) formAssigneeIds.push(id);
        if (!cb.checked && idx >= 0) formAssigneeIds.splice(idx, 1);
      });
    });
  }

  function bindDocumentEvents(root) {
    root.querySelectorAll(".wh-task-doc-remove").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var idx = parseInt(btn.closest(".wh-task-doc-row").getAttribute("data-index"), 10);
        formDocuments.splice(idx, 1);
        refreshDocumentsDom(root);
      });
    });
  }

  function refreshDocumentsDom(root) {
    var wrap = root.querySelector("#whTkDocuments");
    if (!wrap) return;
    wrap.innerHTML = renderDocumentsBlock();
    bindDocumentEvents(root);
  }

  function addDocumentToForm(root, doc) {
    for (var i = 0; i < formDocuments.length; i++) {
      if (
        formDocuments[i].entity_type === doc.entity_type &&
        formDocuments[i].entity_id === doc.entity_id
      ) {
        alert("Этот документ уже привязан");
        return;
      }
    }
    formDocuments.push(doc);
    refreshDocumentsDom(root);
  }

  function openDocumentSearch(root) {
    var entityType = root.querySelector("#whTkDocSearchType").value;
    var q = root.querySelector("#whTkDocSearchQ").value.trim();
    var url =
      "/api/warehouse/tasks/documents/search?limit=40" +
      (entityType ? "&entity_type=" + encodeURIComponent(entityType) : "") +
      (q ? "&q=" + encodeURIComponent(q) : "");
    fetchJson(url)
      .then(function (data) {
        var list = data.documents || [];
        var wrap = root.querySelector("#whTkDocSearchResults");
        if (!list.length) {
          wrap.innerHTML = '<p class="wh-msg">Документы не найдены.</p>';
          return;
        }
        wrap.innerHTML = list
          .map(function (doc) {
            return (
              '<button type="button" class="wh-rc-search-item wh-task-doc-pick">' +
              '<span class="wh-badge">' + esc(doc.entity_type_label) + "</span> " +
              '<span class="wh-rc-search-text">' + esc(doc.label) + "</span></button>"
            );
          })
          .join("");
        wrap.querySelectorAll(".wh-task-doc-pick").forEach(function (btn, i) {
          btn.addEventListener("click", function () {
            addDocumentToForm(root, list[i]);
          });
        });
      })
      .catch(function (err) {
        root.querySelector("#whTkDocSearchResults").innerHTML =
          '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function collectAssigneesFromDom(root) {
    var ids = [];
    root.querySelectorAll(".wh-task-assignee-cb:checked").forEach(function (cb) {
      var id = parseInt(cb.value, 10);
      if (id) ids.push(id);
    });
    return ids;
  }

  function renderList() {
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    fetchJson("/api/warehouse/tasks" + filtersQuery())
      .then(function (data) {
        var rows = (data.tasks || [])
          .map(function (t) {
            return (
              '<tr data-id="' + esc(t.id) + '">' +
              "<td>#" + esc(t.id) + "</td>" +
              "<td>" + esc(t.task_type_name || "—") + "</td>" +
              "<td>" + esc(t.assignees_short || "—") + "</td>" +
              "<td>" + esc(t.documents_short || "—") + "</td>" +
              "<td>" + esc(t.comment_short || "—") + "</td>" +
              "<td>" + esc(formatDay(t.start_date)) + "</td>" +
              "<td>" + esc(formatDay(t.end_date)) + "</td></tr>"
            );
          })
          .join("");
        root.innerHTML =
          '<div class="wh-crm-toolbar">' +
          '<input type="search" id="whTkQuickSearch" class="wh-crm-search" placeholder="Быстрый поиск…" value="' + esc(listFilters.q || "") + '" />' +
          '<button type="button" class="wh-btn" id="whTkToggleFilter">Фильтр</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whTkCreate">+ Задача</button>' +
          "</div>" +
          renderFilterPanel() +
          '<div id="whTkListWrap">' +
          (rows
            ? '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
              "<th>№</th><th>Тип</th><th>Ответственные</th><th>Документы</th><th>Комментарий</th><th>Начало</th><th>Окончание</th>" +
              "</tr></thead><tbody>" + rows + "</tbody></table>"
            : '<p class="wh-msg">Задачи не найдены.</p>') +
          "</div>";
        bindListEvents(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function bindListEvents(root) {
    root.querySelector("#whTkCreate").addEventListener("click", function () {
      editingId = null;
      formAssigneeIds = [];
      formDocuments = [];
      renderForm(null);
    });
    root.querySelector("#whTkToggleFilter").addEventListener("click", function () {
      filterPanelOpen = !filterPanelOpen;
      var fp = root.querySelector("#whTkFilters");
      if (fp) fp.classList.toggle("hidden", !filterPanelOpen);
    });
    root.querySelector("#whTkApplyFilter").addEventListener("click", function () {
      listFilters = readFilterPanel(root);
      renderList();
    });
    root.querySelector("#whTkResetFilter").addEventListener("click", function () {
      listFilters = {};
      filterPanelOpen = false;
      renderList();
    });
    root.querySelector("#whTkQuickSearch").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        listFilters.q = e.target.value.trim();
        renderList();
      }
    });
    root.querySelectorAll("tbody tr[data-id]").forEach(function (tr) {
      tr.style.cursor = "pointer";
      tr.addEventListener("click", function () {
        editingId = parseInt(tr.getAttribute("data-id"), 10);
        renderForm(editingId);
      });
    });
  }

  function renderForm(taskId) {
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    var load = taskId
      ? fetchJson("/api/warehouse/tasks/" + taskId).then(function (d) {
          return d.task;
        })
      : Promise.resolve({
          task_type_id: "",
          comment: "",
          start_date: "",
          end_date: "",
          assignee_ids: [],
          documents: [],
        });
    load
      .then(function (t) {
        formAssigneeIds = (t.assignee_ids || []).slice();
        formDocuments = (t.documents || []).map(function (d) {
          return {
            entity_type: d.entity_type,
            entity_type_label: d.entity_type_label,
            entity_id: d.entity_id,
            label: d.label,
          };
        });
        var docTypeOpts = '<option value="">Все типы</option>';
        meta.document_types.forEach(function (dt) {
          docTypeOpts += '<option value="' + esc(dt.id) + '">' + esc(dt.title) + "</option>";
        });
        root.innerHTML =
          '<div class="wh-crm-form-toolbar">' +
          '<button type="button" class="wh-btn" id="whTkBack">&larr; К списку</button>' +
          (taskId ? '<button type="button" class="wh-btn wh-btn-danger" id="whTkDelete">Удалить</button>' : "") +
          '<button type="button" class="wh-btn wh-btn-primary" id="whTkSave">Сохранить</button></div>' +
          '<p class="wh-msg" id="whTkFormMsg"></p>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Задача</h4>' +
          '<div class="wh-form-row">' +
          '<div><label>Тип задачи</label><select id="whTkType" data-prev="' + esc(t.task_type_id || "") + '">' +
          buildSelectOptions(meta.task_types, t.task_type_id) + "</select></div>" +
          '<div><label>Дата начала</label><input type="date" id="whTkStart" value="' + esc(t.start_date || "") + '" /></div>' +
          '<div><label>Дата окончания</label><input type="date" id="whTkEnd" value="' + esc(t.end_date || "") + '" /></div>' +
          "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Ответственные</h4>' +
          renderAssigneesBlock() + "</section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Привязка документов</h4>' +
          '<div class="wh-rc-search-grid">' +
          '<div><label>Тип документа</label><select id="whTkDocSearchType">' + docTypeOpts + "</select></div>" +
          '<div><label>Поиск</label><input type="text" id="whTkDocSearchQ" placeholder="Название, комментарий…" /></div>' +
          '<div class="wh-rc-search-btn-wrap"><button type="button" class="wh-btn" id="whTkDocSearchBtn">Найти</button></div></div>' +
          '<div id="whTkDocSearchResults" class="wh-rc-search-results"></div>' +
          '<div id="whTkDocuments" class="wh-task-docs">' + renderDocumentsBlock() + "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Дополнительные поля</h4>' +
          '<div id="whTkCustomFields">' + renderCustomFieldsBlock(t) + "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Комментарий</h4>' +
          '<textarea id="whTkComment" class="wh-rc-comment" rows="4" placeholder="Необязательно">' + esc(t.comment) + "</textarea></section>";

        bindAssigneeEvents(root);
        bindDocumentEvents(root);
        bindConfigureSelect(root.querySelector("#whTkType"), function () {
          modalTaskTypesEditor(function () {
            renderForm(taskId);
          });
        });
        var cfgFieldsBtn = root.querySelector("#whTkConfigureFields");
        if (cfgFieldsBtn) {
          cfgFieldsBtn.addEventListener("click", function () {
            modalCustomFieldsEditor(function () {
              renderForm(taskId);
            });
          });
        }
        root.querySelector("#whTkBack").addEventListener("click", function () {
          editingId = null;
          formAssigneeIds = [];
          formDocuments = [];
          renderList();
        });
        root.querySelector("#whTkDocSearchBtn").addEventListener("click", function () {
          openDocumentSearch(root);
        });
        root.querySelector("#whTkDocSearchQ").addEventListener("keydown", function (e) {
          if (e.key === "Enter") openDocumentSearch(root);
        });
        root.querySelector("#whTkSave").addEventListener("click", function () {
          saveTask(root, taskId);
        });
        if (taskId) {
          root.querySelector("#whTkDelete").addEventListener("click", function () {
            if (!confirm("Удалить задачу?")) return;
            fetchJson("/api/warehouse/tasks/" + taskId, { method: "DELETE" })
              .then(function () {
                editingId = null;
                formAssigneeIds = [];
                formDocuments = [];
                renderList();
              })
              .catch(function (err) {
                var msg = root.querySelector("#whTkFormMsg");
                msg.className = "wh-msg wh-msg-error";
                msg.textContent = err.message;
              });
          });
        }
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function saveTask(root, taskId) {
    var msg = root.querySelector("#whTkFormMsg");
    msg.textContent = "";
    msg.className = "wh-msg";
    var body = {
      task_type_id: root.querySelector("#whTkType").value,
      comment: root.querySelector("#whTkComment").value.trim(),
      start_date: root.querySelector("#whTkStart").value,
      end_date: root.querySelector("#whTkEnd").value,
      assignee_ids: collectAssigneesFromDom(root),
      documents: formDocuments.map(function (d) {
        return { entity_type: d.entity_type, entity_id: d.entity_id };
      }),
      custom_fields: collectCustomFieldsFromDom(root),
    };
    var req = taskId
      ? fetchJson("/api/warehouse/tasks/" + taskId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      : fetchJson("/api/warehouse/tasks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
    req
      .then(function () {
        msg.className = "wh-msg wh-msg-ok";
        msg.textContent = "Сохранено.";
        if (!taskId) {
          editingId = null;
          formAssigneeIds = [];
          formDocuments = [];
          renderList();
        }
      })
      .catch(function (err) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Ошибка сохранения";
      });
  }

  function applyNavPreset(itemId) {
    listFilters = {};
    filterPanelOpen = false;
    if (itemId === "assigned-to-me" && meta.current_user_id != null) {
      listFilters.assignee_id = String(meta.current_user_id);
    } else if (itemId === "created-by-me" && meta.current_user_id != null) {
      listFilters.created_by_user_id = String(meta.current_user_id);
    }
  }

  function renderTasks(tab, item) {
    preparePanel(tab, item);
    editingId = null;
    formAssigneeIds = [];
    formDocuments = [];
    applyNavPreset(item.id);
    loadMeta()
      .then(function () {
        if (item.id === "task-create") {
          renderForm(null);
          return;
        }
        renderList();
      })
      .catch(function (err) {
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  global.WhTasks = {
    renderTasks: renderTasks,
  };
})(window);
