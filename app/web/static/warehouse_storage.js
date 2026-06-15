(function (global) {
  var CONFIGURE_VALUE = "__configure__";
  var meta = { groups: [] };
  var listFilters = {};
  var filterPanelOpen = false;
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
    return fetchJson("/api/warehouse/storage/meta").then(function (data) {
      meta.groups = data.groups || [];
      return meta;
    });
  }

  function buildGroupSelectOptions(items, selectedId) {
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

  function modalGroupsEditor(onDone) {
    var rows = meta.groups.map(function (g) {
      return (
        '<div class="wh-modal-row wh-crm-dict-row" data-id="' +
        esc(g.id) +
        '"><input type="text" class="wh-crm-dict-name" value="' +
        esc(g.name) +
        '" placeholder="Название группы" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button></div>'
      );
    });
    openModal(
      "Группы складов",
      '<p class="wh-msg">Группы складов не связаны с группами контрагентов.</p>' +
        '<div id="whStorageGroupRows">' +
        rows.join("") +
        "</div>" +
        '<button type="button" class="wh-btn wh-btn-sm" id="whStorageAddGroup">+ Добавить группу</button>',
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
        fetchJson("/api/warehouse/storage/groups", {
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
    backdrop.querySelector("#whStorageAddGroup").addEventListener("click", function () {
      var row = document.createElement("div");
      row.className = "wh-modal-row wh-crm-dict-row";
      row.innerHTML =
        '<input type="text" class="wh-crm-dict-name" placeholder="Название группы" />' +
        '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>';
      backdrop.querySelector("#whStorageGroupRows").appendChild(row);
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
    var q = root.querySelector("#whStorageQuickSearch");
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

  function renderFilterPanel() {
    return (
      '<div class="wh-crm-filters' +
      (filterPanelOpen ? "" : " hidden") +
      '" id="whStorageFilters">' +
      '<h4 class="wh-crm-section-title">Фильтр по полям</h4>' +
      '<div class="wh-crm-filter-grid">' +
      filterField("name", "Наименование", "text") +
      filterField("code", "Код", "text") +
      filterField("group_id", "Группа", "select", meta.groups) +
      filterField("address", "Адрес", "text") +
      filterField("address_comment", "Комментарий к адресу", "text") +
      filterField("comment", "Комментарий", "text") +
      "</div>" +
      '<div class="wh-form-actions">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whStorageApplyFilter">Применить</button>' +
      '<button type="button" class="wh-btn" id="whStorageResetFilter">Сбросить</button>' +
      "</div></div>"
    );
  }

  function renderListTable(items) {
    if (!items.length) {
      return '<p class="wh-msg">Склады не найдены.</p>';
    }
    var rows = items
      .map(function (wh) {
        var defaultTag = wh.is_default
          ? ' <span class="wh-badge wh-badge-admin">основной</span>'
          : "";
        return (
          "<tr data-id=\"" +
          wh.id +
          '">' +
          "<td>" +
          esc(wh.name) +
          defaultTag +
          "</td>" +
          "<td><code>" +
          esc(wh.code) +
          "</code></td>" +
          "<td>" +
          esc(wh.group_name || "—") +
          "</td>" +
          "<td>" +
          esc(wh.address || "—") +
          "</td>" +
          "<td>" +
          esc(wh.sku_count) +
          "</td>" +
          "<td>" +
          esc(wh.total_stock) +
          "</td></tr>"
        );
      })
      .join("");
    return (
      '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
      "<th>Наименование</th><th>Код</th><th>Группа</th><th>Адрес</th><th>Позиций</th><th>Остаток, шт.</th>" +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table>"
    );
  }

  function renderList() {
    var root = panelEl();
    root.innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    fetchJson("/api/warehouse/storage/warehouses" + filtersQuery())
      .then(function (data) {
        root.innerHTML =
          '<div class="wh-crm-toolbar">' +
          '<input type="search" id="whStorageQuickSearch" class="wh-crm-search" placeholder="Быстрый поиск…" value="' +
          esc(listFilters.q || "") +
          '" />' +
          '<button type="button" class="wh-btn" id="whStorageToggleFilter">Фильтр</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whStorageCreate">+ Добавить склад</button>' +
          "</div>" +
          renderFilterPanel() +
          '<p class="wh-msg">Остатки товаров учитываются отдельно на каждом складе. Старые остатки перенесены на склад «Основной».</p>' +
          '<div id="whStorageListWrap"></div>';
        root.querySelector("#whStorageListWrap").innerHTML = renderListTable(data.warehouses || []);
        bindListEvents(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function bindListEvents(root) {
    root.querySelector("#whStorageCreate").addEventListener("click", function () {
      editingId = null;
      renderForm(null);
    });
    root.querySelector("#whStorageToggleFilter").addEventListener("click", function () {
      filterPanelOpen = !filterPanelOpen;
      var fp = root.querySelector("#whStorageFilters");
      if (fp) fp.classList.toggle("hidden", !filterPanelOpen);
    });
    root.querySelector("#whStorageApplyFilter").addEventListener("click", function () {
      listFilters = readFilterPanel(root);
      renderList();
    });
    root.querySelector("#whStorageResetFilter").addEventListener("click", function () {
      listFilters = {};
      filterPanelOpen = false;
      renderList();
    });
    root.querySelector("#whStorageQuickSearch").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        listFilters = readFilterPanel(root);
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

  function renderForm(warehouseId) {
    var root = panelEl();
    root.innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    var load = warehouseId
      ? fetchJson("/api/warehouse/storage/warehouses/" + warehouseId).then(function (d) {
          return d.warehouse;
        })
      : Promise.resolve({
          name: "",
          code: "",
          address: "",
          address_comment: "",
          comment: "",
          group_id: null,
          is_default: false,
          sku_count: 0,
          total_stock: 0,
        });
    load
      .then(function (wh) {
        root.innerHTML =
          '<div class="wh-crm-form-toolbar">' +
          '<button type="button" class="wh-btn" id="whStorageBackList">&larr; К списку</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whStorageSave">Сохранить</button>' +
          "</div>" +
          '<p class="wh-msg" id="whStorageFormMsg"></p>' +
          (wh.is_default
            ? '<p class="wh-msg">Это основной склад — сюда перенесены остатки из старой панели.</p>'
            : "") +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Склад</h4>' +
          '<div class="wh-form-row">' +
          '<div><label>Наименование</label><input type="text" id="whStName" value="' +
          esc(wh.name) +
          '" /></div>' +
          '<div><label>Код</label><input type="text" id="whStCode" value="' +
          esc(wh.code) +
          '" placeholder="Например, MSK-01" /></div>' +
          '<div><label>Группа</label><select id="whStGroup" data-prev="' +
          esc(wh.group_id || "") +
          '">' +
          buildGroupSelectOptions(meta.groups, wh.group_id) +
          "</select></div>" +
          '<div><label>Адрес</label><input type="text" id="whStAddress" value="' +
          esc(wh.address) +
          '" /></div>' +
          '<div><label>Комментарий к адресу</label><input type="text" id="whStAddressComment" value="' +
          esc(wh.address_comment) +
          '" /></div>' +
          '<div class="wh-form-wide"><label>Комментарий</label><input type="text" id="whStComment" value="' +
          esc(wh.comment) +
          '" /></div>' +
          "</div></section>" +
          (warehouseId
            ? '<section class="wh-crm-section"><h4 class="wh-crm-section-title">Остатки на складе</h4>' +
              '<p class="wh-msg">Позиций: <strong>' +
              esc(wh.sku_count) +
              "</strong>, всего единиц: <strong>" +
              esc(wh.total_stock) +
              "</strong>. Детальный просмотр — в разделе «Остатки» (в разработке).</p></section>"
            : "");

        bindConfigureSelect(root.querySelector("#whStGroup"), function () {
          modalGroupsEditor(function () {
            renderForm(editingId);
          });
        });

        root.querySelector("#whStorageBackList").addEventListener("click", function () {
          editingId = null;
          renderList();
        });
        root.querySelector("#whStorageSave").addEventListener("click", function () {
          saveWarehouse(root, warehouseId);
        });
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function collectForm(root) {
    return {
      name: root.querySelector("#whStName").value.trim(),
      code: root.querySelector("#whStCode").value.trim(),
      group_id: root.querySelector("#whStGroup").value || null,
      address: root.querySelector("#whStAddress").value.trim(),
      address_comment: root.querySelector("#whStAddressComment").value.trim(),
      comment: root.querySelector("#whStComment").value.trim(),
    };
  }

  function saveWarehouse(root, warehouseId) {
    var msg = root.querySelector("#whStorageFormMsg");
    msg.textContent = "";
    msg.className = "wh-msg";
    var body = collectForm(root);
    var req = warehouseId
      ? fetchJson("/api/warehouse/storage/warehouses/" + warehouseId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      : fetchJson("/api/warehouse/storage/warehouses", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
    req
      .then(function () {
        msg.className = "wh-msg wh-msg-ok";
        msg.textContent = "Сохранено.";
        if (!warehouseId) {
          editingId = null;
          renderList();
        }
      })
      .catch(function (err) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Ошибка сохранения";
      });
  }

  function renderWarehouses(tab, item) {
    preparePanel(tab, item);
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

  global.WhStorage = {
    renderWarehouses: renderWarehouses,
  };
})(window);
