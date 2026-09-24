(function (global) {
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

  function download(blob) {
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = "PRICAT_result.xlsx";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 500);
  }

  function bind(root) {
    root.querySelector("#whViPricatForm").addEventListener("submit", function (event) {
      event.preventDefault();
      var stock = root.querySelector("#whViStockFile");
      var message = root.querySelector("#whViPricatMessage");
      if (!stock.files.length) {
        message.className = "wh-msg wh-msg-error";
        message.textContent = "Выберите выгрузку остатков.";
        return;
      }
      var data = new FormData();
      data.append("stock_file", stock.files[0]);
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
          download(result.blob);
          message.className = "wh-msg wh-msg-ok";
          message.textContent =
            "Готово. Обновлено строк: " + (result.stats.updated || 0) +
            ". Артикулов из выгрузки без строки в PRICAT: " +
            (result.stats.not_found_in_pricat || 0) + ".";
        })
        .catch(function (error) {
          message.className = "wh-msg wh-msg-error";
          message.textContent = error.message || String(error);
        })
        .finally(function () { button.disabled = false; });
    });
  }

  function render(tab, item) {
    shell().contentTitleEl.textContent = item.title;
    shell().contentBreadcrumbEl.textContent = tab.title + " → " + item.title + " → ВсеИнструменты";
    shell().contentPlaceholderEl.hidden = true;
    panelEl().hidden = false;
    panelEl().innerHTML =
      '<section class="wh-crm-section">' +
      '<h3 class="wh-crm-section-title">ВсеИнструменты</h3>' +
      '<p class="wh-muted">Свободный остаток делится на 3 с округлением вниз и записывается в столбец «Остаток на складе». Дата обновляется в C6.</p>' +
      '<form id="whViPricatForm" class="wh-form">' +
      '<label>Выгрузка остатков (артикул в A, свободно в E)<input id="whViStockFile" type="file" accept=".xlsx" required></label>' +
      '<button id="whViPricatSubmit" class="wh-btn wh-btn-primary" type="submit">Сформировать PRICAT</button>' +
      '<p id="whViPricatMessage" class="wh-msg"></p>' +
      '</form></section>';
    bind(panelEl());
  }

  global.WhPriceLists = { render: render };
})(window);
