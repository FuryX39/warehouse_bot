/** Поставки FBO Wildberries — менеджер: ID поставки, QR, листы паллет, задание упаковщикам. */
(function (global) {
  var assignees = [];
  var jobsCache = [];
  var jobsPage = 1;
  var preview = null;
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

  function fetchJson(url, options) {
    return shell().fetchJson(url, options);
  }

  function setMessage(root, text, isError) {
    var el = root.querySelector("#whWbFboMessage");
    if (!el) return;
    el.className = "wh-msg" + (isError ? " wh-msg-error" : "");
    el.textContent = text || "";
  }

  function setBusy(root, on) {
    busy = on;
    root.querySelectorAll(".wh-wb-fbo-action").forEach(function (button) {
      button.disabled = on;
    });
  }

  function selectedPackerIds(root) {
    var ids = [];
    root.querySelectorAll(".wh-fbs-packer-cb:checked").forEach(function (cb) {
      var id = parseInt(cb.value, 10);
      if (id) ids.push(id);
    });
    return ids;
  }

  function packerPickerHtml() {
    if (!assignees.length) {
      return '<p class="wh-muted">Нет сотрудников для назначения.</p>';
    }
    return (
      '<div class="wh-fbs-packers">' +
      assignees
        .map(function (a) {
          return (
            '<label class="wh-fbs-packer-option">' +
            '<input type="checkbox" class="wh-fbs-packer-cb" value="' +
            esc(a.id) +
            '" /> ' +
            esc(a.display_name) +
            "</label>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  function jobStatusLabel(status) {
    if (status === "open") return "Открыто";
    if (status === "in_progress") return "В работе";
    if (status === "done") return "Готово";
    if (status === "cancelled") return "Отменено";
    return status || "—";
  }

  function renderPreview(root, data) {
    var box = root.querySelector("#whWbFboPreview");
    if (!box) return;
    if (!data) {
      box.innerHTML = "";
      return;
    }
    var skus = (data.skus || [])
      .map(function (row) {
        return esc(row.sku) + " × " + esc(row.boxes);
      })
      .join(", ");
    box.innerHTML =
      '<div class="wh-wb-fbo-preview">' +
      "<p><strong>Склад:</strong> " +
      esc(data.warehouse_name || "—") +
      "</p>" +
      "<p><strong>Дата:</strong> " +
      esc(data.plan_date || "—") +
      "</p>" +
      "<p><strong>Продавец:</strong> " +
      esc(data.seller_name || "—") +
      "</p>" +
      "<p><strong>Коробов:</strong> " +
      esc(data.box_count) +
      " · артикулов: " +
      esc(data.sku_count) +
      "</p>" +
      (skus ? '<p class="wh-muted">' + skus + "</p>" : "") +
      "</div>";
    var city = root.querySelector("#whWbFboCity");
    if (city && data.city && !String(city.value || "").trim()) {
      city.value = data.city;
    }
  }

  function renderJobs(root, jobs) {
    var wrap = root.querySelector("#whWbFboJobs");
    if (!wrap) return;
    if (jobs) {
      jobsCache = jobs;
      jobsPage = 1;
    }
    jobs = jobsCache || [];
    if (!jobs.length) {
      wrap.innerHTML = '<p class="wh-muted">Заданий пока нет.</p>';
      return;
    }
    var p = global.WH_PAGER;
    var sliced = p ? p.slice(jobs, jobsPage) : { items: jobs, state: { page: 1 } };
    jobsPage = sliced.state.page;
    wrap.innerHTML =
      '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
      "<th>№</th><th>Поставка</th><th>Город</th><th>Паллет</th><th>Короба</th><th>Упаковщики</th><th></th>" +
      "</tr></thead><tbody>" +
      sliced.items
        .map(function (job) {
          var cancel =
            job.status === "open" || job.status === "in_progress"
              ? '<button type="button" class="wh-btn wh-btn-sm wh-wb-fbo-job-cancel" data-id="' +
                esc(job.id) +
                '">Отменить</button>'
              : "";
          var files =
            '<a class="wh-btn wh-btn-sm" href="/api/warehouse/marketplaces/wb-fbo/jobs/' +
            esc(job.id) +
            '/supply-qr.pdf" target="_blank" rel="noopener">QR</a> ' +
            '<a class="wh-btn wh-btn-sm" href="/api/warehouse/marketplaces/wb-fbo/jobs/' +
            esc(job.id) +
            '/pallet-sheets.pdf" target="_blank" rel="noopener">Листы</a> ' +
            '<a class="wh-btn wh-btn-sm" href="/api/warehouse/marketplaces/wb-fbo/jobs/' +
            esc(job.id) +
            '/box-labels.pdf" target="_blank" rel="noopener">Короба</a>';
          return (
            "<tr><td>#" +
            esc(job.id) +
            "</td><td>" +
            esc(job.supply_id) +
            " · " +
            esc(jobStatusLabel(job.status)) +
            (job.supply_qr_code ? "<br><span class=\"wh-muted\">" + esc(job.supply_qr_code) + "</span>" : "") +
            "</td><td>" +
            esc(job.city || job.warehouse_name || "—") +
            "</td><td>" +
            esc(job.pallet_count) +
            "</td><td>" +
            esc(job.line_done) +
            " / " +
            esc(job.line_total) +
            "</td><td>" +
            esc((job.packer_names || []).join(", ") || "—") +
            "</td><td>" +
            files +
            " " +
            cancel +
            "</td></tr>"
          );
        })
        .join("") +
      "</tbody></table>" +
      (p ? p.html(sliced.state) : "");
    if (p) {
      p.bind(wrap, function (delta) {
        jobsPage += delta;
        renderJobs(root, null);
      });
    }
    wrap.querySelectorAll(".wh-wb-fbo-job-cancel").forEach(function (btn) {
      btn.addEventListener("click", function () {
        cancelJob(root, parseInt(btn.getAttribute("data-id"), 10));
      });
    });
  }

  function loadJobs(root) {
    fetchJson("/api/warehouse/marketplaces/wb-fbo/jobs")
      .then(function (data) {
        renderJobs(root, data.jobs || []);
      })
      .catch(function (err) {
        setMessage(root, err.message || "Не удалось загрузить задания", true);
      });
  }

  function loadPreview(root) {
    if (busy) return;
    var supply = String((root.querySelector("#whWbFboSupplyId") || {}).value || "").trim();
    if (!supply) {
      setMessage(root, "Укажите ID поставки FBW.", true);
      return;
    }
    setBusy(root, true);
    setMessage(root, "Загрузка поставки из WB…", false);
    fetchJson("/api/warehouse/marketplaces/wb-fbo/preview?supply_id=" + encodeURIComponent(supply))
      .then(function (data) {
        preview = data;
        renderPreview(root, data);
        setMessage(root, "Поставка " + data.supply_id + ": " + data.box_count + " коробов.", false);
      })
      .catch(function (err) {
        preview = null;
        renderPreview(root, null);
        setMessage(root, err.message || "Не удалось загрузить поставку", true);
      })
      .finally(function () {
        setBusy(root, false);
      });
  }

  function createJob(root) {
    if (busy) return;
    var packers = selectedPackerIds(root);
    if (!packers.length) {
      setMessage(root, "Назначьте хотя бы одного упаковщика.", true);
      return;
    }
    var supply = String((root.querySelector("#whWbFboSupplyId") || {}).value || "").trim();
    if (!supply) {
      setMessage(root, "Укажите ID поставки FBW.", true);
      return;
    }
    var fileEl = root.querySelector("#whWbFboQr");
    var file = fileEl && fileEl.files && fileEl.files[0];
    if (!file) {
      setMessage(root, "Прикрепите PDF с QR поставки из кабинета WB.", true);
      return;
    }
    var fd = new FormData();
    fd.append("supply_id", supply);
    fd.append("pallet_count", String((root.querySelector("#whWbFboPallets") || {}).value || "1"));
    fd.append("city", String((root.querySelector("#whWbFboCity") || {}).value || "").trim());
    fd.append("packer_user_ids", JSON.stringify(packers));
    fd.append("qr", file);
    setBusy(root, true);
    setMessage(root, "Создание задания, листов и этикеток коробов…", false);
    fetchJson("/api/warehouse/marketplaces/wb-fbo/jobs", { method: "POST", body: fd })
      .then(function (data) {
        var job = data.job || {};
        var note = "Задание #" + job.id + " создано.";
        if ((job.warnings || []).length) {
          note += " " + job.warnings.join(" ");
        }
        setMessage(root, note, false);
        if (fileEl) fileEl.value = "";
        loadJobs(root);
      })
      .catch(function (err) {
        setMessage(root, err.message || "Не удалось создать задание", true);
      })
      .finally(function () {
        setBusy(root, false);
      });
  }

  function cancelJob(root, jobId) {
    if (!jobId || busy) return;
    if (!window.confirm("Отменить задание #" + jobId + "?")) return;
    setBusy(root, true);
    fetchJson("/api/warehouse/marketplaces/wb-fbo/jobs/" + jobId + "/cancel", { method: "POST" })
      .then(function () {
        setMessage(root, "Задание #" + jobId + " отменено.", false);
        loadJobs(root);
      })
      .catch(function (err) {
        setMessage(root, err.message || "Не удалось отменить", true);
      })
      .finally(function () {
        setBusy(root, false);
      });
  }

  function bindPanel(root) {
    root.querySelector("#whWbFboPreviewBtn").addEventListener("click", function () {
      loadPreview(root);
    });
    root.querySelector("#whWbFboCreate").addEventListener("click", function () {
      createJob(root);
    });
  }

  function render(tab, item) {
    shell().contentTitleEl.textContent = item.title;
    shell().contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    shell().contentPlaceholderEl.hidden = true;
    panelEl().hidden = false;
    var card = document.querySelector(".wh-content-card");
    if (card) card.classList.add("wh-content-card--wide");
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    fetchJson("/api/warehouse/marketplaces/wb-fbo/meta")
      .then(function (data) {
        assignees = data.assignees || [];
        preview = null;
        root.innerHTML =
          '<div class="wh-route-card">' +
          "<h3>Wildberries FBO</h3>" +
          '<p class="wh-muted">Короба заводятся в кабинете WB. Здесь укажите ID поставки — подтянем состав и packageCode. ' +
          "QR поставки (стикер WB-GI) прикрепите файлом из кабинета: по API его нет. " +
          "Листы паллет заполняются как в «поставка лист.docx»: WILDBERRIES, номер, город, Палет N из M.</p>" +
          '<div class="wh-route-form">' +
          '<label>ID поставки FBW<input type="text" id="whWbFboSupplyId" placeholder="41357389" /></label>' +
          '<label>Паллет<input type="number" id="whWbFboPallets" min="1" step="1" value="1" /></label>' +
          '<label>Город на листах<input type="text" id="whWbFboCity" placeholder="подставится из склада" /></label>' +
          '<label>QR поставки (PDF)<input type="file" id="whWbFboQr" accept="application/pdf,.pdf" /></label>' +
          "</div>" +
          '<p class="wh-muted">Упаковщики</p>' +
          packerPickerHtml() +
          '<div class="wh-route-actions">' +
          '<button type="button" class="wh-btn wh-wb-fbo-action" id="whWbFboPreviewBtn">Подтянуть поставку</button>' +
          '<button type="button" class="wh-btn wh-btn-primary wh-wb-fbo-action" id="whWbFboCreate">Создать задание</button>' +
          "</div>" +
          '<div id="whWbFboPreview"></div>' +
          "</div>" +
          '<p class="wh-msg" id="whWbFboMessage"></p>' +
          '<h4 class="wh-crm-section-title">Задания упаковки</h4>' +
          '<div id="whWbFboJobs"></div>';
        bindPanel(root);
        loadJobs(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  global.WhWbFbo = { render: render };
})(window);
