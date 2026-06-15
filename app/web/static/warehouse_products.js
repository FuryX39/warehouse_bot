(function (global) {
  var CONFIGURE_VALUE = "__configure__";
  var meta = { groups: [], units: [], marking_types: [] };
  var listFilters = {};
  var filterPanelOpen = false;
  var editingId = null;
  var formIsKit = false;
  var kitComponents = [];

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
    return fetchJson("/api/warehouse/catalog/meta").then(function (data) {
      meta.groups = data.groups || [];
      meta.units = data.units || [];
      meta.marking_types = data.marking_types || [];
      return meta;
    });
  }

  function defaultUnitId() {
    for (var i = 0; i < meta.units.length; i++) {
      if (meta.units[i].is_default) return meta.units[i].id;
    }
    return meta.units[0] ? meta.units[0].id : null;
  }

  function defaultMarkingId() {
    for (var i = 0; i < meta.marking_types.length; i++) {
      if (meta.marking_types[i].is_default) return meta.marking_types[i].id;
    }
    return meta.marking_types[0] ? meta.marking_types[0].id : null;
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

  function modalDictEditor(title, apiPath, key, keepDefault, onDone) {
    var rows = (meta[key] || []).map(function (item) {
      return (
        '<div class="wh-modal-row wh-crm-dict-row" data-id="' +
        esc(item.id) +
        '"><input type="text" class="wh-crm-dict-name" value="' +
        esc(item.name) +
        '" placeholder="Название" />' +
        (keepDefault && item.is_default
          ? '<span class="wh-crm-dict-tag">по умолчанию</span>'
          : '<button type="button" class="wh-btn wh-btn-sm wh-crm-dict-remove">Удалить</button>') +
        "</div>"
      );
    });
    var rowsId = "whCatRows" + key;
    var addId = "whCatAdd" + key;
    openModal(
      title,
      '<div id="' + rowsId + '">' + rows.join("") + "</div>" +
        '<button type="button" class="wh-btn wh-btn-sm" id="' + addId + '">+ Добавить</button>',
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
    var q = root.querySelector("#whCatQuickSearch");
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
    if (type === "kind") {
      var kinds = [
        { v: "", t: "—" },
        { v: "product", t: "Товар" },
        { v: "kit", t: "Комплект" },
      ];
      var kindOpts = kinds
        .map(function (k) {
          var sel = listFilters.kind === k.v ? " selected" : "";
          return '<option value="' + esc(k.v) + '"' + sel + ">" + esc(k.t) + "</option>";
        })
        .join("");
      return (
        '<div><label>' + esc(label) + '</label><select data-filter="kind">' + kindOpts + "</select></div>"
      );
    }
    return (
      '<div><label>' + esc(label) + '</label><input type="text" data-filter="' + key + '" value="' + val + '" /></div>'
    );
  }

  function renderFilterPanel() {
    return (
      '<div class="wh-crm-filters' + (filterPanelOpen ? "" : " hidden") + '" id="whCatFilters">' +
      '<h4 class="wh-crm-section-title">Фильтр по полям</h4>' +
      '<div class="wh-crm-filter-grid">' +
      filterField("kind", "Тип", "kind") +
      filterField("name", "Название", "text") +
      filterField("sku", "Артикул", "text") +
      filterField("code", "Код", "text") +
      filterField("external_code", "Внешний код", "text") +
      filterField("group_id", "Группа", "select", meta.groups) +
      filterField("unit_id", "Ед. изм.", "select", meta.units) +
      filterField("marking_type_id", "Маркировка", "select", meta.marking_types) +
      filterField("country", "Страна", "text") +
      filterField("barcode", "Штрихкод", "text") +
      filterField("description", "Описание", "text") +
      filterField("weight", "Вес", "text") +
      filterField("volume", "Объем", "text") +
      "</div>" +
      '<div class="wh-form-actions">' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whCatApplyFilter">Применить</button>' +
      '<button type="button" class="wh-btn" id="whCatResetFilter">Сбросить</button>' +
      "</div></div>"
    );
  }

  function renderListTable(items) {
    if (!items.length) return '<p class="wh-msg">Товары не найдены.</p>';
    var rows = items
      .map(function (p) {
        var typeLabel = p.is_kit
          ? '<span class="wh-badge wh-badge-admin">комплект</span>'
          : "товар";
        return (
          "<tr data-id=\"" + p.id + '">' +
          "<td>" + esc(p.name) + "</td>" +
          "<td>" + typeLabel + "</td>" +
          "<td><code>" + esc(p.sku) + "</code></td>" +
          "<td><code>" + esc(p.code) + "</code></td>" +
          "<td>" + esc(p.group_name || "—") + "</td>" +
          "<td>" + esc(p.unit_name || "—") + "</td>" +
          "<td>" + esc(p.barcode_count || 0) + "</td></tr>"
        );
      })
      .join("");
    return (
      '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
      "<th>Название</th><th>Тип</th><th>Артикул</th><th>Код</th><th>Группа</th><th>Ед.</th><th>ШК</th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table>"
    );
  }

  function renderList() {
    var root = panelEl();
    root.innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    fetchJson("/api/warehouse/catalog/products" + filtersQuery())
      .then(function (data) {
        root.innerHTML =
          '<div class="wh-crm-toolbar">' +
          '<input type="search" id="whCatQuickSearch" class="wh-crm-search" placeholder="Быстрый поиск…" value="' + esc(listFilters.q || "") + '" />' +
          '<button type="button" class="wh-btn" id="whCatToggleFilter">Фильтр</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whCatCreate">+ Товар</button>' +
          '<button type="button" class="wh-btn" id="whCatCreateKit">+ Комплект</button>' +
          "</div>" +
          renderFilterPanel() +
          '<div id="whCatListWrap"></div>';
        root.querySelector("#whCatListWrap").innerHTML = renderListTable(data.products || []);
        bindListEvents(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function bindListEvents(root) {
    root.querySelector("#whCatCreate").addEventListener("click", function () {
      editingId = null;
      formIsKit = false;
      kitComponents = [];
      renderForm(null, false);
    });
    root.querySelector("#whCatCreateKit").addEventListener("click", function () {
      editingId = null;
      formIsKit = true;
      kitComponents = [];
      renderForm(null, true);
    });
    root.querySelector("#whCatToggleFilter").addEventListener("click", function () {
      filterPanelOpen = !filterPanelOpen;
      var fp = root.querySelector("#whCatFilters");
      if (fp) fp.classList.toggle("hidden", !filterPanelOpen);
    });
    root.querySelector("#whCatApplyFilter").addEventListener("click", function () {
      listFilters = readFilterPanel(root);
      renderList();
    });
    root.querySelector("#whCatResetFilter").addEventListener("click", function () {
      listFilters = {};
      filterPanelOpen = false;
      renderList();
    });
    root.querySelector("#whCatQuickSearch").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        listFilters = readFilterPanel(root);
        renderList();
      }
    });
    root.querySelectorAll("tbody tr[data-id]").forEach(function (tr) {
      tr.style.cursor = "pointer";
      tr.addEventListener("click", function () {
        editingId = parseInt(tr.getAttribute("data-id"), 10);
        renderForm(editingId, null);
      });
    });
  }

  function barcodeRow(value) {
    return (
      '<div class="wh-crm-barcode-row">' +
      '<input type="text" class="wh-crm-barcode-input" value="' + esc(value || "") + '" placeholder="Code128" />' +
      '<button type="button" class="wh-btn wh-btn-sm wh-crm-barcode-remove" title="Удалить">&times;</button></div>'
    );
  }

  function componentRow(c) {
    c = c || {};
    var label = (c.component_name || c.name || "") + " (" + (c.component_sku || c.sku || "") + ")";
    if (c.component_is_kit || c.is_kit) label += " · комплект";
    return (
      '<div class="wh-crm-component-row" data-component-id="' + esc(c.component_product_id || c.id || "") + '">' +
      '<span class="wh-crm-component-label">' + esc(label) + "</span>" +
      '<label class="wh-crm-qty-label">Кол-во <input type="number" class="wh-crm-component-qty" min="1" value="' + esc(c.quantity || 1) + '" /></label>' +
      '<button type="button" class="wh-btn wh-btn-sm wh-crm-component-remove">&times;</button></div>'
    );
  }

  function renderForm(productId, forceKit) {
    var root = panelEl();
    root.innerHTML = "<p class=\"wh-msg\">Загрузка…</p>";
    var load = productId
      ? fetchJson("/api/warehouse/catalog/products/" + productId).then(function (d) {
          return d.product;
        })
      : Promise.resolve({
          is_kit: !!forceKit,
          name: "",
          image_url: "",
          description: "",
          group_id: null,
          country: "",
          sku: "",
          code: "",
          external_code: "",
          unit_id: defaultUnitId(),
          weight: "",
          volume: "",
          marking_type_id: defaultMarkingId(),
          barcodes: [],
          components: [],
        });
    load
      .then(function (p) {
        formIsKit = forceKit !== null ? !!forceKit : !!p.is_kit;
        kitComponents = (p.components || []).slice();
        var barcodesHtml = (p.barcodes || []).map(barcodeRow).join("");
        var componentsHtml = kitComponents.map(componentRow).join("");
        root.innerHTML =
          '<div class="wh-crm-form-toolbar">' +
          '<button type="button" class="wh-btn" id="whCatBackList">&larr; К списку</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whCatSave">Сохранить</button>' +
          "</div>" +
          '<p class="wh-msg" id="whCatFormMsg"></p>' +
          (formIsKit ? '<p class="wh-msg"><span class="wh-badge wh-badge-admin">Комплект</span></p>' : "") +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">1. Общие данные</h4>' +
          '<div class="wh-form-row">' +
          '<div class="wh-form-wide"><label>Название</label><input type="text" id="whPrName" value="' + esc(p.name) + '" /></div>' +
          '<div class="wh-form-wide"><label>Изображение (ссылка)</label><input type="text" id="whPrImage" value="' + esc(p.image_url) + '" /></div>' +
          '<div class="wh-form-wide"><label>Описание</label><input type="text" id="whPrDesc" value="' + esc(p.description) + '" /></div>' +
          '<div><label>Группа</label><select id="whPrGroup" data-prev="' + esc(p.group_id || "") + '">' + buildSelectOptions(meta.groups, p.group_id) + "</select></div>" +
          '<div><label>Страна</label><input type="text" id="whPrCountry" value="' + esc(p.country) + '" /></div>' +
          '<div><label>Артикул</label><input type="text" id="whPrSku" value="' + esc(p.sku) + '" /></div>' +
          '<div><label>Код</label><input type="text" id="whPrCode" value="' + esc(p.code) + '" /></div>' +
          '<div><label>Внешний код</label><input type="text" id="whPrExtCode" value="' + esc(p.external_code) + '" /></div>' +
          '<div><label>Единица измерения</label><select id="whPrUnit" data-prev="' + esc(p.unit_id || "") + '">' + buildSelectOptions(meta.units, p.unit_id) + "</select></div>" +
          '<div><label>Вес</label><input type="text" id="whPrWeight" value="' + esc(p.weight) + '" /></div>' +
          '<div><label>Объем</label><input type="text" id="whPrVolume" value="' + esc(p.volume) + '" /></div>' +
          "</div></section>" +
          '<section class="wh-crm-section"><h4 class="wh-crm-section-title">2. Маркировка</h4>' +
          '<div class="wh-form-row"><div><label>Тип маркировки</label><select id="whPrMarking" data-prev="' + esc(p.marking_type_id || "") + '">' + buildSelectOptions(meta.marking_types, p.marking_type_id) + "</select></div></div></section>" +
          '<section class="wh-crm-section"><div class="wh-crm-section-head"><h4 class="wh-crm-section-title">3. Штрихкоды (Code128)</h4>' +
          '<button type="button" class="wh-btn wh-btn-sm wh-crm-icon-btn" id="whPrAddBarcode" title="Добавить">+</button></div>' +
          '<div id="whPrBarcodes">' + barcodesHtml + "</div></section>" +
          (formIsKit
            ? '<section class="wh-crm-section" id="whPrKitSection"><div class="wh-crm-section-head">' +
              '<h4 class="wh-crm-section-title">4. Компоненты</h4>' +
              '<button type="button" class="wh-btn wh-btn-sm wh-crm-icon-btn" id="whPrAddComponent" title="Добавить">+</button></div>' +
              '<div id="whPrComponents">' + componentsHtml + "</div></section>"
            : "");

        bindConfigureSelect(root.querySelector("#whPrGroup"), function () {
          modalDictEditor("Группы товаров", "/api/warehouse/catalog/groups", "groups", false, function () {
            renderForm(editingId, formIsKit);
          });
        });
        bindConfigureSelect(root.querySelector("#whPrUnit"), function () {
          modalDictEditor("Единицы измерения", "/api/warehouse/catalog/units", "units", true, function () {
            renderForm(editingId, formIsKit);
          });
        });
        bindConfigureSelect(root.querySelector("#whPrMarking"), function () {
          modalDictEditor("Маркировка", "/api/warehouse/catalog/marking-types", "marking_types", true, function () {
            renderForm(editingId, formIsKit);
          });
        });

        root.querySelector("#whCatBackList").addEventListener("click", function () {
          editingId = null;
          renderList();
        });
        root.querySelector("#whPrAddBarcode").addEventListener("click", function () {
          root.querySelector("#whPrBarcodes").insertAdjacentHTML("beforeend", barcodeRow(""));
        });
        root.querySelector("#whPrBarcodes").addEventListener("click", function (e) {
          if (e.target.classList.contains("wh-crm-barcode-remove")) {
            e.target.closest(".wh-crm-barcode-row").remove();
          }
        });
        if (formIsKit) {
          root.querySelector("#whPrAddComponent").addEventListener("click", openComponentPicker);
          root.querySelector("#whPrComponents").addEventListener("click", function (e) {
            if (e.target.classList.contains("wh-crm-component-remove")) {
              e.target.closest(".wh-crm-component-row").remove();
            }
          });
        }
        root.querySelector("#whCatSave").addEventListener("click", function () {
          saveProduct(root, productId);
        });
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function openComponentPicker() {
    var exclude = editingId ? "&exclude_id=" + encodeURIComponent(editingId) : "";
    openModal(
      "Добавить компонент",
      '<input type="search" id="whPickerSearch" class="wh-crm-search" placeholder="Поиск по названию, артикулу…" />' +
        '<div id="whPickerList" class="wh-picker-list"><p class="wh-msg">Загрузка…</p></div>',
      function (backdrop, close) {
        close();
      }
    );
    var backdrop = document.querySelector(".wh-modal-backdrop:last-of-type");
    if (!backdrop) return;
    backdrop.querySelector(".wh-modal-save").textContent = "Закрыть";
    backdrop.querySelector(".wh-modal-save").addEventListener("click", function () {
      backdrop.remove();
    });

    function loadPicker(q) {
      var url = "/api/warehouse/catalog/products/picker?q=" + encodeURIComponent(q || "") + exclude;
      fetchJson(url).then(function (data) {
        var list = backdrop.querySelector("#whPickerList");
        var products = data.products || [];
        if (!products.length) {
          list.innerHTML = '<p class="wh-msg">Ничего не найдено.</p>';
          return;
        }
        list.innerHTML = products
          .map(function (p) {
            var tag = p.is_kit ? ' <span class="wh-badge wh-badge-admin">комплект</span>' : "";
            return (
              '<button type="button" class="wh-picker-item" data-pick-id="' +
              esc(p.id) +
              '">' +
              esc(p.name) +
              " · " +
              esc(p.sku) +
              tag +
              "</button>"
            );
          })
          .join("");
        list.querySelectorAll(".wh-picker-item").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var id = parseInt(btn.getAttribute("data-pick-id"), 10);
            var picked = null;
            for (var i = 0; i < products.length; i++) {
              if (products[i].id === id) {
                picked = products[i];
                break;
              }
            }
            if (!picked) return;
            var wrap = document.getElementById("whPrComponents");
            if (!wrap) return;
            if (wrap.querySelector('[data-component-id="' + id + '"]')) {
              alert("Этот товар уже в составе комплекта");
              return;
            }
            wrap.insertAdjacentHTML(
              "beforeend",
              componentRow({
                component_product_id: picked.id,
                component_name: picked.name,
                component_sku: picked.sku,
                component_is_kit: picked.is_kit,
                quantity: 1,
              })
            );
            backdrop.remove();
          });
        });
      });
    }

    loadPicker("");
    var search = backdrop.querySelector("#whPickerSearch");
    var timer = null;
    search.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        loadPicker(search.value.trim());
      }, 250);
    });
  }

  function collectForm(root) {
    var barcodes = [];
    root.querySelectorAll(".wh-crm-barcode-input").forEach(function (inp) {
      var v = inp.value.trim();
      if (v) barcodes.push(v);
    });
    var components = [];
    if (formIsKit) {
      root.querySelectorAll(".wh-crm-component-row").forEach(function (row) {
        var id = parseInt(row.getAttribute("data-component-id"), 10);
        var qty = parseInt(row.querySelector(".wh-crm-component-qty").value, 10) || 1;
        if (id) components.push({ component_product_id: id, quantity: Math.max(1, qty) });
      });
    }
    return {
      is_kit: formIsKit,
      name: root.querySelector("#whPrName").value.trim(),
      image_url: root.querySelector("#whPrImage").value.trim(),
      description: root.querySelector("#whPrDesc").value.trim(),
      group_id: root.querySelector("#whPrGroup").value || null,
      country: root.querySelector("#whPrCountry").value.trim(),
      sku: root.querySelector("#whPrSku").value.trim(),
      code: root.querySelector("#whPrCode").value.trim(),
      external_code: root.querySelector("#whPrExtCode").value.trim(),
      unit_id: root.querySelector("#whPrUnit").value || null,
      weight: root.querySelector("#whPrWeight").value.trim(),
      volume: root.querySelector("#whPrVolume").value.trim(),
      marking_type_id: root.querySelector("#whPrMarking").value || null,
      barcodes: barcodes,
      components: components,
    };
  }

  function saveProduct(root, productId) {
    var msg = root.querySelector("#whCatFormMsg");
    msg.textContent = "";
    msg.className = "wh-msg";
    var body = collectForm(root);
    var req = productId
      ? fetchJson("/api/warehouse/catalog/products/" + productId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      : fetchJson("/api/warehouse/catalog/products", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
    req
      .then(function () {
        msg.className = "wh-msg wh-msg-ok";
        msg.textContent = "Сохранено.";
        if (!productId) {
          editingId = null;
          renderList();
        }
      })
      .catch(function (err) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Ошибка сохранения";
      });
  }

  function renderCatalog(tab, item) {
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

  global.WhProducts = {
    renderCatalog: renderCatalog,
  };
})(window);
