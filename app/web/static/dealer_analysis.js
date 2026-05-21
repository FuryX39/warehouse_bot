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

  function formatTs(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000);
    return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  }

  function formatSize(bytes) {
    if (!bytes) return "—";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function kindLabel(kind) {
    if (kind === "source_a") return "Исходный A";
    if (kind === "source_b") return "Исходный B";
    if (kind === "report") return "Отчёт";
    return kind || "—";
  }

  function downloadUrl(fileId) {
    return "/api/dealer-analysis/files/" + fileId + "/download";
  }

  async function downloadBlobUrl(url, defaultName) {
    var res = await fetch(url, { credentials: "include" });
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("Сессия истекла");
    }
    if (!res.ok) {
      var text = await res.text();
      var data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch (e) {
        data = null;
      }
      throw new Error(formatApiDetail(data && data.detail) || text || "Не удалось скачать");
    }
    var blob = await res.blob();
    var dispo = res.headers.get("Content-Disposition") || "";
    var filename = defaultName || "download.xlsx";
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

  function renderFiles(files) {
    var body = document.getElementById("filesBody");
    body.innerHTML = "";
    if (!files.length) {
      body.innerHTML = '<tr><td colspan="6" class="muted">Нет сохранённых файлов</td></tr>';
      return;
    }
    files.forEach(function (f) {
      var tr = document.createElement("tr");
      var kindClass = "da-kind-" + (f.file_kind || "");
      tr.innerHTML =
        "<td>" +
        formatTs(f.uploaded_at_ts) +
        "</td>" +
        '<td class="' +
        kindClass +
        '">' +
        kindLabel(f.file_kind) +
        "</td>" +
        "<td>" +
        (f.period_label || "—") +
        "</td>" +
        "<td>" +
        (f.original_filename || "—") +
        "</td>" +
        "<td>" +
        formatSize(f.file_size) +
        '</td><td><button type="button" class="btn btn-dl" data-id="' +
        f.id +
        '">Скачать</button></td>';
      body.appendChild(tr);
    });
    body.querySelectorAll(".btn-dl").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-id");
        downloadBlobUrl(downloadUrl(id)).catch(function (err) {
          alert(err.message || String(err));
        });
      });
    });
  }

  function renderRuns(runs) {
    var body = document.getElementById("runsBody");
    body.innerHTML = "";
    if (!runs.length) {
      body.innerHTML = '<tr><td colspan="8" class="muted">Пока нет анализов</td></tr>';
      return;
    }
    runs.forEach(function (r) {
      var st = r.stats || {};
      var tr = document.createElement("tr");
      var dl =
        r.report_file_id != null
          ? '<button type="button" class="btn primary btn-dl-report" data-id="' +
            r.report_file_id +
            '">Отчёт</button>'
          : "—";
      tr.innerHTML =
        "<td>" +
        formatTs(r.created_at_ts) +
        "</td>" +
        "<td>" +
        (r.period_a_label || "?") +
        " → " +
        (r.period_b_label || "?") +
        "</td>" +
        "<td>" +
        (st.sku_count != null ? st.sku_count : "—") +
        "</td>" +
        "<td>" +
        (st.growth != null ? st.growth : "—") +
        "</td>" +
        "<td>" +
        (st.decline != null ? st.decline : "—") +
        "</td>" +
        "<td>" +
        (st.only_period_b != null ? st.only_period_b : "—") +
        "</td>" +
        "<td>" +
        (st.only_period_a != null ? st.only_period_a : "—") +
        "</td>" +
        "<td>" +
        dl +
        "</td>";
      body.appendChild(tr);
    });
    body.querySelectorAll(".btn-dl-report").forEach(function (btn) {
      btn.addEventListener("click", function () {
        downloadBlobUrl(downloadUrl(btn.getAttribute("data-id"))).catch(function (err) {
          alert(err.message || String(err));
        });
      });
    });
  }

  async function loadFiles() {
    var data = await api("/api/dealer-analysis/files");
    renderFiles(data.files || []);
  }

  async function loadRuns() {
    var data = await api("/api/dealer-analysis/runs");
    renderRuns(data.runs || []);
  }

  function showResult(stats, reportFileId) {
    var el = document.getElementById("analyzeResult");
    el.classList.remove("hidden");
    el.innerHTML =
      "<strong>Анализ готов.</strong>" +
      '<div class="da-stats">' +
      "<span>Артикулов: <b>" +
      (stats.sku_count || 0) +
      "</b></span>" +
      "<span>Рост: <b>" +
      (stats.growth || 0) +
      "</b></span>" +
      "<span>Падение: <b>" +
      (stats.decline || 0) +
      "</b></span>" +
      "<span>Новые: <b>" +
      (stats.only_period_b || 0) +
      "</b></span>" +
      "<span>Сняты: <b>" +
      (stats.only_period_a || 0) +
      "</b></span>" +
      "</div>" +
      '<button type="button" class="btn primary" id="btnDlReport">Скачать отчёт Excel</button>';
    document.getElementById("btnDlReport").addEventListener("click", function () {
      downloadBlobUrl(downloadUrl(reportFileId), "dealer_analysis.xlsx").catch(function (err) {
        alert(err.message || String(err));
      });
    });
  }

  document.getElementById("analyzeForm").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var status = document.getElementById("analyzeStatus");
    var btn = document.getElementById("btnAnalyze");
    var fileA = document.getElementById("fileA").files[0];
    var fileB = document.getElementById("fileB").files[0];
    if (!fileA || !fileB) {
      alert("Выберите оба Excel-файла");
      return;
    }
    var fd = new FormData();
    fd.append("period_a_label", document.getElementById("periodALabel").value.trim());
    fd.append("period_b_label", document.getElementById("periodBLabel").value.trim());
    fd.append("file_a", fileA);
    fd.append("file_b", fileB);
    btn.disabled = true;
    status.textContent = "Обработка…";
    try {
      var data = await api("/api/dealer-analysis/analyze", { method: "POST", body: fd });
      status.textContent = "Готово";
      showResult(data.stats || {}, data.report_file_id);
      await Promise.all([loadFiles(), loadRuns()]);
      if (data.report_file_id) {
        await downloadBlobUrl(downloadUrl(data.report_file_id), "dealer_analysis.xlsx");
      }
    } catch (err) {
      status.textContent = "";
      alert(err.message || String(err));
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("btnReloadFiles").addEventListener("click", function () {
    loadFiles().catch(function (err) {
      alert(err.message || String(err));
    });
  });
  document.getElementById("btnReloadRuns").addEventListener("click", function () {
    loadRuns().catch(function (err) {
      alert(err.message || String(err));
    });
  });
  document.getElementById("btnLogout").addEventListener("click", function () {
    api("/api/logout", { method: "POST" }).then(function () {
      window.location.href = "/login";
    });
  });

  loadFiles().catch(function () {});
  loadRuns().catch(function () {});
})();
