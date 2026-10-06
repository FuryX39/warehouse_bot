/** Поставки FBO Яндекс Маркета — актуальные FBY-заявки и задания упаковки. */
(function (global) {
  var assignees = [];
  var jobsCache = [];
  var jobsPage = 1;
  var suppliesCache = [];
  var suppliesPage = 1;
  var preview = null;
  var selectedRequestId = 0;
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
    var el = root.querySelector("#whYmFboMessage");
    if (!el) return;
    el.className = "wh-msg" + (isError ? " wh-msg-error" : "");
    el.textContent = text || "";
  }

  function setBusy(root, on) {
    busy = on;
    root.querySelectorAll(".wh-ym-fbo-action").forEach(function (button) {
      button.disabled = on;
    });
  }

  function selectedPackerIds(root) {
    if (global.WhPackerAssignment) {
      return global.WhPackerAssignment.collect(root, "wh-fbs-packer-cb");
    }
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
    if (global.WhPackerAssignment) {
      return global.WhPackerAssignment.pickerHtml(assignees, {
        checkboxClass: "wh-fbs-packer-cb",
      });
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

  function fmtDate(value) {
    var text = String(value || "").trim();
    if (!text) return "—";
    var match = text.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
    if (match) return match[3] + "." + match[2] + "." + match[1] + " " + match[4] + ":" + match[5];
    return text;
  }

  function renderPreview(root, data) {
    var box = root.querySelector("#whYmFboPreview");
    if (!box) return;
    if (!data || !data.supply) {
      box.innerHTML = "";
      return;
    }
    var supply = data.supply || {};
    var items = (data.items || [])
      .map(function (row) {
        return esc(row.sku) + " × " + esc(row.planned_qty);
      })
      .join(", ");
    box.innerHTML =
      '<div class="wh-wb-fbo-preview">' +
      "<p><strong>" +
      esc(supply.display_id || supply.request_id) +
      "</strong> · " +
      esc(supply.status || "—") +
      "</p>" +
      "<p><strong>Склад:</strong> " +
      esc(supply.warehouse_name || "—") +
      "</p>" +
      "<p><strong>Транзит:</strong> " +
      esc(supply.transit_warehouse || "—") +
      " · " +
      esc(fmtDate(supply.transit_at)) +
      "</p>" +
      "<p><strong>Приёмка:</strong> " +
      esc(fmtDate(supply.accept_at)) +
      " · план " +
      esc(supply.planned_qty || 0) +
      " шт.</p>" +
      (items ? '<p class="wh-muted">' + items + "</p>" : "") +
      "</div>";
  }

  function renderSupplies(root, supplies) {
    var wrap = root.querySelector("#whYmFboSupplies");
    if (!wrap) return;
    if (supplies) {
      suppliesCache = supplies;
      suppliesPage = 1;
    }
    supplies = suppliesCache || [];
    if (!supplies.length) {
      wrap.innerHTML = '<p class="wh-muted">Актуальных дочерних FBY-поставок нет.</p>';
      return;
    }
    var p = global.WH_PAGER;
    var sliced = p ? p.slice(supplies, suppliesPage) : { items: supplies, state: { page: 1 } };
    suppliesPage = sliced.state.page;
    wrap.innerHTML =
      '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
      "<th>Поставка</th><th>Статус</th><th>Транзит / приёмка</th><th>Склад</th><th>План</th><th></th>" +
      "</tr></thead><tbody>" +
      sliced.items
        .map(function (row) {
          var job = row.job || {};
          var created = row.has_job
            ? '<span class="wh-muted">Задание #' + esc(job.id) + " · " + esc(jobStatusLabel(job.status)) + "</span>"
            : '<button type="button" class="wh-btn wh-btn-sm wh-btn-primary wh-ym-fbo-action wh-ym-fbo-create" data-id="' +
              esc(row.request_id) +
              '">Создать задание</button>';
          var selected = Number(selectedRequestId) === Number(row.request_id) ? " class=\"wh-row-selected\"" : "";
          return (
            "<tr" +
            selected +
            ' data-preview-id="' +
            esc(row.request_id) +
            '"><td><button type="button" class="wh-link-btn wh-ym-fbo-preview" data-id="' +
            esc(row.request_id) +
            '">' +
            esc(row.display_id || row.marketplace_request_id || row.request_id) +
            "</button></td><td>" +
            esc(row.status || "—") +
            "</td><td>" +
            esc(fmtDate(row.transit_at)) +
            "<br><span class=\"wh-muted\">" +
            esc(fmtDate(row.accept_at)) +
            "</span></td><td>" +
            esc(row.warehouse_name || "—") +
            (row.transit_warehouse
              ? '<br><span class="wh-muted">' + esc(row.transit_warehouse) + "</span>"
              : "") +
            "</td><td>" +
            esc(row.planned_qty || 0) +
            "</td><td>" +
            created +
            "</td></tr>"
          );
        })
        .join("") +
      "</tbody></table>" +
      (p ? p.html(sliced.state) : "");
    if (p) {
      p.bind(wrap, function (delta) {
        suppliesPage += delta;
        renderSupplies(root, null);
      });
    }
    wrap.querySelectorAll(".wh-ym-fbo-preview").forEach(function (btn) {
      btn.addEventListener("click", function () {
        loadPreview(root, parseInt(btn.getAttribute("data-id"), 10));
      });
    });
    wrap.querySelectorAll(".wh-ym-fbo-create").forEach(function (btn) {
      btn.addEventListener("click", function () {
        createJob(root, parseInt(btn.getAttribute("data-id"), 10));
      });
    });
  }

  function renderJobs(root, jobs) {
    var wrap = root.querySelector("#whYmFboJobs");
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
      "<th>№</th><th>Поставка</th><th>Склад</th><th>Товары</th><th>Упаковщики</th><th></th>" +
      "</tr></thead><tbody>" +
      sliced.items
        .map(function (job) {
          var cancel =
            job.status === "cancelled"
              ? ""
              : '<button type="button" class="wh-btn wh-btn-sm wh-ym-fbo-action wh-ym-fbo-job-cancel" data-id="' +
                esc(job.id) +
                '">Отменить</button>';
          var files = job.has_cargo_labels
            ? '<a class="wh-btn wh-btn-sm" href="/api/warehouse/marketplaces/yandex-fbo/jobs/' +
              esc(job.id) +
              '/labels.pdf" target="_blank" rel="noopener">Грузоместа</a>'
            : "";
          return (
            "<tr><td>#" +
            esc(job.id) +
            "</td><td>" +
            esc(job.display_id || job.supply_id) +
            " · " +
            esc(jobStatusLabel(job.status)) +
            "</td><td>" +
            esc(job.warehouse_name || "—") +
            "</td><td>" +
            esc(job.line_done) +
            " / " +
            esc(job.line_total) +
            "</td><td>" +
            esc((job.packer_names || []).join(", ") || "—") +
            '<br><button type="button" class="wh-btn wh-btn-sm wh-ym-fbo-packers-edit" data-id="' +
            esc(job.id) +
            '">Изменить</button>' +
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
    wrap.querySelectorAll(".wh-ym-fbo-job-cancel").forEach(function (btn) {
      btn.addEventListener("click", function () {
        cancelJob(root, parseInt(btn.getAttribute("data-id"), 10));
      });
    });
    wrap.querySelectorAll(".wh-ym-fbo-packers-edit").forEach(function (btn) {
      btn.addEventListener("click", function () {
        editJobPackers(root, parseInt(btn.getAttribute("data-id"), 10));
      });
    });
  }

  function editJobPackers(root, jobId) {
    var job = jobsCache.find(function (item) {
      return Number(item.id) === Number(jobId);
    });
    if (!job || !global.WhPackerAssignment) return;
    global.WhPackerAssignment.openEditor({
      title: "Упаковщики задания #" + job.id,
      assignees: assignees,
      selectedIds: job.packer_user_ids || [],
      onSave: function (ids) {
        return fetchJson("/api/warehouse/marketplaces/yandex-fbo/jobs/" + job.id + "/assignees", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ packer_user_ids: ids }),
        }).then(function () {
          setMessage(root, "Упаковщики задания #" + job.id + " обновлены.", false);
          loadJobs(root);
          loadSupplies(root);
        });
      },
    });
  }

  function loadJobs(root) {
    fetchJson("/api/warehouse/marketplaces/yandex-fbo/jobs")
      .then(function (data) {
        renderJobs(root, data.jobs || []);
      })
      .catch(function (err) {
        setMessage(root, err.message || "Не удалось загрузить задания", true);
      });
  }

  function loadSupplies(root) {
    fetchJson("/api/warehouse/marketplaces/yandex-fbo/supplies")
      .then(function (data) {
        renderSupplies(root, data.supplies || []);
      })
      .catch(function (err) {
        setMessage(root, err.message || "Не удалось загрузить поставки", true);
      });
  }

  function loadPreview(root, requestId) {
    if (busy || !requestId) return;
    selectedRequestId = requestId;
    renderSupplies(root, null);
    setBusy(root, true);
    setMessage(root, "Загрузка состава поставки…", false);
    fetchJson("/api/warehouse/marketplaces/yandex-fbo/supplies/" + requestId)
      .then(function (data) {
        preview = data;
        renderPreview(root, data);
        setMessage(root, "Поставка " + ((data.supply && data.supply.display_id) || requestId), false);
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

  function createJob(root, requestId) {
    if (busy) return;
    var packers = selectedPackerIds(root);
    if (!packers.length) {
      setMessage(root, "Назначьте хотя бы одного упаковщика.", true);
      return;
    }
    if (!requestId) {
      setMessage(root, "Выберите поставку.", true);
      return;
    }
    setBusy(root, true);
    setMessage(root, "Создание задания и разбор ярлыков грузомест…", false);
    fetchJson("/api/warehouse/marketplaces/yandex-fbo/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id: requestId, packer_user_ids: packers }),
    })
      .then(function (data) {
        var job = data.job || {};
        setMessage(root, "Задание #" + job.id + " создано.", false);
        loadJobs(root);
        loadSupplies(root);
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
    if (!window.confirm("Отменить задание #" + jobId + "? Упаковщик его больше не увидит.")) return;
    setBusy(root, true);
    fetchJson("/api/warehouse/marketplaces/yandex-fbo/jobs/" + jobId + "/cancel", { method: "POST" })
      .then(function () {
        setMessage(root, "Задание #" + jobId + " отменено.", false);
        loadJobs(root);
        loadSupplies(root);
      })
      .catch(function (err) {
        setMessage(root, err.message || "Не удалось отменить", true);
      })
      .finally(function () {
        setBusy(root, false);
      });
  }

  function bindPanel(root) {
    if (global.WhPackerAssignment) global.WhPackerAssignment.bind(root);
    var reload = root.querySelector("#whYmFboReload");
    if (reload) {
      reload.addEventListener("click", function () {
        loadSupplies(root);
        loadJobs(root);
      });
    }
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
    fetchJson("/api/warehouse/marketplaces/yandex-fbo/meta")
      .then(function (data) {
        assignees = data.assignees || [];
        preview = null;
        selectedRequestId = 0;
        root.innerHTML =
          '<div class="wh-route-card">' +
          "<h3>Яндекс Маркет FBY</h3>" +
          '<p class="wh-muted">Список актуальных дочерних поставок FBY. Задание создаётся по заявке API; ярлыки грузомест (CARGO_UNITS) сохраняются на сервере.</p>' +
          '<p class="wh-muted">Упаковщики</p>' +
          packerPickerHtml() +
          '<div class="wh-route-actions">' +
          '<button type="button" class="wh-btn wh-ym-fbo-action" id="whYmFboReload">Обновить поставки</button>' +
          "</div>" +
          '<div id="whYmFboPreview"></div>' +
          "</div>" +
          '<p class="wh-msg" id="whYmFboMessage"></p>' +
          '<h4 class="wh-crm-section-title">Актуальные поставки</h4>' +
          '<div id="whYmFboSupplies"></div>' +
          '<h4 class="wh-crm-section-title">Задания упаковки</h4>' +
          '<div id="whYmFboJobs"></div>';
        bindPanel(root);
        loadSupplies(root);
        loadJobs(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  global.WhYandexFbo = { render: render };
})(window);
