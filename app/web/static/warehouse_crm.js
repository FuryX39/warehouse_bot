(function (global) {
  var CONFIGURE_VALUE = "__configure__";
  var meta = { statuses: [], groups: [], types: [], price_types: [] };
  var listFilters = {};
  var filterPanelOpen = false;
  var viewMode = "list";
  var editingId = null;

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

  function loadMeta() {
    return fetchJson("/api/warehouse/crm/meta").then(function (data) {
      meta.statuses = data.statuses || [];
      meta.groups = data.groups || [];
      meta.types = data.types || [];
      meta.price_types = data.price_types || [];
      return meta;
    });
  }

  function statusBadge(name, color) {
    if (!name) return "—";
    return (
      '<span class="wh-crm-status" style="background:' +
      esc(color || "#9e9e9e") +
      '22;color:' +
      esc(color || "#333") +
      ';border-color:' +
      esc(color || "#9e9e9e") +
      '">' +
      esc(name) +
      "</span>"
    );
  }

  function buildSelectOptions(items, selectedId, labelKey) {
    var html = '<option value="">—</option>';
    items.forEach(function (it) {
      var sel = String(selectedId || "") === String(it.id) ? " selected" : "";
      html +=
        '<option value="' + esc(it.id) + '"' + sel + ">" + esc(it[labelKey || "name"]) + "</option>";
    });
    html += '<option value="' + CONFIGURE_VALUE + '">Настроить…</option>';
    return html;
  }

  function bindConfigureSelect(selectEl, onConfigure) {
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
      '<div class="wh-modal" role="dialog">' +
      '<div class="wh-modal-header"><h3>' +
      esc(title) +
      '</h3><button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body">' +
      bodyHtml +
      "</div>" +
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

  function modalStatusesEditor() {
    var rows = meta.statuses.map(function (s) {
      return (
        '<div class="wh-modal-row wh-crm-dict-row" data-id="' +
        esc(s.id) +
        '">' +
        '<input type="text" class="wh-crm-dict-name" value="' +
        esc(s.name) +
        '" placeholder="Название" />' +
        '<input type="color" class="wh-crm-dict-color" value="' +
        esc(s.color || "#9e9e9e") +
        '" title="Цвет" />' +
        (s.is_default
          ? '<span class="wh-crm-dict-tag">по умолчанию</span>'
          : '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>') +
        "</div>"
      );
    });
    openModal(
      "Статусы контрагентов",
      '<p class="wh-msg">Название и цвет статуса.</p>' +
        '<div id="whCrmStatusRows">' +
        rows.join("") +
        "</div>" +
        '<button type="button" class="wh-btn wh-btn-sm" id="whCrmAddStatus">+ Добавить статус</button>',
      function (backdrop, close) {
        var items = [];
        backdrop.querySelectorAll(".wh-crm-dict-row").forEach(function (row) {
          var name = row.querySelector(".wh-crm-dict-name").value.trim();
          if (!name) return;
          var item = {
            name: name,
            color: row.querySelector(".wh-crm-dict-color").value,
          };
          var id = row.getAttribute("data-id");
          if (id) item.id = parseInt(id, 10);
          items.push(item);
        });
        fetchJson("/api/warehouse/crm/statuses", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: items }),
        })
          .then(function (data) {
            meta.statuses = data.statuses || [];
            close();
            if (viewMode === "form") renderForm(editingId);
            else renderList();
          })
          .catch(function (err) {
            alert(err.message || "Ошибка сохранения");
          });
      }
    );
    var backdrop = document.querySelector(".wh-modal-backdrop:last-of-type");
    if (!backdrop) return;
    backdrop.querySelector("#whCrmAddStatus").addEventListener("click", function () {
      var row = document.createElement("div");
      row.className = "wh-modal-row wh-crm-dict-row";
      row.innerHTML =
        '<input type="text" class="wh-crm-dict-name" placeholder="Название" />' +
        '<input type="color" class="wh-crm-dict-color" value="#9e9e9e" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>';
      backdrop.querySelector("#whCrmStatusRows").appendChild(row);
    });
    backdrop.addEventListener("click", function (e) {
      if (e.target.classList.contains("wh-crm-dict-remove")) {
        e.target.closest(".wh-crm-dict-row").remove();
      }
    });
  }

  function modalSimpleDict(title, apiPath, key, onDone) {
    var rows = (meta[key] || []).map(function (s) {
      return (
        '<div class="wh-modal-row wh-crm-dict-row" data-id="' +
        esc(s.id) +
        '">' +
        '<input type="text" class="wh-crm-dict-name" value="' +
        esc(s.name) +
        '" placeholder="Название" />' +
        (s.is_default
          ? '<span class="wh-crm-dict-tag">по умолчанию</span>'
          : '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>') +
        "</div>"
      );
    });
    var addId = "whCrmAdd" + key;
    var rowsId = "whCrmRows" + key;
    openModal(
      title,
      '<div id="' +
        rowsId +
        '">' +
        rows.join("") +
        "</div>" +
        '<button type="button" class="wh-btn wh-btn-sm" id="' +
        addId +
        '">+ Добавить</button>',
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
        fetchJson(apiPath, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: items }),
        })
          .then(function (data) {
            meta[key] = data[key] || [];
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
    backdrop.querySelector("#" + addId).addEventListener("click", function () {
      var row = document.createElement("div");
      row.className = "wh-modal-row wh-crm-dict-row";
      row.innerHTML =
        '<input type="text" class="wh-crm-dict-name" placeholder="Название" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>';
      backdrop.querySelector("#" + rowsId).appendChild(row);
    });
    backdrop.addEventListener("click", function (e) {
      if (e.target.classList.contains("wh-crm-dict-remove")) {
        e.target.closest(".wh-crm-dict-row").remove();
      }
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
    var q = root.querySelector("#whCrmQuickSearch");
    if (q && q.value.trim()) f.q = q.value.trim();
    root.querySelectorAll("[data-filter]").forEach(function (el) {
      var key = el.getAttribute("data-filter");
      var val = el.value.trim();
      if (val) f[key] = val;
    });
    return f;
  }

  function renderFilterPanel() {
    return (
      '<div class="wh-crm-filters' +
      (filterPanelOpen ? "" : " hidden") +
      '" id="whCrmFilters">' +
      '<h4 class="wh-crm-section-title">Фильтр по полям</h4>' +
      '<div class="wh-crm-filter-grid">' +
      filterField("status_id", "Статус", "select", meta.statuses) +
      filterField("group_id", "Группа", "select", meta.groups) +
      filterField("type_id", "Тип контрагента", "select", meta.types) +
      filterField("price_type_id", "Цена", "select", meta.price_types) +
      filterField("phone", "Телефон", "text") +
      filterField("email", "Электронный адрес", "text") +
      filterField("inn", "ИНН", "text") +
      filterField("full_name", "Полное наименование", "text") +
      filterField("legal_address", "Юридический адрес", "text") +
      filterField("address_comment", "Комментарий к адресу", "text") +
      filterField("fias_code", "Код ФИАС", "text") +
      filterField("kpp", "КПП", "text") +
      filterField("ogrn", "ОГРН", "text") +
      filterField("okpo", "ОКПО", "text") +
      filterField("discount_card_number", "Номер дисконтной карты", "text") +
      filterField("contact", "Контактное лицо (ФИО, телефон…)", "text") +
      "</div>" +
      '<div class="wh-form-actions">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whCrmApplyFilter">Применить</button>' +
      '<button type="button" class="wh-btn" id="whCrmResetFilter">Сбросить</button>' +
      "</div></div>"
    );
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
        '<div><label>' +
        esc(label) +
        '</label><select data-filter="' +
        key +
        '">' +
        opts +
        "</select></div>"
      );
    }
    return (
      '<div><label>' +
      esc(label) +
      '</label><input type="text" data-filter="' +
      key +
      '" value="' +
      val +
      '" /></div>'
    );
  }

  function renderListTable(items) {
    if (!items.length) {
      return '<p class="wh-msg">Контрагенты не найдены.</p>';
    }
    var rows = items
      .map(function (cp) {
        var title = cp.full_name || cp.inn || "Без названия";
        return (
          "<tr data-id=\"" +
          cp.id +
          '">' +
          "<td>" +
          esc(title) +
          "</td>" +
          "<td>" +
          statusBadge(cp.status_name, cp.status_color) +
          "</td>" +
          "<td>" +
          esc(cp.group_name || "—") +
          "</td>" +
          "<td>" +
          esc(cp.phone || "—") +
          "</td>" +
          "<td>" +
          esc(cp.email || "—") +
          "</td>" +
          "<td>" +
          esc(cp.type_name || "—") +
          "</td></tr>"
        );
      })
      .join("");
    return (
      '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
      "<th>Наименование</th><th>Статус</th><th>Группа</th><th>Телефон</th><th>Email</th><th>Тип</th>" +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table>"
    );
  }

  function renderList() {
    var root = panelEl();
    root.innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    fetchJson("/api/warehouse/crm/counterparties" + filtersQuery())
      .then(function (data) {
        root.innerHTML =
          '<div class="wh-crm-toolbar">' +
          '<input type="search" id="whCrmQuickSearch" class="wh-crm-search" placeholder="Быстрый поиск…" value="' +
          esc(listFilters.q || "") +
          '" />' +
          '<button type="button" class="wh-btn" id="whCrmToggleFilter">Фильтр</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whCrmCreate">+ Создать контрагента</button>' +
          "</div>" +
          renderFilterPanel() +
          '<div id="whCrmListWrap"></div>';
        root.querySelector("#whCrmListWrap").innerHTML = renderListTable(data.counterparties || []);
        bindListEvents(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function bindListEvents(root) {
    root.querySelector("#whCrmCreate").addEventListener("click", function () {
      viewMode = "form";
      editingId = null;
      renderForm(null);
    });
    root.querySelector("#whCrmToggleFilter").addEventListener("click", function () {
      filterPanelOpen = !filterPanelOpen;
      var fp = root.querySelector("#whCrmFilters");
      if (fp) fp.classList.toggle("hidden", !filterPanelOpen);
    });
    root.querySelector("#whCrmApplyFilter").addEventListener("click", function () {
      listFilters = readFilterPanel(root);
      renderList();
    });
    root.querySelector("#whCrmResetFilter").addEventListener("click", function () {
      listFilters = {};
      filterPanelOpen = false;
      renderList();
    });
    var qs = root.querySelector("#whCrmQuickSearch");
    qs.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        listFilters = readFilterPanel(root);
        renderList();
      }
    });
    root.querySelectorAll("tbody tr[data-id]").forEach(function (tr) {
      tr.style.cursor = "pointer";
      tr.addEventListener("click", function () {
        viewMode = "form";
        editingId = parseInt(tr.getAttribute("data-id"), 10);
        renderForm(editingId);
      });
    });
  }

  function contactPersonBlock(cp, index) {
    cp = cp || {};
    return (
      '<div class="wh-crm-contact-card" data-contact-index="' +
      index +
      '">' +
      '<div class="wh-crm-contact-head">' +
      "<strong>Контактное лицо</strong>" +
      '<button type="button" class="wh-btn wh-btn-sm wh-crm-remove-contact" title="Удалить">&times;</button>' +
      "</div>" +
      '<div class="wh-form-row">' +
      fieldInput("full_name", "ФИО", cp.full_name) +
      fieldInput("position", "Должность", cp.position) +
      fieldInput("phone", "Телефон", cp.phone) +
      fieldInput("email", "Электронный адрес", cp.email) +
      "</div>" +
      '<div class="wh-form-row">' +
      fieldInput("comment", "Комментарий", cp.comment, true) +
      "</div></div>"
    );
  }

  function fieldInput(name, label, value, wide) {
    return (
      '<div' +
      (wide ? ' class="wh-form-wide"' : "") +
      '><label>' +
      esc(label) +
      '</label><input type="text" data-cp-field="' +
      name +
      '" value="' +
      esc(value || "") +
      '" /></div>'
    );
  }

  function formField(label, inner) {
    return "<div><label>" + esc(label) + "</label>" + inner + "</div>";
  }

  function renderForm(counterpartyId) {
    var root = panelEl();
    root.innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    var load = counterpartyId
      ? fetchJson("/api/warehouse/crm/counterparties/" + counterpartyId).then(function (d) {
          return d.counterparty;
        })
      : Promise.resolve(emptyCounterparty());
    load
      .then(function (cp) {
        var contacts = cp.contact_persons || [];
        var contactsHtml = contacts.length
          ? contacts.map(function (c, i) {
              return contactPersonBlock(c, i);
            }).join("")
          : "";
        root.innerHTML =
          '<div class="wh-crm-form-toolbar">' +
          '<button type="button" class="wh-btn" id="whCrmBackList">&larr; К списку</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whCrmSaveCp">Сохранить</button>' +
          "</div>" +
          '<p class="wh-msg" id="whCrmFormMsg"></p>' +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">1. О контрагенте</h4>' +
          '<div class="wh-form-row">' +
          formField(
            "Статус",
            '<select id="whCpStatus" data-prev="' +
              esc(cp.status_id || "") +
              '">' +
              buildSelectOptions(meta.statuses, cp.status_id) +
              "</select>"
          ) +
          formField(
            "Группы",
            '<select id="whCpGroup" data-prev="' +
              esc(cp.group_id || "") +
              '">' +
              buildSelectOptions(meta.groups, cp.group_id) +
              "</select>"
          ) +
          formField("Телефон", '<input type="text" id="whCpPhone" value="' + esc(cp.phone) + '" />') +
          formField(
            "Электронный адрес",
            '<input type="text" id="whCpEmail" value="' + esc(cp.email) + '" />'
          ) +
          "</div></section>" +
          '<section class="wh-crm-section"><div class="wh-crm-section-head">' +
          '<h4 class="wh-crm-section-title">2. Контактные лица</h4>' +
          '<button type="button" class="wh-btn wh-btn-sm wh-crm-icon-btn" id="whCpAddContact" title="Добавить">+</button>' +
          "</div>" +
          '<div id="whCpContacts">' +
          contactsHtml +
          "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">3. Реквизиты</h4>' +
          '<div class="wh-form-row">' +
          formField(
            "Тип контрагента",
            '<select id="whCpType" data-prev="' +
              esc(cp.type_id || "") +
              '">' +
              buildSelectOptions(meta.types, cp.type_id) +
              "</select>"
          ) +
          formField("ИНН", '<input type="text" id="whCpInn" value="' + esc(cp.inn) + '" />') +
          formField(
            "Полное наименование",
            '<input type="text" id="whCpFullName" value="' + esc(cp.full_name) + '" />'
          ) +
          formField(
            "Юридический адрес",
            '<input type="text" id="whCpLegalAddress" value="' + esc(cp.legal_address) + '" />'
          ) +
          formField(
            "Комментарий к адресу",
            '<input type="text" id="whCpAddressComment" value="' + esc(cp.address_comment) + '" />'
          ) +
          formField(
            "Код ФИАС",
            '<input type="text" id="whCpFias" value="' + esc(cp.fias_code) + '" />'
          ) +
          formField("КПП", '<input type="text" id="whCpKpp" value="' + esc(cp.kpp) + '" />') +
          formField("ОГРН", '<input type="text" id="whCpOgrn" value="' + esc(cp.ogrn) + '" />') +
          formField("ОКПО", '<input type="text" id="whCpOkpo" value="' + esc(cp.okpo) + '" />') +
          "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">4. Цены и скидки</h4>' +
          '<div class="wh-form-row">' +
          formField(
            "Цены",
            '<select id="whCpPriceType" data-prev="' +
              esc(cp.price_type_id || "") +
              '">' +
              buildSelectOptions(meta.price_types, cp.price_type_id) +
              "</select>"
          ) +
          formField(
            "Номер дисконтной карты",
            '<input type="text" id="whCpDiscount" value="' + esc(cp.discount_card_number) + '" />'
          ) +
          "</div></section>";

        bindConfigureSelect(root.querySelector("#whCpStatus"), modalStatusesEditor);
        bindConfigureSelect(root.querySelector("#whCpGroup"), function () {
          modalSimpleDict("Группы контрагентов", "/api/warehouse/crm/groups", "groups", function () {
            renderForm(editingId);
          });
        });
        bindConfigureSelect(root.querySelector("#whCpType"), function () {
          modalSimpleDict("Типы контрагентов", "/api/warehouse/crm/types", "types", function () {
            renderForm(editingId);
          });
        });
        bindConfigureSelect(root.querySelector("#whCpPriceType"), function () {
          window.open("/warehouse#tab=products&item=price-types", "_blank");
        });

        root.querySelector("#whCrmBackList").addEventListener("click", function () {
          viewMode = "list";
          editingId = null;
          renderList();
        });
        root.querySelector("#whCpAddContact").addEventListener("click", function () {
          var wrap = root.querySelector("#whCpContacts");
          var idx = wrap.querySelectorAll(".wh-crm-contact-card").length;
          wrap.insertAdjacentHTML("beforeend", contactPersonBlock({}, idx));
          bindContactRemove(root);
        });
        bindContactRemove(root);
        root.querySelector("#whCrmSaveCp").addEventListener("click", function () {
          saveCounterparty(root, counterpartyId);
        });
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function bindContactRemove(root) {
    root.querySelectorAll(".wh-crm-remove-contact").forEach(function (btn) {
      btn.onclick = function () {
        var card = btn.closest(".wh-crm-contact-card");
        if (card) card.remove();
      };
    });
  }

  function emptyCounterparty() {
    var defStatus = meta.statuses.length ? meta.statuses[0].id : null;
    var defType = meta.types.length ? meta.types[0].id : null;
    return {
      status_id: defStatus,
      group_id: null,
      phone: "",
      email: "",
      type_id: defType,
      inn: "",
      full_name: "",
      legal_address: "",
      address_comment: "",
      fias_code: "",
      kpp: "",
      ogrn: "",
      okpo: "",
      price_type_id: null,
      discount_card_number: "",
      contact_persons: [],
    };
  }

  function collectForm(root) {
    var contacts = [];
    root.querySelectorAll(".wh-crm-contact-card").forEach(function (card) {
      var c = {};
      card.querySelectorAll("[data-cp-field]").forEach(function (inp) {
        c[inp.getAttribute("data-cp-field")] = inp.value.trim();
      });
      if (c.full_name || c.phone || c.email || c.position || c.comment) contacts.push(c);
    });
    return {
      status_id: root.querySelector("#whCpStatus").value || null,
      group_id: root.querySelector("#whCpGroup").value || null,
      phone: root.querySelector("#whCpPhone").value.trim(),
      email: root.querySelector("#whCpEmail").value.trim(),
      type_id: root.querySelector("#whCpType").value || null,
      inn: root.querySelector("#whCpInn").value.trim(),
      full_name: root.querySelector("#whCpFullName").value.trim(),
      legal_address: root.querySelector("#whCpLegalAddress").value.trim(),
      address_comment: root.querySelector("#whCpAddressComment").value.trim(),
      fias_code: root.querySelector("#whCpFias").value.trim(),
      kpp: root.querySelector("#whCpKpp").value.trim(),
      ogrn: root.querySelector("#whCpOgrn").value.trim(),
      okpo: root.querySelector("#whCpOkpo").value.trim(),
      price_type_id: root.querySelector("#whCpPriceType").value || null,
      discount_card_number: root.querySelector("#whCpDiscount").value.trim(),
      contact_persons: contacts,
    };
  }

  function saveCounterparty(root, counterpartyId) {
    var msg = root.querySelector("#whCrmFormMsg");
    msg.textContent = "";
    msg.className = "wh-msg";
    var body = collectForm(root);
    var req = counterpartyId
      ? fetchJson("/api/warehouse/crm/counterparties/" + counterpartyId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      : fetchJson("/api/warehouse/crm/counterparties", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
    req
      .then(function () {
        msg.className = "wh-msg wh-msg-ok";
        msg.textContent = "Сохранено.";
        if (!counterpartyId) {
          viewMode = "list";
          renderList();
        }
      })
      .catch(function (err) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Ошибка сохранения";
      });
  }

  function renderPriceTypes(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    loadMeta()
      .then(function () {
        root.innerHTML =
          '<p class="wh-msg">Настройка видов цен для контрагентов и товаров.</p>' +
          '<div id="whPriceTypeRows"></div>' +
          '<button type="button" class="wh-btn wh-btn-sm" id="whAddPriceType">+ Добавить вид цены</button>' +
          '<div class="wh-form-actions"><button type="button" class="wh-btn wh-btn-primary" id="whSavePriceTypes">Сохранить</button></div>' +
          '<p class="wh-msg" id="whPriceTypeMsg"></p>';
        renderPriceTypeRows(root);
        root.querySelector("#whAddPriceType").addEventListener("click", function () {
          meta.price_types.push({ name: "" });
          renderPriceTypeRows(root);
        });
        root.querySelector("#whSavePriceTypes").addEventListener("click", function () {
          var items = [];
          root.querySelectorAll(".wh-crm-dict-row").forEach(function (row) {
            var name = row.querySelector(".wh-crm-dict-name").value.trim();
            if (!name) return;
            var item = { name: name };
            var id = row.getAttribute("data-id");
            if (id) item.id = parseInt(id, 10);
            items.push(item);
          });
          fetchJson("/api/warehouse/crm/price-types", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ items: items }),
          })
            .then(function (data) {
              meta.price_types = data.price_types || [];
              var msg = root.querySelector("#whPriceTypeMsg");
              msg.className = "wh-msg wh-msg-ok";
              msg.textContent = "Сохранено.";
              renderPriceTypeRows(root);
            })
            .catch(function (err) {
              var msg = root.querySelector("#whPriceTypeMsg");
              msg.className = "wh-msg wh-msg-error";
              msg.textContent = err.message;
            });
        });
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function renderPriceTypeRows(root) {
    var html = meta.price_types
      .map(function (p) {
        return (
          '<div class="wh-modal-row wh-crm-dict-row" data-id="' +
          esc(p.id || "") +
          '"><input type="text" class="wh-crm-dict-name" value="' +
          esc(p.name) +
          '" placeholder="Название вида цены" />' +
          '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button></div>'
        );
      })
      .join("");
    var wrap = root.querySelector("#whPriceTypeRows");
    wrap.innerHTML = html || '<p class="wh-msg">Нет видов цен. Добавьте первый.</p>';
    wrap.querySelectorAll(".wh-crm-dict-remove").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var row = btn.closest(".wh-crm-dict-row");
        var id = row.getAttribute("data-id");
        if (id) {
          meta.price_types = meta.price_types.filter(function (p) {
            return String(p.id) !== String(id);
          });
        } else {
          row.remove();
        }
        renderPriceTypeRows(root);
      });
    });
  }

  function renderCounterparties(tab, item) {
    preparePanel(tab, item);
    viewMode = "list";
    editingId = null;
    listFilters = {};
    filterPanelOpen = false;
    loadMeta()
      .then(function () {
        renderList();
      })
      .catch(function (err) {
        panelEl().innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  global.WhCrm = {
    renderCounterparties: renderCounterparties,
    renderPriceTypes: renderPriceTypes,
  };
})(window);
