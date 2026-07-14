(function (global) {
  var meta = { price_types: [] };

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

  function formatMoney(value) {
    if (value == null || value === "") return "—";
    var n = Number(value);
    if (Number.isNaN(n)) return esc(value);
    return n.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  }

  function buildPriceTypeOptions(selectedId) {
    return (meta.price_types || [])
      .map(function (pt) {
        var sel = String(selectedId || "") === String(pt.id) ? " selected" : "";
        return '<option value="' + esc(pt.id) + '"' + sel + ">" + esc(pt.name) + "</option>";
      })
      .join("");
  }

  function formatCatalogPrice(row) {
    if (row.missing_catalog_price || row.catalog_price == null) {
      return '<span class="wh-repricer-missing-price">' + esc(row.note || "У товара нет выбранного вида цен") + "</span>";
    }
    return formatMoney(row.catalog_price);
  }

  function renderPreviewTable(rows) {
    if (!rows || !rows.length) {
      return '<p class="wh-muted">Нет строк для предпросмотра.</p>';
    }
    var body = rows
      .map(function (row) {
        var rowClass = "";
        if (row.updated) rowClass = " wh-repricer-row-updated";
        else if (row.missing_catalog_price) rowClass = " wh-repricer-row-missing-price";
        return (
          "<tr class=\"" + rowClass + '">' +
          "<td><code>" + esc(row.sku) + "</code></td>" +
          "<td>" + esc(row.name) + "</td>" +
          "<td>" + formatMoney(row.showcase_price) + "</td>" +
          "<td>" + formatMoney(row.estimated_card_price) + "</td>" +
          "<td>" + formatCatalogPrice(row) + "</td>" +
          "<td>" + formatMoney(row.recommended_seller_price) + "</td>" +
          "<td>" + esc(row.note) + "</td>" +
          "</tr>"
        );
      })
      .join("");
    return (
      '<table class="wh-employees-table wh-repricer-table">' +
      "<thead><tr>" +
      "<th>Артикул</th><th>Название</th><th>На витрине</th><th>Расч. по карте</th>" +
      "<th>Вид цен</th><th>Рекоменд. «Цена *»</th><th>Комментарий</th>" +
      "</tr></thead><tbody>" +
      body +
      "</tbody></table>"
    );
  }

  function downloadBlob(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 500);
  }

  function parseHeaderJson(name, headers) {
    var raw = headers.get(name);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }

  function bindForm(root) {
    var form = root.querySelector("#whRepricerForm");
    var msg = root.querySelector("#whRepricerMsg");
    var preview = root.querySelector("#whRepricerPreview");
    if (!form) return;

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      msg.className = "wh-msg";
      msg.textContent = "";
      preview.innerHTML = "";

      var priceTypeId = root.querySelector("#whRepricerPriceType").value;
      var fileInput = root.querySelector("#whRepricerFile");
      if (!priceTypeId) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите вид цен.";
        return;
      }
      if (!fileInput.files || !fileInput.files.length) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите файл Excel (.xlsx) из Яндекс Маркета.";
        return;
      }

      var formData = new FormData();
      formData.append("price_type_id", priceTypeId);
      formData.append("file", fileInput.files[0]);

      var submitBtn = root.querySelector("#whRepricerSubmit");
      submitBtn.disabled = true;
      msg.textContent = "Считаем рекомендации...";

      fetch("/api/warehouse/marketplaces/repricer/calculate", {
        method: "POST",
        credentials: "include",
        body: formData,
      })
        .then(function (r) {
          var stats = parseHeaderJson("X-Repricer-Stats", r.headers);
          var rows = parseHeaderJson("X-Repricer-Preview", r.headers);
          if (!r.ok) {
            return r.text().then(function (text) {
              var detail = text || "HTTP " + r.status;
              try {
                var json = JSON.parse(text);
                if (json && json.detail) detail = json.detail;
              } catch (e2) {}
              throw new Error(typeof detail === "string" ? detail : "Ошибка расчёта");
            });
          }
          return r.blob().then(function (blob) {
            return { blob: blob, stats: stats, rows: rows };
          });
        })
        .then(function (result) {
          downloadBlob(result.blob, "yandex_repricer_result.xlsx");
          preview.innerHTML = renderPreviewTable(result.rows || []);
          var stats = result.stats || {};
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent =
            "Готово. Обновлено строк: " +
            (stats.updated || 0) +
            " из " +
            (stats.with_showcase || 0) +
            " с витриной (" +
            (stats.with_catalog_price || 0) +
            " с ценой в каталоге, " +
            (stats.skipped_no_catalog_price || 0) +
            " без выбранного вида цен). Файл скачан.";
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || String(err);
        })
        .finally(function () {
          submitBtn.disabled = false;
        });
    });
  }

  function render(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-placeholder">Загрузка...</p>';
    fetchJson("/api/warehouse/marketplaces/repricer/meta")
      .then(function (data) {
        meta = data || meta;
        root.innerHTML =
          '<div class="wh-route-card">' +
          '<p class="wh-muted">Загрузите файл базовых цен Яндекс Маркета. Для строк с ценой «На витрине» рассчитается примерная цена по карте. ' +
          "Цена меняется, если по карте ниже вида цен на 1% и более или выше на 10% и более. В файл попадут только товары с изменённой ценой.</p>" +
          '<form id="whRepricerForm" class="wh-repricer-form">' +
          '<label><span>Вид цен для сравнения</span><select id="whRepricerPriceType" required>' +
          '<option value="">— выберите —</option>' +
          buildPriceTypeOptions("") +
          "</select></label>" +
          '<label class="wh-import-file-label">Файл цен Яндекс (.xlsx)<input type="file" id="whRepricerFile" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required /></label>' +
          '<div class="wh-tools-actions">' +
          '<button type="submit" class="wh-btn wh-btn-primary" id="whRepricerSubmit">Рассчитать и скачать</button>' +
          "</div>" +
          "</form>" +
          '<p class="wh-msg" id="whRepricerMsg"></p>' +
          '<div id="whRepricerPreview"></div>' +
          "</div>";
        bindForm(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  global.WhRepricer = { render: render };
})(window);
