(function (global) {
  var meta = {
    marketplaces: [
      { id: "vseinstrumenti", title: "ВсеИнструменты", enabled: true },
      { id: "ozon", title: "Ozon", enabled: false },
      { id: "wildberries", title: "Wildberries", enabled: false },
      { id: "yandex_market", title: "Яндекс Маркет", enabled: false },
    ],
    vseinstrumenti: { supplier: 'ООО "ШАЙН СИСТЕМС"', statuses: ["ПСО", "КЗ"] },
  };

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

  function apiDetailText(detail) {
    if (detail == null || detail === "") return "";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map(function (item) {
          return item && item.msg ? item.msg : "";
        })
        .filter(Boolean)
        .join("; ");
    }
    return String(detail);
  }

  function downloadBlob(response, fallbackName) {
    return response.blob().then(function (blob) {
      var dispo = response.headers.get("Content-Disposition") || "";
      var filename = fallbackName || "route_sheets.pdf";
      var m = dispo.match(/filename="([^"]+)"/);
      if (m) filename = m[1];
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
    });
  }

  function postPdf(url, payload) {
    return fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) {
      if (r.ok) return downloadBlob(r, "route_sheets.pdf");
      return r.text().then(function (text) {
        var msg = text || "HTTP " + r.status;
        try {
          var json = JSON.parse(text);
          if (json && json.detail != null) msg = apiDetailText(json.detail);
        } catch (e) {}
        throw new Error(msg);
      });
    });
  }

  function marketplaceTabs(activeId) {
    return (
      '<div class="wh-route-tabs">' +
      meta.marketplaces
        .map(function (mp) {
          return (
            '<button type="button" class="wh-btn wh-route-tab' +
            (mp.id === activeId ? " active" : "") +
            '" data-mp="' +
            esc(mp.id) +
            '">' +
            esc(mp.title) +
            "</button>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  function statusOptions() {
    return (meta.vseinstrumenti.statuses || [])
      .map(function (s) {
        return '<option value="' + esc(s) + '"></option>';
      })
      .join("");
  }

  function renderVseinstrumenti() {
    return (
      '<div class="wh-route-card">' +
      '<p class="wh-muted">PDF маршрутного листа для быстрого печати. Если паллет несколько, PDF будет содержать отдельную страницу для каждой паллеты.</p>' +
      '<datalist id="whRouteStatusOptions">' +
      statusOptions() +
      "</datalist>" +
      '<form id="whRouteVsiForm" class="wh-route-form">' +
      '<label><span>Поставщик</span><input name="supplier" value="' +
      esc(meta.vseinstrumenti.supplier) +
      '" /></label>' +
      '<label><span>№ закупки</span><input name="purchase_number" autocomplete="off" /></label>' +
      '<label><span>Статус закупки</span><input name="purchase_status" list="whRouteStatusOptions" placeholder="ПСО или КЗ" autocomplete="off" /></label>' +
      '<label><span>Дата доставки</span><input name="delivery_date" type="date" /></label>' +
      '<label><span>Кол-во паллет</span><input name="pallet_count" type="number" min="1" step="1" required /></label>' +
      '<label><span>Паллет из</span><input value="сформируется автоматически: 1/N, 2/N..." disabled /></label>' +
      '<div class="wh-route-actions">' +
      '<button type="submit" class="wh-btn wh-btn-primary">Сформировать PDF</button>' +
      '<span id="whRouteMsg" class="wh-msg"></span>' +
      "</div>" +
      "</form>" +
      "</div>"
    );
  }

  function renderStub(mp) {
    return (
      '<div class="wh-route-card">' +
      '<p class="wh-placeholder">Маршрутные листы для «' +
      esc(mp.title) +
      "» пока не реализованы. Раздел подготовлен для будущей настройки.</p>" +
      "</div>"
    );
  }

  function bindTabs(root) {
    root.querySelectorAll(".wh-route-tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-mp") || "vseinstrumenti";
        drawMarketplace(root, id);
      });
    });
  }

  function drawMarketplace(root, activeId) {
    var active = meta.marketplaces.filter(function (m) {
      return m.id === activeId;
    })[0] || meta.marketplaces[0];
    root.innerHTML =
      marketplaceTabs(active.id) +
      '<div id="whRouteContent">' +
      (active.id === "vseinstrumenti" ? renderVseinstrumenti() : renderStub(active)) +
      "</div>";
    bindTabs(root);
    if (active.id === "vseinstrumenti") bindVseinstrumenti(root);
  }

  function bindVseinstrumenti(root) {
    var form = root.querySelector("#whRouteVsiForm");
    var msg = root.querySelector("#whRouteMsg");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      msg.className = "wh-msg";
      msg.textContent = "Формируем PDF...";
      var fd = new FormData(form);
      var payload = {
        supplier: fd.get("supplier"),
        purchase_number: fd.get("purchase_number"),
        purchase_status: fd.get("purchase_status"),
        delivery_date: fd.get("delivery_date"),
        pallet_count: fd.get("pallet_count"),
      };
      postPdf("/api/warehouse/marketplaces/route-sheets/vseinstrumenti.pdf", payload)
        .then(function () {
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = "PDF сформирован.";
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || String(err);
        });
    });
  }

  function loadMeta() {
    return shell()
      .fetchJson("/api/warehouse/marketplaces/route-sheets/meta")
      .then(function (data) {
        meta = data || meta;
        return meta;
      })
      .catch(function () {
        return meta;
      });
  }

  function render(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-placeholder">Загрузка...</p>';
    loadMeta().then(function () {
      drawMarketplace(root, "vseinstrumenti");
    });
  }

  global.WhRouteSheets = { render: render };
})(window);
