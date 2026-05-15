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
    var headers = Object.assign({}, options.headers || {});
    var res = await fetch(
      path,
      Object.assign({}, options, { headers: headers, credentials: "include" })
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
      throw new Error(msg || res.statusText || "Запрос не выполнен");
    }
    return data;
  }

  function tsToLocal(ts) {
    if (!ts || ts <= 0) return "—";
    return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
  }

  function applySearch(tableBodyId, query) {
    var q = (query || "").trim().toLowerCase();
    document.querySelectorAll("#" + tableBodyId + " tr").forEach(function (tr) {
      if (!q) {
        tr.classList.remove("hidden-row");
        return;
      }
      tr.classList.toggle("hidden-row", tr.textContent.toLowerCase().indexOf(q) === -1);
    });
  }

  var inventoryRows = [];
  var inventorySort = { col: "sku", dir: 1 };

  function compareInventoryRow(a, b, col) {
    var va = a[col];
    var vb = b[col];
    if (col === "stock" || col === "reserve" || col === "available") {
      va = Number(va) || 0;
      vb = Number(vb) || 0;
      if (va < vb) return -1;
      if (va > vb) return 1;
      return 0;
    }
    va = String(va || "").toLowerCase();
    vb = String(vb || "").toLowerCase();
    if (va < vb) return -1;
    if (va > vb) return 1;
    return 0;
  }

  function sortInventoryRowsCopy() {
    var col = inventorySort.col;
    var dir = inventorySort.dir;
    return inventoryRows.slice().sort(function (a, b) {
      return compareInventoryRow(a, b, col) * dir;
    });
  }

  function updateInventorySortThClasses() {
    var row = document.getElementById("inventoryHeadRow");
    if (!row) return;
    row.querySelectorAll(".th-sortable").forEach(function (th) {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (th.getAttribute("data-inv-col") === inventorySort.col) {
        th.classList.add(inventorySort.dir === 1 ? "sorted-asc" : "sorted-desc");
      }
    });
  }

  function renderInventoryTable() {
    var body = document.getElementById("inventoryBody");
    body.innerHTML = "";
    var items = sortInventoryRowsCopy();
    items.forEach(function (it) {
      var av = Number(it.available);
      var nm =
        it.name != null && String(it.name).trim() !== ""
          ? String(it.name)
          : "Товар отсутствует в номенклатуре";
      var imgUrl = String(it.image_url || "").trim();
      var imgTd;
      if (/^https?:\/\//i.test(imgUrl)) {
        imgTd =
          '<td class="img-col"><img class="thumb-inv" src="' +
          encodeAttr(imgUrl) +
          '" alt="" loading="lazy" referrerpolicy="no-referrer" /></td>';
      } else {
        imgTd = '<td class="img-col muted">—</td>';
      }
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" +
        escapeHtml(it.sku) +
        "</td>" +
        '<td class="name-col">' +
        escapeHtml(nm) +
        "</td>" +
        imgTd +
        '<td class="num">' +
        it.stock +
        "</td>" +
        '<td class="num">' +
        it.reserve +
        "</td>" +
        '<td class="num' +
        (av < 0 ? " neg" : "") +
        '">' +
        av +
        "</td>" +
        '<td><button type="button" class="btn btn-edit" data-sku="' +
        encodeAttr(it.sku) +
        '" data-stock="' +
        it.stock +
        '">Изменить</button></td>';
      body.appendChild(tr);
    });
    body.querySelectorAll(".btn-edit").forEach(function (b) {
      b.addEventListener("click", function () {
        openStockDialog(b.getAttribute("data-sku"), b.getAttribute("data-stock"));
      });
    });
    updateInventorySortThClasses();
    applySearch("inventoryBody", document.getElementById("globalSearch").value);
  }

  var titles = {
    inventory: "Остатки",
    nomenclature: "Номенклатура",
    orders: "Заказы",
    movements: "Перемещения",
    sync: "Синхронизация",
  };
  document.querySelectorAll(".nav-item").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var view = btn.getAttribute("data-view");
      document.querySelectorAll(".nav-item").forEach(function (b) {
        b.classList.remove("active");
      });
      btn.classList.add("active");
      document.querySelectorAll(".view").forEach(function (v) {
        v.classList.remove("active");
      });
      document.getElementById("view-" + view).classList.add("active");
      document.getElementById("pageTitle").textContent = titles[view] || view;
      if (view === "sync") refreshStatus();
      if (view === "nomenclature") loadNomenclature().catch(showErr);
      if (view === "movements") loadMovements(false).catch(showErr);
    });
  });

  document.getElementById("globalSearch").addEventListener("input", function (e) {
    var active = document.querySelector(".view.active");
    if (!active) return;
    if (active.id === "view-inventory") applySearch("inventoryBody", e.target.value);
    if (active.id === "view-nomenclature") applySearch("nomenclatureBody", e.target.value);
    if (active.id === "view-orders") applySearch("ordersBody", e.target.value);
    if (active.id === "view-movements") applySearch("movementsBody", e.target.value);
  });

  document.getElementById("btnLogout").addEventListener("click", function () {
    api("/api/logout", { method: "POST" })
      .then(function () {
        window.location.href = "/login";
      })
      .catch(function () {
        window.location.href = "/login";
      });
  });

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }
  function encodeAttr(s) {
    return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }

  async function loadInventory() {
    var meta = document.getElementById("inventoryMeta");
    var body = document.getElementById("inventoryBody");
    meta.textContent = "Загрузка…";
    body.innerHTML = "";
    var data = await api("/api/inventory");
    inventoryRows = data.items || [];
    meta.textContent = "Строк: " + inventoryRows.length;
    renderInventoryTable();
  }

  document.getElementById("btnReloadInventory").addEventListener("click", function () {
    loadInventory().catch(showErr);
  });

  document.getElementById("inventoryHeadRow").addEventListener("click", function (e) {
    var th = e.target.closest(".th-sortable");
    if (!th) return;
    var col = th.getAttribute("data-inv-col");
    if (!col) return;
    if (inventorySort.col === col) inventorySort.dir = -inventorySort.dir;
    else {
      inventorySort.col = col;
      inventorySort.dir = 1;
    }
    renderInventoryTable();
  });

  document.getElementById("btnImportSheet").addEventListener("click", function () {
    var meta = document.getElementById("inventoryMeta");
    var urlEl = document.getElementById("importSheetUrl");
    var url = urlEl ? String(urlEl.value || "").trim() : "";
    meta.textContent = "Импорт из таблицы…";
    api("/api/import_sheet", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body: new URLSearchParams({ url: url }).toString(),
    })
      .then(function (r) {
        var parts = [];
        if (r.message) parts.push(r.message);
        else {
          parts.push("Импорт: обновлено записей (SKU): " + (r.updated != null ? r.updated : "—"));
          if (r.sku_in_sheet != null) parts.push("в таблице SKU: " + r.sku_in_sheet);
        }
        if (r.warnings && r.warnings.length) {
          var wn = r.warnings.slice(0, 3).join("; ");
          if (r.warnings.length > 3) wn += "…";
          parts.push("Предупреждения: " + wn);
        }
        if (r.warnings_more && r.warnings_more > 0) {
          parts.push("ещё предупреждений: " + r.warnings_more);
        }
        meta.textContent = parts.join(" · ");
        return loadInventory();
      })
      .catch(showErr);
  });

  async function loadNomenclature() {
    var meta = document.getElementById("nomenclatureMeta");
    var body = document.getElementById("nomenclatureBody");
    meta.textContent = "Загрузка…";
    body.innerHTML = "";
    var data = await api("/api/nomenclature");
    var rows = data.rows || [];
    meta.textContent = "Строк: " + rows.length;
    rows.forEach(function (r) {
      var imgUrl = String(r.image_url || "").trim();
      var imgTd;
      if (/^https?:\/\//i.test(imgUrl)) {
        imgTd =
          '<td class="img-col"><img class="thumb-inv" src="' +
          encodeAttr(imgUrl) +
          '" alt="" loading="lazy" referrerpolicy="no-referrer" /></td>';
      } else {
        imgTd = '<td class="img-col muted">—</td>';
      }
      var link = String(r.image_url || "").trim();
      var linkCell;
      if (/^https?:\/\//i.test(link)) {
        var short = link.length > 56 ? link.slice(0, 53) + "…" : link;
        linkCell =
          '<td class="link-col"><a href="' +
          encodeAttr(link) +
          '" target="_blank" rel="noopener noreferrer">' +
          escapeHtml(short) +
          "</a></td>";
      } else {
        linkCell = '<td class="link-col muted">—</td>';
      }
      var tr = document.createElement("tr");
      tr.innerHTML =
        imgTd +
        "<td>" +
        escapeHtml(r.sku) +
        "</td>" +
        '<td class="name-col">' +
        escapeHtml(r.name || "") +
        "</td>" +
        linkCell;
      body.appendChild(tr);
    });
    applySearch("nomenclatureBody", document.getElementById("globalSearch").value);
  }

  document.getElementById("btnReloadNomenclature").addEventListener("click", function () {
    loadNomenclature().catch(showErr);
  });

  document.getElementById("btnImportNomSheet").addEventListener("click", function () {
    var meta = document.getElementById("nomenclatureMeta");
    var urlEl = document.getElementById("importNomSheetUrl");
    var url = urlEl ? String(urlEl.value || "").trim() : "";
    meta.textContent = "Импорт номенклатуры (лист «nomenclature»)…";
    api("/api/import_nomenclature_sheet", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body: new URLSearchParams({ url: url }).toString(),
    })
      .then(function (r) {
        var parts = [];
        if (r.message) parts.push(r.message);
        else {
          parts.push("Записано SKU: " + (r.updated != null ? r.updated : "—"));
          if (r.sku_in_sheet != null) parts.push("в листе: " + r.sku_in_sheet);
        }
        if (r.warnings && r.warnings.length) {
          var wn = r.warnings.slice(0, 3).join("; ");
          if (r.warnings.length > 3) wn += "…";
          parts.push("Предупреждения: " + wn);
        }
        if (r.warnings_more && r.warnings_more > 0) {
          parts.push("ещё предупреждений: " + r.warnings_more);
        }
        meta.textContent = parts.join(" · ");
        return loadNomenclature();
      })
      .catch(showErr);
  });

  var dlg = document.getElementById("dlgStock");
  var dlgSku = document.getElementById("dlgSku");
  var dlgQty = document.getElementById("dlgStockQty");
  function openStockDialog(sku, stock) {
    dlgSku.textContent = sku;
    dlgQty.value = stock;
    dlg.showModal();
  }
  document.getElementById("dlgCancel").addEventListener("click", function () {
    dlg.close();
  });
  document.getElementById("formStock").addEventListener("submit", function (e) {
    e.preventDefault();
    var sku = dlgSku.textContent;
    var stock = parseInt(dlgQty.value, 10);
    api("/api/stock/" + encodeURIComponent(sku), {
      method: "PUT",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body: new URLSearchParams({ stock: String(stock) }).toString(),
    })
      .then(function () {
        dlg.close();
        return loadInventory();
      })
      .catch(showErr);
  });

  function buildOrdersUrl(skipDates) {
    var params = new URLSearchParams();
    params.set("limit", "5000");
    if (!skipDates) {
      var f = document.getElementById("ordersFrom").value;
      var t = document.getElementById("ordersTo").value;
      if (f) params.set("from", f);
      if (t) params.set("to", t);
    }
    var src = document.getElementById("ordersSource").value;
    if (src) params.set("source", src);
    var sku = document.getElementById("ordersSku").value.trim();
    if (sku) params.set("sku", sku);
    var ord = document.getElementById("ordersOrder").value.trim();
    if (ord) params.set("order", ord);
    return "/api/orders?" + params.toString();
  }

  async function loadOrders(skipDates) {
    var meta = document.getElementById("ordersMeta");
    var body = document.getElementById("ordersBody");
    meta.textContent = "Загрузка…";
    body.innerHTML = "";
    var url = buildOrdersUrl(!!skipDates);
    var data = await api(url);
    var rows = data.rows || [];
    meta.textContent = "Строк: " + rows.length;
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" +
        escapeHtml(r.source) +
        "</td><td>" +
        escapeHtml(r.external_order_id) +
        "</td><td>" +
        escapeHtml(r.sku) +
        "</td><td class=\"num\">" +
        r.quantity +
        "</td><td>" +
        escapeHtml(r.state) +
        "</td><td>" +
        tsToLocal(r.first_seen_ts) +
        "</td><td>" +
        tsToLocal(r.last_seen_ts) +
        "</td>";
      body.appendChild(tr);
    });
    applySearch("ordersBody", document.getElementById("globalSearch").value);
  }
  document.getElementById("btnLoadOrders").addEventListener("click", function () {
    loadOrders(false).catch(showErr);
  });
  document.getElementById("btnOrdersAll").addEventListener("click", function () {
    loadOrders(true).catch(showErr);
  });

  var dlgMovement = document.getElementById("dlgMovement");
  var dlgMovementCreate = document.getElementById("dlgMovementCreate");
  var currentMovementId = null;

  function sourceLabel(src) {
    if (src === "telegram") return "Telegram";
    if (src === "web") return "Веб";
    return src || "—";
  }

  function buildMovementsUrl(skipDates) {
    var params = new URLSearchParams();
    params.set("limit", "100");
    if (!skipDates) {
      var f = document.getElementById("movementsFrom").value;
      var t = document.getElementById("movementsTo").value;
      if (f) params.set("from", f);
      if (t) params.set("to", t);
    }
    return "/api/movements?" + params.toString();
  }

  async function openMovementDetail(id) {
    var data = await api("/api/movements/" + encodeURIComponent(String(id)));
    currentMovementId = data.id;
    document.getElementById("dlgMovementTitle").textContent = data.title || "Перемещение #" + data.id;
    document.getElementById("dlgMovementEditTitle").value = data.title || "";
    document.getElementById("dlgMovementEditComment").value = data.comment || "";
    var meta = document.getElementById("dlgMovementMeta");
    meta.innerHTML = "";
    function addMeta(label, value) {
      var dt = document.createElement("dt");
      dt.textContent = label;
      var dd = document.createElement("dd");
      dd.textContent = value;
      meta.appendChild(dt);
      meta.appendChild(dd);
    }
    addMeta("Дата", tsToLocal(data.created_at_ts));
    addMeta("Тип", data.direction_label || data.direction);
    addMeta("Источник", sourceLabel(data.source));
    addMeta("SKU в документе", String(data.sku_count));
    addMeta("Единиц", String(data.total_quantity));
    if (data.sheet_url) {
      var dt = document.createElement("dt");
      dt.textContent = "Таблица";
      var dd = document.createElement("dd");
      var a = document.createElement("a");
      a.href = data.sheet_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = "Открыть Google Sheets";
      dd.appendChild(a);
      meta.appendChild(dt);
      meta.appendChild(dd);
    }
    var warnEl = document.getElementById("dlgMovementWarnings");
    if (data.warnings && data.warnings.length) {
      warnEl.classList.remove("hidden");
      warnEl.textContent = "Предупреждения:\n- " + data.warnings.join("\n- ");
    } else {
      warnEl.classList.add("hidden");
      warnEl.textContent = "";
    }
    var linesBody = document.getElementById("dlgMovementLines");
    linesBody.innerHTML = "";
    (data.lines || []).forEach(function (ln) {
      var tr = document.createElement("tr");
      var delta = Number(ln.delta);
      tr.innerHTML =
        "<td>" +
        escapeHtml(ln.sku) +
        "</td><td class=\"num\">" +
        ln.quantity +
        "</td><td class=\"num" +
        (delta < 0 ? " neg" : "") +
        "\">" +
        (delta > 0 ? "+" : "") +
        delta +
        "</td>";
      linesBody.appendChild(tr);
    });
    applySearch("dlgMovementLines", document.getElementById("globalSearch").value);
    dlgMovement.showModal();
  }

  async function loadMovements(skipDates) {
    var meta = document.getElementById("movementsMeta");
    var body = document.getElementById("movementsBody");
    meta.textContent = "Загрузка…";
    body.innerHTML = "";
    var data = await api(buildMovementsUrl(!!skipDates));
    var rows = data.rows || [];
    meta.textContent = "Документов: " + rows.length;
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      tr.className = "row-clickable";
      var badge =
        r.direction === "in"
          ? '<span class="badge badge-in">' + escapeHtml(r.direction_label) + "</span>"
          : '<span class="badge badge-out">' + escapeHtml(r.direction_label) + "</span>";
      tr.innerHTML =
        '<td class="num">' +
        r.id +
        '</td><td class="name-col">' +
        escapeHtml(r.title || "") +
        "</td><td>" +
        tsToLocal(r.created_at_ts) +
        "</td><td>" +
        badge +
        "</td><td>" +
        escapeHtml(sourceLabel(r.source)) +
        '</td><td class="num">' +
        r.sku_count +
        '</td><td class="num">' +
        r.total_quantity +
        '</td><td><button type="button" class="btn btn-open-movement" data-id="' +
        r.id +
        '">Открыть</button></td>';
      tr.addEventListener("click", function (e) {
        if (e.target.closest("button")) return;
        openMovementDetail(r.id).catch(showErr);
      });
      body.appendChild(tr);
    });
    body.querySelectorAll(".btn-open-movement").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        openMovementDetail(btn.getAttribute("data-id")).catch(showErr);
      });
    });
    applySearch("movementsBody", document.getElementById("globalSearch").value);
  }

  document.getElementById("btnLoadMovements").addEventListener("click", function () {
    loadMovements(false).catch(showErr);
  });
  document.getElementById("btnMovementsAll").addEventListener("click", function () {
    loadMovements(true).catch(showErr);
  });
  document.getElementById("dlgMovementClose").addEventListener("click", function () {
    dlgMovement.close();
  });
  document.getElementById("btnSaveMovementMeta").addEventListener("click", function () {
    if (!currentMovementId) return;
    var params = new URLSearchParams();
    params.set("update_title", "true");
    params.set("update_comment", "true");
    params.set("title", document.getElementById("dlgMovementEditTitle").value);
    params.set("comment", document.getElementById("dlgMovementEditComment").value);
    api("/api/movements/" + encodeURIComponent(String(currentMovementId)), {
      method: "PATCH",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body: params.toString(),
    })
      .then(function () {
        return openMovementDetail(currentMovementId);
      })
      .then(function () {
        return loadMovements(false);
      })
      .catch(showErr);
  });
  document.getElementById("btnOpenMovementCreate").addEventListener("click", function () {
    document.getElementById("createMovementDirection").value = "in";
    document.getElementById("createMovementSheetUrl").value = "";
    document.getElementById("createMovementTitle").value = "";
    document.getElementById("createMovementComment").value = "";
    dlgMovementCreate.showModal();
  });
  document.getElementById("dlgMovementCreateCancel").addEventListener("click", function () {
    dlgMovementCreate.close();
  });
  document.getElementById("formMovementCreate").addEventListener("submit", function (e) {
    e.preventDefault();
    var meta = document.getElementById("movementsMeta");
    meta.textContent = "Выполняется перемещение…";
    var params = new URLSearchParams();
    params.set("direction", document.getElementById("createMovementDirection").value);
    params.set("url", document.getElementById("createMovementSheetUrl").value);
    params.set("title", document.getElementById("createMovementTitle").value);
    params.set("comment", document.getElementById("createMovementComment").value);
    api("/api/movement", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body: params.toString(),
    })
      .then(function (r) {
        dlgMovementCreate.close();
        meta.textContent =
          "Готово: " + (r.title || "#" + r.movement_id) + ", SKU: " + r.sku_count;
        return loadMovements(false);
      })
      .catch(showErr);
  });

  async function refreshStatus() {
    var el = document.getElementById("syncStatus");
    el.textContent = "Загрузка…";
    try {
      el.textContent = JSON.stringify(await api("/api/status"), null, 2);
    } catch (e) {
      el.textContent = String(e);
    }
  }
  document.getElementById("btnRefreshStatus").addEventListener("click", function () {
    refreshStatus();
  });
  document.querySelectorAll("[data-sync]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var mode = btn.getAttribute("data-sync");
      var out = document.getElementById("syncResult");
      out.textContent = "Выполняется…";
      api("/api/sync", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
        body: new URLSearchParams({ mode: mode }).toString(),
      })
        .then(function (r) {
          out.textContent = JSON.stringify(r, null, 2);
          return refreshStatus();
        })
        .catch(function (e) {
          out.textContent = String(e);
        });
    });
  });

  function showErr(err) {
    console.error(err);
    alert(err.message || String(err));
  }

  loadInventory().catch(showErr);
})();
