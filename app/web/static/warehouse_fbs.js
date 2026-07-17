(function (global) {
  var activeMarketplace = "yandex";
  var labelTokens = { ozon: null, yandex: null };
  var busy = false;

  function shell() {
    return global.WH_SHELL || {};
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function panelEl() {
    return shell().contentPanelEl;
  }

  function setMessage(root, text, isError) {
    var el = root.querySelector("#whFbsMessage");
    if (!el) return;
    el.className = "wh-msg" + (isError ? " wh-msg-error" : "");
    el.textContent = text || "";
  }

  function setBusy(root, on) {
    busy = on;
    root.querySelectorAll(".wh-fbs-action").forEach(function (button) {
      button.disabled = on;
    });
    root.querySelectorAll(".wh-fbs-marketplace-tab").forEach(function (button) {
      button.disabled = on;
    });
    var download = root.querySelector("#whFbsDownload");
    if (download) {
      download.disabled = on || !labelTokens[activeMarketplace];
    }
  }

  function marketplacePanelHtml() {
    if (activeMarketplace === "ozon") {
      return (
        '<div class="wh-route-card">' +
        "<h3>Ozon FBS</h3>" +
        '<p class="wh-muted">Отправления awaiting_deliver. Порядок списка и этикеток берётся из листа assembly.</p>' +
        '<div class="wh-route-form">' +
        '<label>Первое отправление<input type="text" id="whFbsFirstPosting" placeholder="Необязательно" /></label>' +
        '<label>Последнее отправление<input type="text" id="whFbsLastPosting" placeholder="Необязательно" /></label>' +
        '<div class="wh-route-actions">' +
        '<button type="button" class="wh-btn wh-fbs-action" id="whFbsRefresh">Обновить список</button>' +
        '<button type="button" class="wh-btn wh-btn-primary wh-fbs-action" id="whFbsGenerate">Сформировать список и этикетки</button>' +
        '<button type="button" class="wh-btn wh-fbs-action" id="whFbsDownload" disabled>Скачать этикетки</button>' +
        "</div></div></div>"
      );
    }
    return (
      '<div class="wh-route-card">' +
      "<h3>Яндекс Маркет FBS</h3>" +
      '<p class="wh-muted">Берутся заказы PROCESSING с выбранным статусом: «Готовы к сборке» или «Готовы к отгрузке». ' +
      "Для готовых к сборке каждая товарная единица назначается в отдельную коробку; для готовых к отгрузке используются уже созданные этикетки. " +
      "Статус заказа не изменяется; список и этикетки сортируются по assembly.</p>" +
      '<div class="wh-route-form">' +
      '<label>Статус заказов<select id="whFbsYandexSubstatus">' +
      '<option value="STARTED">Готовы к сборке</option>' +
      '<option value="READY_TO_SHIP">Готовы к отгрузке</option>' +
      "</select></label>" +
      '<label>Количество товаров<input type="number" id="whFbsYandexItemLimit" min="1" step="1" placeholder="Все товары" /></label>' +
      '<div class="wh-route-actions">' +
      '<button type="button" class="wh-btn wh-fbs-action" id="whFbsRefresh">Обновить список</button>' +
      '<button type="button" class="wh-btn wh-btn-primary wh-fbs-action" id="whFbsGenerate">Сформировать список и этикетки</button>' +
      '<button type="button" class="wh-btn wh-fbs-action" id="whFbsDownload" disabled>Скачать этикетки</button>' +
      "</div></div></div>"
    );
  }

  function renderRows(root, rows) {
    rows = rows || [];
    var wrap = root.querySelector("#whFbsResult");
    if (!wrap) return;
    if (!rows.length) {
      wrap.innerHTML = '<p class="wh-msg">Подходящих заказов нет.</p>';
      return;
    }
    var body = rows
      .map(function (row, index) {
        return (
          "<tr><td>" +
          esc(row.seq != null ? row.seq : index + 1) +
          "</td><td><code>" +
          esc(row.sku) +
          "</code></td><td>" +
          esc(row.quantity) +
          "</td><td>" +
          esc(row.order_display || row.order_id || row.posting_number) +
          "</td></tr>"
        );
      })
      .join("");
    wrap.innerHTML =
      '<p class="wh-muted">Единиц в списке: ' +
      rows.length +
      "</p>" +
      '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
      "<th>№</th><th>Артикул</th><th>Кол-во</th><th>Заказ / отправление</th>" +
      "</tr></thead><tbody>" +
      body +
      "</tbody></table>";
  }

  function queryOrForm(root, asForm) {
    if (activeMarketplace === "yandex") {
      var limitEl = root.querySelector("#whFbsYandexItemLimit");
      var itemLimit = limitEl ? String(limitEl.value || "").trim() : "";
      var substatusEl = root.querySelector("#whFbsYandexSubstatus");
      var substatus = substatusEl ? String(substatusEl.value || "STARTED") : "STARTED";
      if (asForm) {
        var yandexForm = new FormData();
        if (itemLimit) yandexForm.append("item_limit", itemLimit);
        yandexForm.append("order_substatus", substatus);
        return yandexForm;
      }
      var yandexParams = new URLSearchParams();
      if (itemLimit) yandexParams.set("item_limit", itemLimit);
      yandexParams.set("order_substatus", substatus);
      return "?" + yandexParams.toString();
    }
    var first = String(root.querySelector("#whFbsFirstPosting").value || "").trim();
    var last = String(root.querySelector("#whFbsLastPosting").value || "").trim();
    if (asForm) {
      var form = new FormData();
      if (first) form.append("first_posting", first);
      if (last) form.append("last_posting", last);
      return form;
    }
    var params = new URLSearchParams();
    if (first) params.set("first_posting", first);
    if (last) params.set("last_posting", last);
    var query = params.toString();
    return query ? "?" + query : "";
  }

  function refreshList(root) {
    if (busy) return;
    setBusy(root, true);
    setMessage(root, "Получение заказов…", false);
    var url =
      activeMarketplace === "ozon"
        ? "/api/ozon/awaiting-shipment" + queryOrForm(root, false)
        : "/api/yandex/awaiting-assembly" + queryOrForm(root, false);
    shell()
      .fetchJson(url)
      .then(function (data) {
        labelTokens[activeMarketplace] = null;
        renderRows(root, data.list_rows || []);
        var warnings = data.warnings || [];
        if (activeMarketplace === "yandex") {
          warnings.unshift(
            "Показано товаров: " +
              (data.count || 0) +
              " из " +
              (data.available_count != null ? data.available_count : data.count || 0) +
              "."
          );
        }
        setMessage(root, warnings.length ? warnings.join("\n") : "Список обновлён.", false);
      })
      .catch(function (error) {
        setMessage(root, error.message || "Ошибка получения заказов", true);
      })
      .finally(function () {
        setBusy(root, false);
      });
  }

  function generate(root) {
    if (busy) return;
    setBusy(root, true);
    setMessage(
      root,
      activeMarketplace === "yandex"
        ? "Создание отдельных коробок и получение этикеток…"
        : "Формирование списка и этикеток…",
      false
    );
    var url =
      activeMarketplace === "ozon" ? "/api/fbs/ozon/generate" : "/api/fbs/yandex/generate";
    var options = { method: "POST", body: queryOrForm(root, true) };
    shell()
      .fetchJson(url, options)
      .then(function (data) {
        labelTokens[activeMarketplace] = data.labels_token || null;
        renderRows(root, data.list_rows || []);
        var notes = [];
        if (activeMarketplace === "yandex") {
          notes.push(
            "Взято товаров: " +
              (data.count || 0) +
              " из " +
              (data.available_count != null ? data.available_count : data.count || 0) +
              "."
          );
        }
        if (data.sheet_url) notes.push("Список создан: " + data.sheet_url);
        notes = notes.concat(data.warnings || []);
        if (!notes.length) notes.push("Список и этикетки сформированы.");
        setMessage(root, notes.join("\n"), false);
      })
      .catch(function (error) {
        labelTokens[activeMarketplace] = null;
        setMessage(root, error.message || "Ошибка формирования", true);
      })
      .finally(function () {
        setBusy(root, false);
      });
  }

  function download(root) {
    var token = labelTokens[activeMarketplace];
    if (!token || busy) return;
    setBusy(root, true);
    var url =
      "/api/fbs/" +
      activeMarketplace +
      "/labels?token=" +
      encodeURIComponent(token);
    fetch(url, { credentials: "include" })
      .then(function (response) {
        if (!response.ok) {
          return response.text().then(function (text) {
            var detail = text;
            try {
              var data = text ? JSON.parse(text) : null;
              if (data && data.detail) detail = data.detail;
            } catch (e) {
              // Оставляем исходный текст ответа.
            }
            throw new Error(detail || "Не удалось скачать этикетки");
          });
        }
        return response.blob().then(function (blob) {
          var disposition = response.headers.get("Content-Disposition") || "";
          var match = /filename="?([^";]+)"?/i.exec(disposition);
          var filename =
            match && match[1]
              ? match[1]
              : activeMarketplace === "yandex"
                ? "yandex_fbs_labels.pdf"
                : "ozon_fbs_labels.pdf";
          var objectUrl = URL.createObjectURL(blob);
          var link = document.createElement("a");
          link.href = objectUrl;
          link.download = filename;
          document.body.appendChild(link);
          link.click();
          link.remove();
          setTimeout(function () {
            URL.revokeObjectURL(objectUrl);
          }, 1000);
          labelTokens[activeMarketplace] = null;
        });
      })
      .catch(function (error) {
        setMessage(root, error.message || "Ошибка скачивания", true);
      })
      .finally(function () {
        setBusy(root, false);
      });
  }

  function bindPanel(root) {
    root.querySelectorAll(".wh-fbs-marketplace-tab").forEach(function (button) {
      button.addEventListener("click", function () {
        activeMarketplace = button.getAttribute("data-marketplace") || "yandex";
        renderBody(root);
      });
    });
    root.querySelector("#whFbsRefresh").addEventListener("click", function () {
      refreshList(root);
    });
    root.querySelector("#whFbsGenerate").addEventListener("click", function () {
      generate(root);
    });
    root.querySelector("#whFbsDownload").addEventListener("click", function () {
      download(root);
    });
  }

  function renderBody(root) {
    root.innerHTML =
      '<div class="wh-route-tabs">' +
      '<button type="button" class="wh-btn wh-route-tab wh-fbs-marketplace-tab' +
      (activeMarketplace === "yandex" ? " active" : "") +
      '" data-marketplace="yandex">Яндекс Маркет</button>' +
      '<button type="button" class="wh-btn wh-route-tab wh-fbs-marketplace-tab' +
      (activeMarketplace === "ozon" ? " active" : "") +
      '" data-marketplace="ozon">Ozon</button>' +
      "</div>" +
      marketplacePanelHtml() +
      '<p class="wh-msg" id="whFbsMessage"></p>' +
      '<div id="whFbsResult"></div>';
    bindPanel(root);
  }

  function render(tab, item) {
    shell().contentTitleEl.textContent = item.title;
    shell().contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    shell().contentPlaceholderEl.hidden = true;
    panelEl().hidden = false;
    var card = document.querySelector(".wh-content-card");
    if (card) card.classList.add("wh-content-card--wide");
    renderBody(panelEl());
  }

  global.WhFbs = { render: render };
})(window);
