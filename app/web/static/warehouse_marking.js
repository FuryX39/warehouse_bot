(function (global) {
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function shell() {
    return global.WH_SHELL || {};
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

  function codesTable(headers, rowsHtml) {
    if (!rowsHtml) return "";
    return (
      '<div class="wh-table-wrap"><table class="wh-table"><thead><tr>' +
      headers.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("") +
      "</tr></thead><tbody>" +
      rowsHtml +
      "</tbody></table></div>"
    );
  }

  function sampleCell(sample, count) {
    var shown = (sample || []).map(esc).join("<br>");
    var extra = count > (sample || []).length ? "<br>… ещё " + (count - sample.length) : "";
    return shown + extra;
  }

  function renderPreview(root, data) {
    var box = root.querySelector("#whMarkingPreview");
    if (!box) return;
    var stats = (data && data.stats) || {};
    var groups = data.groups || [];
    var unmatched = data.unmatched || [];
    var conflicts = data.conflicts || [];
    var invalid = data.invalid || [];
    var summary =
      '<p class="wh-msg">Строк: ' +
      esc(stats.total_lines) +
      ", уникальных: " +
      esc(stats.unique_codes) +
      ", сопоставлено: " +
      esc(stats.matched_codes) +
      ", товаров: " +
      esc(stats.product_count) +
      (stats.duplicate_count ? ", дубликатов пропущено: " + esc(stats.duplicate_count) : "") +
      "</p>";

    var groupRows = groups
      .map(function (g) {
        return (
          "<tr><td>" +
          esc(g.sku) +
          "</td><td>" +
          esc(g.gtin) +
          "</td><td>" +
          esc(g.count) +
          '</td><td class="wh-marking-sample">' +
          sampleCell(g.sample, g.count) +
          "</td></tr>"
        );
      })
      .join("");

    var unmatchedRows = unmatched
      .map(function (u) {
        return (
          "<tr><td>" +
          esc(u.gtin) +
          "</td><td>" +
          esc(u.count) +
          '</td><td class="wh-marking-sample">' +
          sampleCell(u.sample, u.count) +
          "</td></tr>"
        );
      })
      .join("");

    var conflictRows = conflicts
      .map(function (c) {
        return (
          "<tr><td>" +
          esc(c.gtin) +
          "</td><td>" +
          esc((c.skus || []).join(", ")) +
          "</td><td>" +
          esc(c.count) +
          "</td></tr>"
        );
      })
      .join("");

    var invalidRows = invalid
      .map(function (row) {
        return "<tr><td>" + esc(row.raw) + "</td><td>" + esc(row.error) + "</td></tr>";
      })
      .join("");

    box.innerHTML =
      summary +
      (groups.length
        ? "<h4 class=\"wh-crm-section-title\">Сопоставлено</h4>" +
          codesTable(["Артикул", "GTIN", "Кодов", "Примеры DataMatrix"], groupRows)
        : "") +
      (unmatched.length
        ? "<h4 class=\"wh-crm-section-title\">GTIN нет в каталоге</h4>" +
          "<p class=\"wh-muted\">Добавьте GTIN в карточке товара (Товары и услуги → Маркировка) или укажите EAN-13/GTIN-14 в штрихкодах.</p>" +
          codesTable(["GTIN", "Кодов", "Примеры"], unmatchedRows)
        : "") +
      (conflicts.length
        ? "<h4 class=\"wh-crm-section-title\">Один GTIN у нескольких товаров</h4>" +
          codesTable(["GTIN", "Артикулы", "Кодов"], conflictRows)
        : "") +
      (invalid.length
        ? "<h4 class=\"wh-crm-section-title\">Не разобраны</h4>" +
          codesTable(["Строка", "Причина"], invalidRows)
        : "");
  }

  function currentText(root) {
    var area = root.querySelector("#whMarkingCodes");
    return area ? area.value : "";
  }

  function setBusy(root, busy) {
    root.querySelectorAll("#whMarkingParse, #whMarkingExport").forEach(function (btn) {
      btn.disabled = !!busy;
    });
  }

  function bind(root) {
    var msg = root.querySelector("#whMarkingMsg");
    var file = root.querySelector("#whMarkingFile");

    file.addEventListener("change", function () {
      var picked = file.files && file.files[0];
      if (!picked) return;
      var reader = new FileReader();
      reader.onload = function () {
        root.querySelector("#whMarkingCodes").value = String(reader.result || "");
      };
      reader.readAsText(picked);
    });

    root.querySelector("#whMarkingParse").addEventListener("click", function () {
      var text = currentText(root);
      msg.className = "wh-msg";
      msg.textContent = "";
      if (!text.trim()) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Вставьте коды Data Matrix.";
        return;
      }
      setBusy(root, true);
      msg.textContent = "Разбираем…";
      shell()
        .fetchJson("/api/warehouse/marking/codes/parse", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: text }),
        })
        .then(function (data) {
          renderPreview(root, data);
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = "Готово. Можно скачать Excel.";
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || "Не удалось разобрать коды";
        })
        .then(function () {
          setBusy(root, false);
        });
    });

    root.querySelector("#whMarkingExport").addEventListener("click", function () {
      var text = currentText(root);
      msg.className = "wh-msg";
      msg.textContent = "";
      if (!text.trim()) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Вставьте коды Data Matrix.";
        return;
      }
      setBusy(root, true);
      msg.textContent = "Формируем Excel…";
      fetch("/api/warehouse/marking/codes/export", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text }),
      })
        .then(function (r) {
          if (!r.ok) {
            return r.text().then(function (body) {
              var detail = body || "HTTP " + r.status;
              try {
                var json = JSON.parse(body);
                if (json && json.detail) detail = json.detail;
              } catch (e) {}
              throw new Error(typeof detail === "string" ? detail : "Не удалось сформировать файл");
            });
          }
          return r.blob();
        })
        .then(function (blob) {
          downloadBlob(blob, "marking_codes.xlsx");
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = "Файл скачан.";
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || "Не удалось сформировать файл";
        })
        .then(function () {
          setBusy(root, false);
        });
    });
  }

  function render(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML =
      '<div class="wh-route-card">' +
      "<p class=\"wh-muted\">Вставьте коды Data Matrix Честного знака (по одному на строку). " +
      "Из каждого кода берётся GTIN и сопоставляется с товаром: сначала поле GTIN в карточке, иначе EAN-13/GTIN-14 в штрихкодах.</p>" +
      '<label class="wh-marking-file-label">Загрузить текстовый файл <input type="file" id="whMarkingFile" accept=".txt,.csv,.tsv,text/plain" /></label>' +
      '<textarea id="whMarkingCodes" class="wh-marking-codes" placeholder="01046…21…&#10;01046…21…"></textarea>' +
      '<div class="wh-tools-actions">' +
      '<button type="button" class="wh-btn" id="whMarkingParse">Разобрать</button>' +
      '<button type="button" class="wh-btn wh-btn-primary" id="whMarkingExport">Скачать Excel</button>' +
      "</div>" +
      '<p class="wh-msg" id="whMarkingMsg"></p>' +
      '<div id="whMarkingPreview"></div>' +
      "</div>";
    bind(root);
  }

  global.WhMarking = { render: render };
})(window);
