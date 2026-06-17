(function (global) {
  var meta = {
    task_types: [],
    custom_fields: [],
    assignees: [],
    counterparties: [],
    document_types: [],
    current_user_id: null,
    current_user_name: "",
  };
  var listFilters = {};
  var listSort = { by: "end_date", dir: "desc" };
  var filterPanelOpen = false;
  var editingId = null;
  var formAssigneeIds = [];
  var formDocuments = [];
  var summaryYear = null;
  var summaryMonth = null;
  var CONFIGURE_VALUE = "__configure__";

  var MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
  ];
  var WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function linkifyText(text) {
    var safe = esc(text);
    return safe.replace(
      /(https?:\/\/[^\s<&]+[^\s<&.,;:!?)\]}'"])/gi,
      function (url) {
        return (
          '<a class="wh-link" href="' +
          url +
          '" target="_blank" rel="noopener noreferrer">' +
          url +
          "</a>"
        );
      }
    );
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
      meta.counterparties = data.counterparties || [];
      meta.document_types = data.document_types || [];
      meta.current_user_id = data.current_user_id != null ? data.current_user_id : null;
      meta.current_user_name = data.current_user_name || "";
      return meta;
    });
  }

  function filtersQuery() {
    var parts = [];
    Object.keys(listFilters).forEach(function (k) {
      if (listFilters[k]) parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(listFilters[k]));
    });
    if (listSort.by) {
      parts.push("sort_by=" + encodeURIComponent(listSort.by));
      parts.push("sort_dir=" + encodeURIComponent(listSort.dir || "desc"));
    }
    return parts.length ? "?" + parts.join("&") : "";
  }

  function sortableHeader(key, label) {
    var active = listSort.by === key;
    var cls = "wh-th-sortable";
    if (active) cls += listSort.dir === "asc" ? " wh-sorted-asc" : " wh-sorted-desc";
    return '<th class="' + cls + '" data-sort="' + esc(key) + '">' + esc(label) + "</th>";
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
      filterField("created_by_user_id", "Автор", "select", meta.assignees) +
      filterField("counterparty_id", "Контрагент", "select", meta.counterparties) +
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

  function buildCounterpartyOptions(items, selectedId) {
    var html = '<option value="">— не выбран —</option>';
    (items || []).forEach(function (it) {
      var sel = String(selectedId || "") === String(it.id) ? " selected" : "";
      html += '<option value="' + esc(it.id) + '"' + sel + ">" + esc(it.name) + "</option>";
    });
    return html;
  }

  function resolveAuthorName(task, taskId) {
    if (task.created_by_name) return task.created_by_name;
    if (task.author_name) return task.author_name;
    if (!taskId) {
      if (meta.current_user_name) return meta.current_user_name;
      if (meta.current_user_id != null) {
        for (var i = 0; i < meta.assignees.length; i++) {
          if (meta.assignees[i].id === meta.current_user_id) {
            return meta.assignees[i].display_name;
          }
        }
      }
    }
    return "—";
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

  function pad2(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function dayKey(year, month, day) {
    return year + "-" + pad2(month) + "-" + pad2(day);
  }

  function formatCost(val) {
    if (val === null || val === undefined || val === 0) return "";
    var n = Number(val);
    if (isNaN(n) || n === 0) return "";
    var s = n.toFixed(3).replace(/\.?0+$/, "");
    return s.replace(".", ",");
  }

  function formatHours(val) {
    if (val === null || val === undefined) return "";
    var n = Number(val);
    if (isNaN(n)) return "";
    if (n === 0) return "0 ч";
    var s = n.toFixed(2).replace(/\.?0+$/, "");
    return s.replace(".", ",") + " ч";
  }

  function parseCoefInput(raw) {
    return String(raw || "").trim().replace(",", ".");
  }

  function saveSummaryDefaultCoefficient(root, value, onDone) {
    fetchJson("/api/warehouse/tasks/summary/default-coefficient", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ coefficient: value }),
    })
      .then(function () {
        if (onDone) onDone();
      })
      .catch(function (err) {
        var msg = root.querySelector("#whTkSummaryMsg");
        if (msg) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || "Ошибка сохранения коэффициента";
        }
      });
  }

  function saveSummaryDayCoefficient(root, date, value, tab, item) {
    var body =
      value === "" ? { date: date, coefficient: null } : { date: date, coefficient: value };
    return fetchJson("/api/warehouse/tasks/summary/day-coefficient", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function () {
        renderSummaryCalendar(tab, item);
      })
      .catch(function (err) {
        var msg = root.querySelector("#whTkSummaryMsg");
        if (msg) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || "Ошибка сохранения коэффициента";
        }
      });
  }

  function bindSummaryCoefficientInputs(root, tab, item) {
    var defaultInput = root.querySelector("#whTkDefaultCoef");
    if (defaultInput) {
      defaultInput.addEventListener("change", function () {
        var val = parseCoefInput(defaultInput.value);
        if (!val) return;
        saveSummaryDefaultCoefficient(root, val, function () {
          renderSummaryCalendar(tab, item);
        });
      });
    }
    root.querySelectorAll(".wh-task-cal-coef").forEach(function (input) {
      input.addEventListener("change", function () {
        var date = input.getAttribute("data-date");
        if (!date) return;
        var val = parseCoefInput(input.value);
        saveSummaryDayCoefficient(root, date, val, tab, item);
      });
    });
  }

  function buildCalendarCells(year, month) {
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

  function shiftSummaryMonth(delta) {
    if (!summaryYear || !summaryMonth) {
      var now = new Date();
      summaryYear = now.getFullYear();
      summaryMonth = now.getMonth() + 1;
    }
    summaryMonth += delta;
    while (summaryMonth < 1) {
      summaryMonth += 12;
      summaryYear -= 1;
    }
    while (summaryMonth > 12) {
      summaryMonth -= 12;
      summaryYear += 1;
    }
  }

  function renderSummaryCalendar(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    var now = new Date();
    if (!summaryYear) summaryYear = now.getFullYear();
    if (!summaryMonth) summaryMonth = now.getMonth() + 1;
    root.innerHTML = "<p class=\"wh-msg\">Загрузка календаря…</p>";

    fetchJson(
      "/api/warehouse/tasks/summary/calendar?year=" +
        encodeURIComponent(summaryYear) +
        "&month=" +
        encodeURIComponent(summaryMonth)
    )
      .then(function (data) {
        var days = data.days || {};
        var defaultCoef = data.default_coefficient != null ? data.default_coefficient : 1;
        var cells = buildCalendarCells(summaryYear, summaryMonth);
        var weekdayHead = WEEKDAY_NAMES.map(function (name) {
          return "<div class=\"wh-task-cal-weekday\">" + esc(name) + "</div>";
        }).join("");
        var cellHtml = cells
          .map(function (dayNum) {
            if (!dayNum) {
              return '<div class="wh-task-cal-cell wh-task-cal-cell--empty"></div>';
            }
            var key = dayKey(summaryYear, summaryMonth, dayNum);
            var info = days[key] || {};
            var staffCount = info.staff_count || 0;
            var hours = formatHours(info.hours);
            var hoursHtml = hours
              ? '<span class="wh-task-cal-hours" title="Сумма документов ÷ сотрудники ÷ коэффициент">' +
                esc(hours) +
                "</span>"
              : staffCount > 0 && (info.total_cost || 0) > 0
                ? '<span class="wh-task-cal-hours wh-muted">—</span>'
                : "";
            var staffHtml =
              staffCount > 0
                ? '<span class="wh-task-cal-staff" title="Сотрудников на смене">' +
                  esc(staffCount) +
                  " чел.</span>"
                : '<span class="wh-task-cal-staff wh-muted">0 чел.</span>';
            var coefValue =
              info.coefficient_override != null && info.coefficient_override !== undefined
                ? String(info.coefficient_override).replace(".", ",")
                : "";
            var coefPlaceholder = String(defaultCoef).replace(".", ",");
            var tasksHint =
              info.task_count > 0
                ? "Задач: " + (info.task_count || 0) + ". Сумма: " + formatCost(info.total_cost)
                : "Нет задач с датой начала";
            return (
              '<div class="wh-task-cal-cell wh-task-summary-cell" title="' + esc(tasksHint) + '">' +
              '<span class="wh-task-cal-day">' + esc(dayNum) + "</span>" +
              staffHtml +
              '<label class="wh-task-cal-coef-wrap">кф. <input type="text" class="wh-task-cal-coef" data-date="' +
              esc(key) +
              '" value="' +
              esc(coefValue) +
              '" placeholder="' +
              esc(coefPlaceholder) +
              '" inputmode="decimal" /></label>' +
              hoursHtml +
              "</div>"
            );
          })
          .join("");

        root.innerHTML =
          '<div class="wh-task-summary">' +
          '<p class="wh-muted wh-task-summary-hint">Часы = сумма стоимостей групп товаров из документов задач ÷ количество сотрудников на смену ÷ коэффициент. Учитывается только <strong>дата начала</strong> задачи.</p>' +
          '<div class="wh-task-summary-settings">' +
          '<label class="wh-task-summary-default-coef">Коэффициент по умолчанию ' +
          '<input type="text" id="whTkDefaultCoef" value="' +
          esc(String(defaultCoef).replace(".", ",")) +
          '" inputmode="decimal" /></label>' +
          "</div>" +
          '<div class="wh-task-cal-toolbar">' +
          '<button type="button" class="wh-btn" id="whTkCalPrev">&larr;</button>' +
          "<h3>" + esc(MONTH_NAMES[summaryMonth - 1]) + " " + esc(summaryYear) + "</h3>" +
          '<button type="button" class="wh-btn" id="whTkCalNext">&rarr;</button>' +
          '<button type="button" class="wh-btn" id="whTkCalToday">Сегодня</button>' +
          "</div>" +
          '<div class="wh-task-cal-grid wh-task-summary-grid">' + weekdayHead + cellHtml + "</div>" +
          '<p class="wh-muted">Задач с датой начала в месяце: ' + esc(data.total_tasks || 0) + "</p>" +
          '<p class="wh-msg" id="whTkSummaryMsg"></p>' +
          "</div>";

        bindSummaryCoefficientInputs(root, tab, item);
        root.querySelector("#whTkCalPrev").addEventListener("click", function () {
          shiftSummaryMonth(-1);
          renderSummaryCalendar(tab, item);
        });
        root.querySelector("#whTkCalNext").addEventListener("click", function () {
          shiftSummaryMonth(1);
          renderSummaryCalendar(tab, item);
        });
        root.querySelector("#whTkCalToday").addEventListener("click", function () {
          var t = new Date();
          summaryYear = t.getFullYear();
          summaryMonth = t.getMonth() + 1;
          renderSummaryCalendar(tab, item);
        });
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
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
          var commentHtml = field.comment
            ? '<div class="wh-task-custom-field-comment">' + linkifyText(field.comment) + "</div>"
            : "";
          return (
            '<div class="wh-task-custom-field">' +
            '<div class="wh-task-custom-field-meta">' +
            '<div class="wh-task-custom-field-name">' + esc(field.name) + "</div>" +
            commentHtml +
            "</div>" +
            '<input type="text" class="wh-task-custom-field-value" data-field-id="' + esc(field.id) + '" value="' +
            esc(values[field.id] || "") + '" placeholder="Значение" /></div>'
          );
        })
        .join("") +
      '<div class="wh-form-actions"><button type="button" class="wh-btn wh-btn-sm" id="whTkConfigureFields">Настроить поля</button></div></div>'
    );
  }

  function emptyTaskDraft() {
    return {
      task_type_id: "",
      comment: "",
      start_date: "",
      end_date: "",
      counterparty_id: "",
      assignee_ids: [],
      documents: [],
      custom_fields: [],
    };
  }

  function captureFormDraft(root) {
    if (!root || !root.querySelector("#whTkType")) return emptyTaskDraft();
    var cpEl = root.querySelector("#whTkCounterparty");
    return {
      task_type_id: root.querySelector("#whTkType").value,
      comment: root.querySelector("#whTkComment").value.trim(),
      start_date: root.querySelector("#whTkStart").value,
      end_date: root.querySelector("#whTkEnd").value,
      counterparty_id: cpEl ? cpEl.value : "",
      assignee_ids: collectAssigneesFromDom(root),
      documents: formDocuments.map(function (d) {
        return {
          entity_type: d.entity_type,
          entity_type_label: d.entity_type_label,
          entity_id: d.entity_id,
          label: d.label,
        };
      }),
      custom_fields: collectCustomFieldsFromDom(root),
    };
  }

  function applyTaskDraft(draft) {
    var base = emptyTaskDraft();
    if (!draft) return base;
    formAssigneeIds = (draft.assignee_ids || []).slice();
    formDocuments = (draft.documents || []).map(function (d) {
      return {
        entity_type: d.entity_type,
        entity_type_label: d.entity_type_label,
        entity_id: d.entity_id,
        label: d.label,
      };
    });
    return {
      task_type_id: draft.task_type_id != null ? draft.task_type_id : "",
      comment: draft.comment || "",
      start_date: draft.start_date || "",
      end_date: draft.end_date || "",
      counterparty_id: draft.counterparty_id != null ? draft.counterparty_id : "",
      assignee_ids: formAssigneeIds.slice(),
      documents: formDocuments.slice(),
      custom_fields: draft.custom_fields || [],
    };
  }

  function collectCustomFieldsFromDom(root) {
    var valuesById = {};
    if (root) {
      root.querySelectorAll(".wh-task-custom-field-value").forEach(function (inp) {
        var fieldId = parseInt(inp.getAttribute("data-field-id"), 10);
        if (!fieldId) return;
        valuesById[fieldId] = inp.value.trim();
      });
    }
    if (!meta.custom_fields || !meta.custom_fields.length) return [];
    return meta.custom_fields
      .map(function (field) {
        var fieldId = parseInt(field.id, 10);
        if (!fieldId) return null;
        return {
          field_id: fieldId,
          value: valuesById[fieldId] != null ? valuesById[fieldId] : "",
        };
      })
      .filter(function (item) {
        return item != null;
      });
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
        if (data.sort_by) listSort.by = data.sort_by;
        if (data.sort_dir) listSort.dir = data.sort_dir;
        var rows = (data.tasks || [])
          .map(function (t) {
            return (
              '<tr data-id="' + esc(t.id) + '">' +
              "<td>#" + esc(t.id) + "</td>" +
              "<td>" + esc(t.task_type_name || "—") + "</td>" +
              "<td>" + esc(t.author_name || t.created_by_name || "—") + "</td>" +
              "<td>" + esc(t.counterparty_name || "—") + "</td>" +
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
              sortableHeader("id", "№") +
              sortableHeader("task_type", "Тип") +
              sortableHeader("author", "Автор") +
              sortableHeader("counterparty", "Контрагент") +
              sortableHeader("assignees", "Ответственные") +
              sortableHeader("documents", "Документы") +
              sortableHeader("comment", "Комментарий") +
              sortableHeader("start_date", "Начало") +
              sortableHeader("end_date", "Окончание") +
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
      loadMeta()
        .then(function () {
          renderForm(null);
        })
        .catch(function (err) {
          panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
        });
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
    root.querySelectorAll(".wh-th-sortable").forEach(function (th) {
      th.addEventListener("click", function (e) {
        e.stopPropagation();
        var key = th.getAttribute("data-sort");
        if (!key) return;
        if (listSort.by === key) {
          listSort.dir = listSort.dir === "asc" ? "desc" : "asc";
        } else {
          listSort.by = key;
          listSort.dir = key === "id" || key === "documents" ? "desc" : "asc";
        }
        renderList();
      });
    });
    root.querySelectorAll("tbody tr[data-id]").forEach(function (tr) {
      tr.style.cursor = "pointer";
      tr.addEventListener("click", function () {
        editingId = parseInt(tr.getAttribute("data-id"), 10);
        renderForm(editingId);
      });
    });
  }

  function renderForm(taskId, draft) {
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    var load = taskId
      ? fetchJson("/api/warehouse/tasks/" + taskId).then(function (d) {
          if (!draft) return d.task;
          var merged = applyTaskDraft(draft);
          return Object.assign({}, d.task, merged);
        })
      : Promise.resolve(applyTaskDraft(draft));
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
        var authorName = resolveAuthorName(t, taskId);
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
          '<div><label>Автор</label><div class="wh-input-readonly" id="whTkAuthor">' + esc(authorName) + "</div></div>" +
          '<div><label>Контрагент</label><select id="whTkCounterparty">' +
          buildCounterpartyOptions(meta.counterparties, t.counterparty_id) + "</select></div>" +
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
          var formDraft = captureFormDraft(root);
          modalTaskTypesEditor(function () {
            renderForm(taskId, formDraft);
          });
        });
        var cfgFieldsBtn = root.querySelector("#whTkConfigureFields");
        if (cfgFieldsBtn) {
          cfgFieldsBtn.addEventListener("click", function () {
            var formDraft = captureFormDraft(root);
            modalCustomFieldsEditor(function () {
              renderForm(taskId, formDraft);
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
    var typeVal = root.querySelector("#whTkType").value;
    if (!typeVal || typeVal === CONFIGURE_VALUE) {
      msg.className = "wh-msg wh-msg-error";
      msg.textContent = "Выберите тип задачи";
      return;
    }
    var body = {
      task_type_id: typeVal,
      comment: root.querySelector("#whTkComment").value.trim(),
      start_date: root.querySelector("#whTkStart").value,
      end_date: root.querySelector("#whTkEnd").value,
      counterparty_id: root.querySelector("#whTkCounterparty").value || null,
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
        if (item.id === "tasks-summary") {
          renderSummaryCalendar(tab, item);
          return;
        }
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
