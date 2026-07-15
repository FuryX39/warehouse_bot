(function () {
  function formatApiDetail(detail) {
    if (detail == null || detail === "") return "";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map(function (item) {
          if (typeof item === "string") return item;
          if (item && typeof item.msg === "string") return item.msg;
          try {
            return JSON.stringify(item);
          } catch (e) {
            return String(item);
          }
        })
        .filter(Boolean)
        .join("; ");
    }
    if (typeof detail === "object") {
      if (typeof detail.msg === "string") return detail.msg;
      if (typeof detail.message === "string") return detail.message;
      try {
        return JSON.stringify(detail);
      } catch (e2) {
        return String(detail);
      }
    }
    return String(detail);
  }

  async function api(path, options) {
    options = options || {};
    var res = await fetch(
      path,
      Object.assign({}, options, {
        headers: options.headers || {},
        credentials: "include",
      })
    );
    var text = await res.text();
    var data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (e) {
      data = { raw: text };
    }
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("Сессия истекла или требуется вход");
    }
    if (!res.ok) {
      var msg = formatApiDetail(data && data.detail) || text || res.statusText;
      throw new Error(msg || "Ошибка запроса");
    }
    return data;
  }

  async function downloadBlobUrl(url, defaultName) {
    var res = await fetch(url, { credentials: "include" });
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("Сессия истекла или требуется вход");
    }
    if (!res.ok) {
      var text = await res.text();
      var data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch (e) {
        data = null;
      }
      var msg = formatApiDetail(data && data.detail) || text || res.statusText;
      throw new Error(msg || "Не удалось скачать файл");
    }
    var blob = await res.blob();
    var dispo = res.headers.get("Content-Disposition") || "";
    var filename = defaultName || "download";
    var m = /filename="?([^";]+)"?/i.exec(dispo);
    if (m && m[1]) filename = m[1];
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () {
      URL.revokeObjectURL(a.href);
    }, 2000);
  }

  var ozonLabelsToken = null;
  var yandexLabelsToken = null;
  var busy = false;

  function setOzonBusy(on) {
    busy = on;
    var gen = document.getElementById("btnOzonGenerate");
    var dl = document.getElementById("btnOzonLabels");
    var ref = document.getElementById("btnOzonRefreshList");
    if (gen) gen.disabled = on;
    if (ref) ref.disabled = on;
    if (dl) dl.disabled = on || !ozonLabelsToken;
  }

  function setYandexBusy(on) {
    busy = on;
    var gen = document.getElementById("btnYandexGenerate");
    var dl = document.getElementById("btnYandexLabels");
    var ref = document.getElementById("btnYandexRefreshList");
    if (gen) gen.disabled = on;
    if (ref) ref.disabled = on;
    if (dl) dl.disabled = on || !yandexLabelsToken;
  }

  function showWarnings(warnings) {
    var box = document.getElementById("ozonWarnings");
    if (!box) return;
    if (!warnings || !warnings.length) {
      box.classList.add("hidden");
      box.textContent = "";
      return;
    }
    box.classList.remove("hidden");
    box.textContent = warnings.join("\n");
  }

  function renderListRows(rows) {
    var tbody = document.getElementById("ozonListBody");
    var result = document.getElementById("ozonResult");
    var summary = document.getElementById("ozonSummary");
    if (!tbody || !result) return;
    rows = rows || [];
    tbody.innerHTML = rows
      .map(function (r, i) {
        return (
          "<tr><td>" +
          (r.seq != null ? r.seq : i + 1) +
          "</td><td>" +
          escapeHtml(r.sku || "") +
          '</td><td class="num">' +
          (r.quantity != null ? r.quantity : "") +
          "</td><td>" +
          escapeHtml(r.posting_number || r.order_id || "") +
          "</td></tr>"
        );
      })
      .join("");
    if (summary) {
      summary.textContent =
        rows.length === 0
          ? "Нет строк в списке."
          : "Строк в списке: " + rows.length;
    }
    result.classList.toggle("hidden", false);
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function applySheetLink(sheetUrl, sheetTitle) {
    var block = document.getElementById("ozonSheetBlock");
    var link = document.getElementById("ozonSheetLink");
    if (!block || !link) return;
    if (sheetUrl) {
      block.classList.remove("hidden");
      link.href = sheetUrl;
      link.textContent = sheetTitle || sheetUrl;
    } else {
      block.classList.add("hidden");
      link.href = "#";
      link.textContent = "";
    }
  }

  function applyOzonGenerateResult(data) {
    ozonLabelsToken = data.labels_token || null;
    var dl = document.getElementById("btnOzonLabels");
    if (dl) dl.disabled = !ozonLabelsToken;
    renderListRows(data.list_rows || []);
    applySheetLink(data.sheet_url, data.sheet_title);
    showWarnings(data.warnings || []);
  }

  function showYandexWarnings(warnings) {
    var box = document.getElementById("yandexWarnings");
    if (!box) return;
    if (!warnings || !warnings.length) {
      box.classList.add("hidden");
      box.textContent = "";
      return;
    }
    box.classList.remove("hidden");
    box.textContent = warnings.join("\n");
  }

  function renderYandexListRows(rows) {
    var tbody = document.getElementById("yandexListBody");
    var result = document.getElementById("yandexResult");
    var summary = document.getElementById("yandexSummary");
    if (!tbody || !result) return;
    rows = rows || [];
    tbody.innerHTML = rows
      .map(function (r, i) {
        return (
          "<tr><td>" +
          (r.seq != null ? r.seq : i + 1) +
          "</td><td>" +
          escapeHtml(r.sku || "") +
          '</td><td class="num">' +
          (r.quantity != null ? r.quantity : "") +
          "</td><td>" +
          escapeHtml(r.order_display || r.order_id || r.posting_number || "") +
          "</td></tr>"
        );
      })
      .join("");
    if (summary) {
      summary.textContent =
        rows.length === 0
          ? "Нет заказов PROCESSING + STARTED."
          : "Товарных единиц в списке: " + rows.length;
    }
    result.classList.toggle("hidden", false);
  }

  function applyYandexSheetLink(sheetUrl, sheetTitle) {
    var block = document.getElementById("yandexSheetBlock");
    var link = document.getElementById("yandexSheetLink");
    if (!block || !link) return;
    if (sheetUrl) {
      block.classList.remove("hidden");
      link.href = sheetUrl;
      link.textContent = sheetTitle || sheetUrl;
    } else {
      block.classList.add("hidden");
      link.href = "#";
      link.textContent = "";
    }
  }

  function applyYandexGenerateResult(data) {
    yandexLabelsToken = data.labels_token || null;
    var dl = document.getElementById("btnYandexLabels");
    if (dl) dl.disabled = !yandexLabelsToken;
    renderYandexListRows(data.list_rows || []);
    applyYandexSheetLink(data.sheet_url, data.sheet_title);
    showYandexWarnings(data.warnings || []);
  }

  function renderFbsHelpLinks(fbs) {
    fbs = fbs || {};
    var tabName = document.getElementById("fbsAssemblyTabName");
    if (tabName) tabName.textContent = fbs.assembly_sheet_name || "assembly";

    function linkCell(id, url, label) {
      var cell = document.getElementById(id);
      if (!cell) return;
      if (url) {
        cell.innerHTML =
          '<a class="fbs-sheet-link" href="' +
          escapeHtml(url) +
          '" target="_blank" rel="noopener">' +
          escapeHtml(label || url) +
          "</a>";
      } else {
        cell.textContent = "не настроено";
      }
    }

    linkCell(
      "fbsBotTableLinkCell",
      fbs.assembly_sheet_url || fbs.default_stocks_sheet_url,
      fbs.assembly_sheet_url
        ? "Открыть assembly в bot_table"
        : fbs.default_stocks_sheet_url
          ? "Открыть bot_table"
          : ""
    );
    linkCell(
      "fbsSpreadsheetLinkCell",
      fbs.fbs_list_sheet_url,
      fbs.fbs_list_sheet_url ? "Открыть таблицу FBS Ozon" : ""
    );

    var status = document.getElementById("fbsAssemblyStatus");
    if (!status) return;
    var parts = [];
    if (!fbs.default_stocks_sheet_url) {
      parts.push("DEFAULT_STOCKS_SHEET_URL не задан — порядок ТСД недоступен.");
    }
    if (!fbs.fbs_list_sheet_url) {
      parts.push("FBS_LIST_SHEET_URL не задан — список в Google не будет создан.");
    }
    if (fbs.assembly_message) {
      parts.push(fbs.assembly_message);
    }
    status.textContent = parts.join(" ");
    status.classList.remove("ok", "warn");
    if (fbs.assembly_ok) {
      status.classList.add("ok");
    } else if (fbs.assembly_message) {
      status.classList.add("warn");
    }
  }

  async function loadMpConfig() {
    var cfg;
    try {
      cfg = await api("/api/config/marketplaces");
    } catch (e) {
      cfg = {};
    }
    var ozonApi = document.getElementById("ozonApiStatus");
    var ozonSheet = document.getElementById("ozonSheetStatus");
    if (ozonApi) {
      ozonApi.textContent = cfg.ozon && cfg.ozon.configured ? "настроен" : "не настроен";
    }
    if (ozonSheet) {
      var fbs = cfg.fbs || {};
      renderFbsHelpLinks(fbs);
      if (fbs.sheet_configured) {
        ozonSheet.textContent = "bot_table + FBS + service account настроены";
      } else {
        ozonSheet.textContent =
          "нужны DEFAULT_STOCKS_SHEET_URL, FBS_LIST_SHEET_URL и GOOGLE_SERVICE_ACCOUNT_FILE";
      }
      if (fbs.assembly_ok) {
        ozonSheet.textContent += "; assembly — OK";
      } else if (fbs.assembly_message) {
        ozonSheet.textContent += "; assembly — проверьте";
      }
    }
    var yandexApi = document.getElementById("yandexApiStatus");
    var yandexSheet = document.getElementById("yandexSheetStatus");
    if (yandexApi) {
      yandexApi.textContent =
        cfg.yandex_market && cfg.yandex_market.configured ? "настроен" : "не настроен";
    }
    if (yandexSheet) {
      yandexSheet.textContent = "assembly + FBS_LIST_SHEET_URL + service account";
    }
  }

  function ozonPostingRangeParams() {
    var firstEl = document.getElementById("ozonFirstPosting");
    var lastEl = document.getElementById("ozonLastPosting");
    return {
      first: firstEl ? String(firstEl.value || "").trim() : "",
      last: lastEl ? String(lastEl.value || "").trim() : "",
    };
  }

  function ozonPostingRangeFormData() {
    var p = ozonPostingRangeParams();
    var fd = new FormData();
    if (p.first) fd.append("first_posting", p.first);
    if (p.last) fd.append("last_posting", p.last);
    return fd;
  }

  function ozonPostingRangeQuery() {
    var p = ozonPostingRangeParams();
    var q = new URLSearchParams();
    if (p.first) q.set("first_posting", p.first);
    if (p.last) q.set("last_posting", p.last);
    var s = q.toString();
    return s ? "?" + s : "";
  }

  async function ozonGenerate() {
    if (busy) return;
    setOzonBusy(true);
    try {
      var data = await api("/api/fbs/ozon/generate", {
        method: "POST",
        body: ozonPostingRangeFormData(),
      });
      applyOzonGenerateResult(data);
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      setOzonBusy(false);
    }
  }

  async function ozonRefreshList() {
    if (busy) return;
    setOzonBusy(true);
    try {
      var data = await api("/api/ozon/awaiting-shipment" + ozonPostingRangeQuery());
      ozonLabelsToken = null;
      var dl = document.getElementById("btnOzonLabels");
      if (dl) dl.disabled = true;
      renderListRows(data.list_rows || []);
      applySheetLink(null, null);
      showWarnings(data.warnings || []);
      if (document.getElementById("ozonSummary")) {
        document.getElementById("ozonSummary").textContent =
          (data.list_rows || []).length === 0
            ? "Нет отправлений awaiting_deliver."
            : "Строк в списке: " + (data.list_rows || []).length;
      }
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      setOzonBusy(false);
    }
  }

  async function ozonDownloadLabels() {
    if (!ozonLabelsToken || busy) return;
    setOzonBusy(true);
    try {
      await downloadBlobUrl(
        "/api/fbs/ozon/labels?token=" + encodeURIComponent(ozonLabelsToken),
        "ozon_labels.pdf"
      );
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      setOzonBusy(false);
    }
  }

  async function yandexGenerate() {
    if (busy) return;
    setYandexBusy(true);
    try {
      var form = new FormData();
      var limit = yandexItemLimit();
      if (limit) form.append("item_limit", limit);
      var data = await api("/api/fbs/yandex/generate", { method: "POST", body: form });
      applyYandexGenerateResult(data);
      var summary = document.getElementById("yandexSummary");
      if (summary) {
        summary.textContent =
          "Взято товаров: " +
          (data.count || 0) +
          " из " +
          (data.available_count != null ? data.available_count : data.count || 0) +
          ".";
      }
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      setYandexBusy(false);
    }
  }

  async function yandexRefreshList() {
    if (busy) return;
    setYandexBusy(true);
    try {
      var limit = yandexItemLimit();
      var query = limit ? "?item_limit=" + encodeURIComponent(limit) : "";
      var data = await api("/api/yandex/awaiting-assembly" + query);
      yandexLabelsToken = null;
      var dl = document.getElementById("btnYandexLabels");
      if (dl) dl.disabled = true;
      renderYandexListRows(data.list_rows || []);
      var summary = document.getElementById("yandexSummary");
      if (summary) {
        summary.textContent =
          "Показано товаров: " +
          (data.count || 0) +
          " из " +
          (data.available_count != null ? data.available_count : data.count || 0) +
          ".";
      }
      applyYandexSheetLink(null, null);
      showYandexWarnings(data.warnings || []);
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      setYandexBusy(false);
    }
  }

  async function yandexDownloadLabels() {
    if (!yandexLabelsToken || busy) return;
    setYandexBusy(true);
    try {
      await downloadBlobUrl(
        "/api/fbs/yandex/labels?token=" + encodeURIComponent(yandexLabelsToken),
        "yandex_labels.pdf"
      );
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      setYandexBusy(false);
    }
  }

  function yandexItemLimit() {
    var input = document.getElementById("yandexItemLimit");
    return input ? String(input.value || "").trim() : "";
  }

  var shipScope = "all";
  var shipCodeRequested = false;

  async function apiForm(path, fields) {
    var body = new URLSearchParams();
    Object.keys(fields || {}).forEach(function (k) {
      body.set(k, String(fields[k]));
    });
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
  }

  var _SOURCE_NAMES = {
    ozon: "Ozon",
    wildberries: "Wildberries",
    yandex_market: "Яндекс Маркет",
  };

  function renderShipPreview(data) {
    var tbody = document.getElementById("shipPreviewBody");
    var summary = document.getElementById("shipPreviewSummary");
    if (!tbody) return;
    var by = (data && data.by_source) || {};
    var rows = Object.keys(by).sort();
    if (!rows.length) {
      tbody.innerHTML = "";
      if (summary) summary.textContent = "Нет данных по выбранному scope.";
      return;
    }
    var totalActive = 0;
    var totalReady = 0;
    tbody.innerHTML = rows
      .map(function (src) {
        var st = by[src] || {};
        if (st.error) {
          return (
            "<tr><td>" +
            escapeHtml(_SOURCE_NAMES[src] || src) +
            '</td><td colspan="2" class="muted">' +
            escapeHtml(st.error) +
            "</td></tr>"
          );
        }
        if (!st.configured) {
          return (
            "<tr><td>" +
            escapeHtml(_SOURCE_NAMES[src] || src) +
            '</td><td colspan="2" class="muted">API не настроен</td></tr>'
          );
        }
        totalActive += st.active_reserves || 0;
        totalReady += st.ready_to_ship || 0;
        return (
          "<tr><td>" +
          escapeHtml(_SOURCE_NAMES[src] || src) +
          '</td><td class="num">' +
          (st.active_reserves != null ? st.active_reserves : "—") +
          '</td><td class="num">' +
          (st.ready_to_ship != null ? st.ready_to_ship : "—") +
          "</td></tr>"
        );
      })
      .join("");
    if (summary) {
      summary.textContent =
        (data.scope_label || data.scope || "") +
        ": резервов added — " +
        totalActive +
        ", к отгрузке — " +
        totalReady;
    }
  }

  async function loadShipPreview() {
    try {
      var data = await api(
        "/api/fbs/ship/preview?scope=" + encodeURIComponent(shipScope)
      );
      renderShipPreview(data);
    } catch (e) {
      var summary = document.getElementById("shipPreviewSummary");
      if (summary) summary.textContent = e.message || String(e);
    }
  }

  function renderShipResult(data) {
    var box = document.getElementById("shipResult");
    var summary = document.getElementById("shipResultSummary");
    var tbody = document.getElementById("shipResultBody");
    var warn = document.getElementById("shipResultWarnings");
    if (!box || !tbody) return;
    box.classList.remove("hidden");
    if (summary) {
      summary.textContent =
        (data.scope_label || "") +
        ": отгружено резервов " +
        (data.total_reserves_shipped || 0) +
        ", единиц " +
        (data.total_reserved_units || 0) +
        ", SKU " +
        (data.total_skus || 0);
    }
    var lines = data.by_source || [];
    tbody.innerHTML = lines
      .map(function (row) {
        return (
          "<tr><td>" +
          escapeHtml(_SOURCE_NAMES[row.source] || row.source) +
          '</td><td class="num">' +
          row.reserves_shipped +
          '</td><td class="num">' +
          row.reserved_units +
          '</td><td class="num">' +
          row.affected_skus +
          "</td></tr>"
        );
      })
      .join("");
    var notes = []
      .concat(data.source_errors || [])
      .concat(data.sync_warnings || []);
    var mids = data.movement_ids || {};
    Object.keys(mids).forEach(function (src) {
      notes.push("Журнал " + (_SOURCE_NAMES[src] || src) + ": #" + mids[src]);
    });
    if (warn) {
      if (notes.length) {
        warn.classList.remove("hidden");
        warn.textContent = notes.join("\n");
      } else {
        warn.classList.add("hidden");
        warn.textContent = "";
      }
    }
  }

  async function shipRequestCode() {
    if (busy) return;
    busy = true;
    try {
      var data = await apiForm("/api/fbs/ship/request", { scope: shipScope });
      shipCodeRequested = true;
      var hint = document.getElementById("shipCodeHint");
      var confirmBtn = document.getElementById("btnShipConfirm");
      if (hint) {
        hint.classList.remove("hidden");
        hint.textContent =
          "Код для «" +
          (data.scope_label || shipScope) +
          "»: " +
          data.code +
          " (действует " +
          Math.round((data.expires_in || 300) / 60) +
          " мин). Введите его и нажмите «Выполнить отгрузку».";
      }
      if (confirmBtn) confirmBtn.disabled = false;
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      busy = false;
    }
  }

  async function shipConfirm() {
    if (busy) return;
    var codeEl = document.getElementById("shipConfirmCode");
    var code = codeEl ? String(codeEl.value || "").trim() : "";
    if (!code) {
      alert("Введите код подтверждения.");
      return;
    }
    if (!shipCodeRequested) {
      alert("Сначала запросите код.");
      return;
    }
    if (
      !window.confirm(
        "Выполнить отгрузку для «" +
          shipScope +
          "»? Списание остатков необратимо."
      )
    ) {
      return;
    }
    busy = true;
    var confirmBtn = document.getElementById("btnShipConfirm");
    if (confirmBtn) confirmBtn.disabled = true;
    try {
      var data = await apiForm("/api/fbs/ship/confirm", {
        scope: shipScope,
        code: code,
      });
      renderShipResult(data);
      shipCodeRequested = false;
      if (codeEl) codeEl.value = "";
      var hint = document.getElementById("shipCodeHint");
      if (hint) hint.classList.add("hidden");
      await loadShipPreview();
    } catch (e) {
      alert(e.message || String(e));
      if (confirmBtn) confirmBtn.disabled = false;
    } finally {
      busy = false;
    }
  }

  function initSectionTabs() {
    var tabs = document.querySelectorAll(".fbs-section-tab");
    var title = document.getElementById("fbsPageTitle");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var section = tab.getAttribute("data-section");
        if (!section) return;
        tabs.forEach(function (t) {
          t.classList.toggle("active", t === tab);
        });
        document.querySelectorAll(".fbs-section-panel").forEach(function (panel) {
          panel.classList.toggle("active", panel.id === "section-" + section);
        });
        if (title) {
          title.textContent =
            section === "ship" ? "FBS — отгрузка" : "FBS — список и этикетки";
        }
        if (section === "ship") loadShipPreview();
      });
    });
  }

  function initShipScopes() {
    document.querySelectorAll(".fbs-ship-scope").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var scope = btn.getAttribute("data-ship-scope");
        if (!scope) return;
        shipScope = scope;
        shipCodeRequested = false;
        document.querySelectorAll(".fbs-ship-scope").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });
        var hint = document.getElementById("shipCodeHint");
        if (hint) hint.classList.add("hidden");
        var confirmBtn = document.getElementById("btnShipConfirm");
        if (confirmBtn) confirmBtn.disabled = true;
        loadShipPreview();
      });
    });
  }

  function initTabs() {
    var tabs = document.querySelectorAll(".fbs-mp-tab");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var mp = tab.getAttribute("data-mp");
        if (!mp) return;
        tabs.forEach(function (t) {
          t.classList.toggle("active", t === tab);
        });
        document.querySelectorAll(".fbs-mp-panel").forEach(function (panel) {
          panel.classList.toggle("active", panel.id === "mp-" + mp);
        });
      });
    });
  }

  document.getElementById("btnOzonGenerate") &&
    document.getElementById("btnOzonGenerate").addEventListener("click", ozonGenerate);
  document.getElementById("btnOzonRefreshList") &&
    document.getElementById("btnOzonRefreshList").addEventListener("click", ozonRefreshList);
  document.getElementById("btnOzonLabels") &&
    document.getElementById("btnOzonLabels").addEventListener("click", ozonDownloadLabels);
  document.getElementById("btnYandexGenerate") &&
    document.getElementById("btnYandexGenerate").addEventListener("click", yandexGenerate);
  document.getElementById("btnYandexRefreshList") &&
    document.getElementById("btnYandexRefreshList").addEventListener("click", yandexRefreshList);
  document.getElementById("btnYandexLabels") &&
    document.getElementById("btnYandexLabels").addEventListener("click", yandexDownloadLabels);
  document.getElementById("btnLogout") &&
    document.getElementById("btnLogout").addEventListener("click", function () {
      api("/api/logout", { method: "POST" })
        .then(function () {
          window.location.href = "/login";
        })
        .catch(function () {
          window.location.href = "/login";
        });
    });

  document.getElementById("btnShipRequestCode") &&
    document.getElementById("btnShipRequestCode").addEventListener("click", shipRequestCode);
  document.getElementById("btnShipConfirm") &&
    document.getElementById("btnShipConfirm").addEventListener("click", shipConfirm);

  initSectionTabs();
  initShipScopes();
  initTabs();
  loadMpConfig();
})();
