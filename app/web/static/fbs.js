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

  initTabs();
  loadMpConfig();
})();
