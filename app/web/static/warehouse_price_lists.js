(function (global) {
  var meta = { counterparties: [] };

  function shell() {
    return global.WH_SHELL || {};
  }

  function panelEl() {
    return shell().contentPanelEl;
  }

  function errorDetail(response) {
    return response.text().then(function (text) {
      try {
        var body = JSON.parse(text);
        return body.detail || text;
      } catch (e) {
        return text || "HTTP " + response.status;
      }
    });
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function download(blob, filename) {
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 500);
  }

  function today() {
    var value = new Date();
    var month = String(value.getMonth() + 1).padStart(2, "0");
    var day = String(value.getDate()).padStart(2, "0");
    return value.getFullYear() + "-" + month + "-" + day;
  }

  function counterpartyOptions() {
    return (
      '<option value="">— выберите контрагента —</option>' +
      (meta.counterparties || [])
        .map(function (item) {
          var details = [];
          if (item.inn) details.push("ИНН " + item.inn);
          if (item.gln) details.push("GLN " + item.gln);
          return '<option value="' + esc(item.id) + '">' +
            esc(item.name + (details.length ? " · " + details.join(" · ") : "")) +
            "</option>";
        })
        .join("")
    );
  }

  function partyStatus(root, selectId, statusId) {
    var select = root.querySelector(selectId);
    var status = root.querySelector(statusId);
    var item = (meta.counterparties || []).find(function (row) {
      return String(row.id) === String(select.value || "");
    });
    if (!item) {
      status.textContent = "";
      status.className = "wh-pricat-party-status";
      return;
    }
    var missing = [];
    if (!item.name) missing.push("наименование");
    if (!item.inn) missing.push("ИНН");
    if (!item.kpp) missing.push("КПП");
    if (!item.gln) missing.push("GLN");
    status.textContent = missing.length
      ? "Не заполнено в CRM: " + missing.join(", ")
      : "Реквизиты заполнены";
    status.className = "wh-pricat-party-status" + (missing.length ? " is-error" : " is-ok");
  }

  function bind(root) {
    root.querySelector("#whViQuantityTemplate").addEventListener("click", function () {
      fetch("/api/warehouse/products/price-lists/vseinstrumenti/quantity-template", {
        credentials: "include",
      })
        .then(function (response) {
          if (!response.ok) {
            return errorDetail(response).then(function (detail) { throw new Error(detail); });
          }
          return response.blob();
        })
        .then(function (blob) {
          download(blob, "PRICAT_quantity_template.xlsx");
        })
        .catch(function (error) {
          var message = root.querySelector("#whViPricatMessage");
          message.className = "wh-msg wh-msg-error";
          message.textContent = error.message || String(error);
        });
    });
    root.querySelector("#whViBuyer").addEventListener("change", function () {
      partyStatus(root, "#whViBuyer", "#whViBuyerStatus");
    });
    root.querySelector("#whViSupplier").addEventListener("change", function () {
      partyStatus(root, "#whViSupplier", "#whViSupplierStatus");
    });
    root.querySelector("#whViPricatForm").addEventListener("submit", function (event) {
      event.preventDefault();
      var quantity = root.querySelector("#whViQuantityFile");
      var message = root.querySelector("#whViPricatMessage");
      if (!quantity.files.length) {
        message.className = "wh-msg wh-msg-error";
        message.textContent = "Выберите заполненный файл количества.";
        return;
      }
      var data = new FormData();
      [
        ["buyer_id", "#whViBuyer"],
        ["supplier_id", "#whViSupplier"],
        ["document_name", "#whViDocumentName"],
        ["document_date", "#whViDocumentDate"],
        ["contract_number", "#whViContractNumber"],
        ["contract_date", "#whViContractDate"],
        ["price_list_type", "#whViPriceListType"],
        ["valid_from", "#whViValidFrom"],
        ["valid_to", "#whViValidTo"],
        ["internal_comment", "#whViInternalComment"],
        ["buyer_message", "#whViBuyerMessage"],
      ].forEach(function (field) {
        data.append(field[0], root.querySelector(field[1]).value.trim());
      });
      data.append("quantity_file", quantity.files[0]);
      var button = root.querySelector("#whViPricatSubmit");
      button.disabled = true;
      message.className = "wh-msg";
      message.textContent = "Формируем PRICAT...";
      fetch("/api/warehouse/products/price-lists/vseinstrumenti/pricat", {
        method: "POST",
        credentials: "include",
        body: data,
      })
        .then(function (response) {
          if (!response.ok) {
            return errorDetail(response).then(function (detail) { throw new Error(detail); });
          }
          var stats = {};
          try { stats = JSON.parse(response.headers.get("X-PRICAT-Stats") || "{}"); } catch (e) {}
          return response.blob().then(function (blob) { return { blob: blob, stats: stats }; });
        })
        .then(function (result) {
          download(result.blob, "PRICAT_result.xlsx");
          message.className = "wh-msg wh-msg-ok";
          message.textContent =
            "Готово. Записано строк: " + (result.stats.rows_written || 0) +
            ". Рассчитано комплектов: " + (result.stats.kits_calculated || 0) +
            ". Пропущено: " + (result.stats.skipped_names || 0) + ".";
        })
        .catch(function (error) {
          message.className = "wh-msg wh-msg-error";
          message.textContent = error.message || String(error);
        })
        .finally(function () { button.disabled = false; });
    });
  }

  function formHtml() {
    var current = today();
    var options = counterpartyOptions();
    return (
      '<section class="wh-crm-section wh-pricat-section">' +
      '<div class="wh-pricat-head"><div><h3 class="wh-crm-section-title">ВсеИнструменты · PRICAT</h3>' +
      '<p class="wh-muted">Полный ценовой лист на основе мастер-шаблона. Реквизиты берутся из CRM, остатки — из файла «Название — Количество».</p></div>' +
      '<button id="whViQuantityTemplate" class="wh-btn" type="button">Скачать шаблон количества</button></div>' +
      '<form id="whViPricatForm" class="wh-pricat-form">' +
      '<fieldset><legend>1. Стороны</legend><div class="wh-pricat-grid">' +
      '<label>Покупатель<select id="whViBuyer" required>' + options + '</select><small id="whViBuyerStatus" class="wh-pricat-party-status"></small></label>' +
      '<label>Поставщик<select id="whViSupplier" required>' + options + '</select><small id="whViSupplierStatus" class="wh-pricat-party-status"></small></label>' +
      "</div></fieldset>" +
      '<fieldset><legend>2. Документ и договор</legend><div class="wh-pricat-grid">' +
      '<label>Название или номер документа<input id="whViDocumentName" value="Остатки" required></label>' +
      '<label>Дата документа<input id="whViDocumentDate" type="date" value="' + current + '" required></label>' +
      '<label>Номер договора<input id="whViContractNumber" required></label>' +
      '<label>Дата договора<input id="whViContractDate" type="date" value="' + current + '" required></label>' +
      '<label>Тип прайса<select id="whViPriceListType"><option>Основной</option><option>Акционный</option></select></label>' +
      '<label>Цены действуют с<input id="whViValidFrom" type="date" value="' + current + '" required></label>' +
      '<label>Цены действуют по<input id="whViValidTo" type="date" value="' + current + '" required></label>' +
      "</div></fieldset>" +
      '<fieldset><legend>3. Комментарии</legend><div class="wh-pricat-grid">' +
      '<label>Комментарий для себя<input id="whViInternalComment"></label>' +
      '<label>Сообщение покупателю<input id="whViBuyerMessage" placeholder="Актуальные остатки ДД.ММ"></label>' +
      "</div></fieldset>" +
      '<fieldset><legend>4. Количество</legend>' +
      '<label class="wh-pricat-file">Заполненный шаблон количества<input id="whViQuantityFile" type="file" accept=".xlsx" required></label>' +
      '<p class="wh-muted">Скачайте шаблон: в нём уже названия из каталога. Заполните столбец «Количество». Допустимы значения вида 889&nbsp;826,000. Дробная часть отбрасывается. Ненайденные, неоднозначные и отсутствующие в PRICAT строки пропускаются. Комплекты считаются из компонентов.</p>' +
      "</fieldset>" +
      '<div class="wh-pricat-actions"><button id="whViPricatSubmit" class="wh-btn wh-btn-primary" type="submit">Сформировать PRICAT</button></div>' +
      '<p id="whViPricatMessage" class="wh-msg"></p>' +
      "</form></section>"
    );
  }

  function render(tab, item) {
    shell().contentTitleEl.textContent = item.title;
    shell().contentBreadcrumbEl.textContent = tab.title + " → " + item.title + " → ВсеИнструменты";
    shell().contentPlaceholderEl.hidden = true;
    panelEl().hidden = false;
    panelEl().innerHTML = '<p class="wh-msg">Загрузка контрагентов…</p>';
    shell()
      .fetchJson("/api/warehouse/products/price-lists/vseinstrumenti/meta")
      .then(function (data) {
        meta.counterparties = data.counterparties || [];
        panelEl().innerHTML = formHtml();
        bind(panelEl());
      })
      .catch(function (error) {
        panelEl().innerHTML =
          '<p class="wh-msg wh-msg-error">' + esc(error.message || String(error)) + "</p>";
      });
  }

  global.WhPriceLists = { render: render };
})(window);
