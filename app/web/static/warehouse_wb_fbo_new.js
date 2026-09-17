/** FBO WB new — менеджер: таблицы товаров и ШК коробов, скачивание заполненного xlsx. */
(function (global) {
  var assignees = [];
  var jobsCache = [];
  var jobsPage = 1;
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
    var el = root.querySelector("#whWbFboNewMessage");
    if (!el) return;
    el.className = "wh-msg" + (isError ? " wh-msg-error" : "");
    el.textContent = text || "";
  }

  function setBusy(root, on) {
    busy = on;
    root.querySelectorAll(".wh-wb-fbo-new-action").forEach(function (button) {
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

  function renderJobs(root, jobs) {
    var wrap = root.querySelector("#whWbFboNewJobs");
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
      "<th>№</th><th>Поставка</th><th>Штук</th><th>Короба</th><th>Упаковщики</th><th></th>" +
      "</tr></thead><tbody>" +
      sliced.items
        .map(function (job) {
          var cancel =
            job.status === "cancelled" || job.status === "done"
              ? ""
              : '<button type="button" class="wh-btn wh-btn-sm wh-wb-fbo-new-cancel" data-id="' +
                esc(job.id) +
                '">Отменить</button>';
          var xlsx =
            '<a class="wh-btn wh-btn-sm" href="/api/warehouse/marketplaces/wb-fbo-new/jobs/' +
            esc(job.id) +
            '/boxes.xlsx" download="wb_fbo_sheet_' +
            esc(job.id) +
            '_boxes.xlsx">Скачать таблицу грузомест</a>';
          return (
            "<tr><td>#" +
            esc(job.id) +
            "</td><td>" +
            esc(job.supply_id || "—") +
            " · " +
            esc(jobStatusLabel(job.status)) +
            "</td><td>" +
            esc(job.pcs_assigned) +
            " / " +
            esc(job.pcs_plan) +
            "</td><td>" +
            esc(job.box_assigned) +
            " / " +
            esc(job.box_total) +
            "</td><td>" +
            esc((job.packer_names || []).join(", ") || "—") +
            "</td><td>" +
            xlsx +
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
    wrap.querySelectorAll(".wh-wb-fbo-new-cancel").forEach(function (btn) {
      btn.addEventListener("click", function () {
        cancelJob(root, parseInt(btn.getAttribute("data-id"), 10));
      });
    });
  }

  function loadJobs(root) {
    fetchJson("/api/warehouse/marketplaces/wb-fbo-new/jobs")
      .then(function (data) {
        renderJobs(root, data.jobs || []);
      })
      .catch(function (err) {
        setMessage(root, err.message || "Не удалось загрузить задания", true);
      });
  }

  function createJob(root) {
    if (busy) return;
    var goods = root.querySelector("#whWbFboNewGoods").files[0];
    var boxes = root.querySelector("#whWbFboNewBoxes").files[0];
    if (!goods || !boxes) {
      setMessage(root, "Прикрепите обе таблицы: товары и ШК коробов.", true);
      return;
    }
    var packers = selectedPackerIds(root);
    if (!packers.length) {
      setMessage(root, "Назначьте хотя бы одного упаковщика.", true);
      return;
    }
    var fd = new FormData();
    fd.append("goods", goods);
    fd.append("boxes", boxes);
    fd.append("packer_user_ids", JSON.stringify(packers));
    fd.append("supply_id", root.querySelector("#whWbFboNewSupplyId").value.trim());
    fd.append("warehouse_name", root.querySelector("#whWbFboNewWarehouse").value.trim());
    fd.append("seller_name", root.querySelector("#whWbFboNewSeller").value.trim());
    fd.append("plan_date", root.querySelector("#whWbFboNewPlanDate").value.trim());
    setBusy(root, true);
    fetchJson("/api/warehouse/marketplaces/wb-fbo-new/jobs", { method: "POST", body: fd })
      .then(function (data) {
        var job = data.job || {};
        var warn = (job.warnings || []).join(" ");
        setMessage(
          root,
          "Задание #" +
            job.id +
            " создано: " +
            (job.pcs_plan || 0) +
            " шт., " +
            (job.box_total || 0) +
            " коробов." +
            (warn ? " " + warn : ""),
          false
        );
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
    setBusy(root, true);
    fetchJson("/api/warehouse/marketplaces/wb-fbo-new/jobs/" + jobId + "/cancel", { method: "POST" })
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
    root.querySelector("#whWbFboNewCreate").addEventListener("click", function () {
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
    fetchJson("/api/warehouse/marketplaces/wb-fbo-new/meta")
      .then(function (data) {
        assignees = data.assignees || [];
        root.innerHTML =
          '<div class="wh-route-card">' +
          "<h3>FBO WB new</h3>" +
          '<p class="wh-muted">Прикрепите таблицы из кабинета WB: товары и пустые ШК коробов. ' +
          "Упаковщики раскладывают товар по коробам. Готовый файл грузомест скачивается в том же формате кабинета — загружаете его в WB сами.</p>" +
          '<div class="wh-route-form">' +
          '<label>Товары.xlsx<input type="file" id="whWbFboNewGoods" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" /></label>' +
          '<label>Шк коробов.xlsx<input type="file" id="whWbFboNewBoxes" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" /></label>' +
          '<label>№ поставки (на этикетке)<input type="text" id="whWbFboNewSupplyId" placeholder="необязательно" /></label>' +
          '<label>Склад<input type="text" id="whWbFboNewWarehouse" /></label>' +
          '<label>Продавец<input type="text" id="whWbFboNewSeller" /></label>' +
          '<label>Плановая дата<input type="text" id="whWbFboNewPlanDate" placeholder="ДД.ММ.ГГГГ" /></label>' +
          "</div>" +
          '<p class="wh-muted">Упаковщики</p>' +
          packerPickerHtml() +
          '<div class="wh-route-actions">' +
          '<button type="button" class="wh-btn wh-btn-primary wh-wb-fbo-new-action" id="whWbFboNewCreate">Создать задание</button>' +
          "</div>" +
          "</div>" +
          '<p class="wh-msg" id="whWbFboNewMessage"></p>' +
          '<h4 class="wh-crm-section-title">Задания</h4>' +
          '<div id="whWbFboNewJobs"></div>';
        bindPanel(root);
        loadJobs(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  global.WhWbFboNew = { render: render };
})(window);
