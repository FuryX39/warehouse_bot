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

  var labelsToken = null;
  var busy = false;

  function setBusy(on) {
    busy = on;
    var gen = document.getElementById("btnOzonGenerate");
    var dl = document.getElementById("btnOzonLabels");
    var ref = document.getElementById("btnOzonRefreshList");
    if (gen) gen.disabled = on;
    if (ref) ref.disabled = on;
    if (dl) dl.disabled = on || !labelsToken;
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
          escapeHtml(r.posting_number || "") +
          "</td></tr>"
        );
      })
      .join("");
    if (summary) {
      summary.textContent =
        rows.length === 0
          ? "Нет отправлений awaiting_deliver."
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

  function applyGenerateResult(data) {
    labelsToken = data.labels_token || null;
    var dl = document.getElementById("btnOzonLabels");
    if (dl) dl.disabled = !labelsToken;
    renderListRows(data.list_rows || []);
    applySheetLink(data.sheet_url, data.sheet_title);
    showWarnings(data.warnings || []);
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
      ozonSheet.textContent = "FBS_LIST_SHEET_URL + service account";
    }
  }

  async function ozonGenerate() {
    if (busy) return;
    setBusy(true);
    try {
      var data = await api("/api/fbs/ozon/generate", { method: "POST" });
      applyGenerateResult(data);
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function ozonRefreshList() {
    if (busy) return;
    setBusy(true);
    try {
      var data = await api("/api/ozon/awaiting-shipment");
      labelsToken = null;
      var dl = document.getElementById("btnOzonLabels");
      if (dl) dl.disabled = true;
      renderListRows(data.list_rows || []);
      applySheetLink(null, null);
      showWarnings([]);
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function ozonDownloadLabels() {
    if (!labelsToken || busy) return;
    setBusy(true);
    try {
      await downloadBlobUrl(
        "/api/fbs/ozon/labels?token=" + encodeURIComponent(labelsToken),
        "ozon_labels.pdf"
      );
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      setBusy(false);
    }
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
