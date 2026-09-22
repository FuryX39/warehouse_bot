(function (global) {
  var meta = { assignees: [], purchase_statuses: [] };
  var platform = "vseinstrumenti";

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

  function fetchJson(url, options) {
    return shell().fetchJson(url, options);
  }

  function panelEl() {
    return shell().contentPanelEl;
  }

  function statusRu(status) {
    return (
      {
        open: "Открыто",
        in_progress: "В работе",
        done: "Готово",
        cancelled: "Отменено",
      }[status] || status || ""
    );
  }

  function preparePanel(tab, item) {
    shell().contentTitleEl.textContent = item.title;
    shell().contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    shell().contentPlaceholderEl.hidden = true;
    panelEl().hidden = false;
    var card = document.querySelector(".wh-content-card");
    if (card) card.classList.add("wh-content-card--wide");
  }

  function packerHtml() {
    return (meta.assignees || [])
      .map(function (user) {
        return (
          '<label class="wh-fbs-packer-option"><input type="checkbox" class="wh-om-packer" value="' +
          esc(user.id) +
          '" /> ' +
          esc(user.display_name) +
          "</label>"
        );
      })
      .join("");
  }

  function statusOptions(selected) {
    var html = '<option value="">—</option>';
    (meta.purchase_statuses || []).forEach(function (item) {
      var name = item && item.name ? item.name : "";
      if (!name) return;
      html +=
        '<option value="' +
        esc(name) +
        '"' +
        (name === selected ? " selected" : "") +
        ">" +
        esc(name) +
        "</option>";
    });
    return html;
  }

  function renderJobs(jobs) {
    if (!jobs.length) return '<p class="wh-msg">Заданий пока нет.</p>';
    var rows = jobs
      .map(function (job) {
        return (
          "<tr><td>" +
          esc(job.id) +
          "</td><td>" +
          esc(job.order_number) +
          "</td><td>" +
          esc(job.transfer_number) +
          '</td><td><select class="wh-om-purchase" data-job="' +
          esc(job.id) +
          '" data-prev="' +
          esc(job.purchase_status || "") +
          '">' +
          statusOptions(job.purchase_status || "") +
          "</select></td><td>" +
          esc(statusRu(job.status)) +
          "</td><td>" +
          esc((job.line_done || 0) + "/" + (job.line_total || 0)) +
          "</td><td>" +
          esc((job.packer_names || []).join(", ")) +
          '</td><td><a href="/api/warehouse/other-platforms/jobs/' +
          esc(job.id) +
          '/marking.xlsx">КИЗ</a></td><td>' +
          (job.status === "cancelled" || job.status === "done"
            ? ""
            : '<button type="button" class="wh-btn wh-btn-sm" data-cancel="' + esc(job.id) + '">Отменить</button>') +
          "</td></tr>"
        );
      })
      .join("");
    return (
      '<table class="wh-table"><thead><tr><th>№</th><th>Заказ</th><th>Перемещение</th><th>Статус закупки</th><th>Статус</th><th>Строки</th><th>Упаковщики</th><th>Файл</th><th></th></tr></thead><tbody>' +
      rows +
      "</tbody></table>"
    );
  }

  function bind(root) {
    root.querySelector("#whOmCreate").addEventListener("click", function () {
      var msg = root.querySelector("#whOmMsg");
      var file = root.querySelector("#whOmFile").files[0];
      var transfer = root.querySelector("#whOmTransfer").value.trim();
      var purchaseStatus = root.querySelector("#whOmPurchase").value;
      var packers = [];
      root.querySelectorAll(".wh-om-packer:checked").forEach(function (box) {
        packers.push(box.value);
      });
      msg.className = "wh-msg";
      msg.textContent = "";
      if (!file) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите Excel заказа";
        return;
      }
      if (!transfer) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Укажите номер перемещения";
        return;
      }
      if (!purchaseStatus) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Укажите статус закупки";
        return;
      }
      if (!packers.length) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите упаковщика";
        return;
      }
      var body = new FormData();
      body.append("file", file);
      body.append("transfer_number", transfer);
      body.append("purchase_status", purchaseStatus);
      body.append("packer_user_ids", packers.join(","));
      fetch("/api/warehouse/other-platforms/vseinstrumenti/jobs", {
        method: "POST",
        body: body,
        credentials: "include",
      })
        .then(function (response) {
          return response.text().then(function (text) {
            var data = text ? JSON.parse(text) : {};
            if (!response.ok) {
              var detail = data && data.detail ? data.detail : "Не удалось создать задание";
              throw new Error(typeof detail === "string" ? detail : "Не удалось создать задание");
            }
            return data;
          });
        })
        .then(function (data) {
          var warnings = (data.warnings || []).join(" ");
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent = "Задание создано." + (warnings ? " " + warnings : "");
          root.querySelector("#whOmFile").value = "";
          return fetchJson("/api/warehouse/other-platforms/vseinstrumenti/jobs");
        })
        .then(function (data) {
          root.querySelector("#whOmJobs").innerHTML = renderJobs(data.jobs || []);
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || "Ошибка";
        });
    });
    root.querySelector("#whOmJobs").addEventListener("change", function (event) {
      var select = event.target.closest(".wh-om-purchase");
      if (!select) return;
      var jobId = select.getAttribute("data-job");
      var previous = select.getAttribute("data-prev") || "";
      fetchJson("/api/warehouse/other-platforms/jobs/" + jobId + "/purchase-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ purchase_status: select.value }),
      })
        .then(function () {
          select.setAttribute("data-prev", select.value);
        })
        .catch(function (err) {
          select.value = previous;
          var msg = root.querySelector("#whOmMsg");
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || "Не удалось сохранить статус закупки";
        });
    });
    root.querySelector("#whOmJobs").addEventListener("click", function (event) {
      var button = event.target.closest("[data-cancel]");
      if (!button) return;
      fetchJson("/api/warehouse/other-platforms/jobs/" + button.getAttribute("data-cancel") + "/cancel", {
        method: "POST",
      }).then(function () {
        return fetchJson("/api/warehouse/other-platforms/vseinstrumenti/jobs");
      }).then(function (data) {
        root.querySelector("#whOmJobs").innerHTML = renderJobs(data.jobs || []);
      });
    });
  }

  function render(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка…</p>';
    fetchJson("/api/warehouse/other-platforms/meta")
      .then(function (data) {
        meta.assignees = data.assignees || [];
        meta.purchase_statuses = data.purchase_statuses || [];
        return fetchJson("/api/warehouse/other-platforms/" + platform + "/jobs");
      })
      .then(function (data) {
        root.innerHTML =
          '<div class="wh-crm-section-head"><h4 class="wh-crm-section-title">Другие площадки</h4></div>' +
          '<p><button type="button" class="wh-btn wh-btn-primary">ВсеИнструменты</button></p>' +
          '<p class="wh-muted">Загрузите подтверждение заказа и укажите номер перемещения. Название товара берётся из карточки, артикул и количество — из Excel.</p>' +
          '<section class="wh-crm-section"><div class="wh-form-row">' +
          '<div><label>Excel заказа</label><input type="file" id="whOmFile" accept=".xlsx,.xlsm" /></div>' +
          '<div><label>Номер перемещения</label><input type="text" id="whOmTransfer" /></div>' +
          '<div><label>Статус закупки</label><select id="whOmPurchase">' +
          statusOptions("") +
          "</select></div>" +
          "</div>" +
          '<div class="wh-fbs-packers">' +
          packerHtml() +
          "</div>" +
          '<p><button type="button" class="wh-btn wh-btn-primary" id="whOmCreate">Создать задание</button></p>' +
          '<p class="wh-msg" id="whOmMsg"></p></section>' +
          '<div id="whOmJobs">' +
          renderJobs(data.jobs || []) +
          "</div>";
        bind(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  global.WhOtherPlatforms = { render: render };
})(window);
