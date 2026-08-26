(function (global) {
  var activeMarketplace = "yandex";
  var labelTokens = { ozon: null, yandex: null };
  var busy = false;
  var assignees = [];
  var currentJobId = null;
  var hasMerged = false;

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

  function setMessage(root, text, isError) {
    var el = root.querySelector("#whFbsMessage");
    if (!el) return;
    el.className = "wh-msg" + (isError ? " wh-msg-error" : "");
    el.textContent = text || "";
  }

  function setBusy(root, on) {
    busy = on;
    root.querySelectorAll(".wh-fbs-action").forEach(function (button) {
      button.disabled = on;
    });
    root.querySelectorAll(".wh-fbs-marketplace-tab").forEach(function (button) {
      button.disabled = on;
    });
    var download = root.querySelector("#whFbsDownload");
    if (download) {
      download.disabled = on || (activeMarketplace === "ozon" ? !labelTokens.ozon : !hasMerged);
    }
  }

  function buildListChecked(root) {
    var el = root.querySelector("#whFbsBuildList");
    return !el || el.checked;
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

  function marketplacePanelHtml() {
    if (activeMarketplace === "ozon") {
      return (
        '<div class="wh-route-card">' +
        "<h3>Ozon FBS</h3>" +
        '<p class="wh-muted">Отправления awaiting_deliver. Порядок списка и этикеток берётся из листа assembly.</p>' +
        '<div class="wh-route-form">' +
        '<label>Первое отправление<input type="text" id="whFbsFirstPosting" placeholder="Необязательно" /></label>' +
        '<label>Последнее отправление<input type="text" id="whFbsLastPosting" placeholder="Необязательно" /></label>' +
        '<div class="wh-route-actions">' +
        '<button type="button" class="wh-btn wh-fbs-action" id="whFbsRefresh">Обновить список</button>' +
        '<button type="button" class="wh-btn wh-btn-primary wh-fbs-action" id="whFbsGenerate">Сформировать список и этикетки</button>' +
        '<button type="button" class="wh-btn wh-fbs-action" id="whFbsDownload" disabled>Скачать этикетки</button>' +
        "</div></div></div>"
      );
    }
    return (
      '<div class="wh-route-card">' +
      "<h3>Яндекс Маркет FBS</h3>" +
      '<p class="wh-muted">Заказы PROCESSING. «Готовы к сборке» режут заказ на коробки в Яндексе (одна штука — одна коробка). ' +
      "Галочка «Сформировать список» дополнительно пишет Google-лист по assembly и общий PDF.</p>" +
      '<div class="wh-route-form">' +
      '<label>Статус заказов<select id="whFbsYandexSubstatus">' +
      '<option value="STARTED">Готовы к сборке</option>' +
      '<option value="READY_TO_SHIP">Готовы к отгрузке</option>' +
      "</select></label>" +
      '<label>Количество товаров<input type="number" id="whFbsYandexItemLimit" min="1" step="1" placeholder="Все товары" /></label>' +
      "</div>" +
      '<label class="wh-fbs-check"><input type="checkbox" id="whFbsBuildList" checked /> Сформировать список</label>' +
      '<p class="wh-muted">Упаковщики</p>' +
      packerPickerHtml() +
      '<div class="wh-route-actions">' +
      '<button type="button" class="wh-btn wh-fbs-action" id="whFbsRefresh">Обновить список</button>' +
      '<button type="button" class="wh-btn wh-btn-primary wh-fbs-action" id="whFbsGenerate">Создать задание</button>' +
      '<button type="button" class="wh-btn wh-fbs-action" id="whFbsDownload" disabled>Скачать общий PDF</button>' +
      "</div></div>"
    );
  }

  function jobStatusLabel(status) {
    if (status === "in_progress") return "В работе";
    if (status === "done") return "Выполнено";
    if (status === "cancelled") return "Отменено";
    return "Открыто";
  }

  function renderJobs(root, jobs) {
    var wrap = root.querySelector("#whFbsJobs");
    if (!wrap) return;
    jobs = jobs || [];
    if (!jobs.length) {
      wrap.innerHTML = '<p class="wh-muted">Заданий пока нет.</p>';
      return;
    }
    wrap.innerHTML =
      '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
      "<th>№</th><th>Статус</th><th>Заказы</th><th>Строки</th><th>Упаковщики</th><th>Список</th><th></th>" +
      "</tr></thead><tbody>" +
      jobs
        .map(function (job) {
          var sheet = job.sheet_url
            ? '<a href="' + esc(job.sheet_url) + '" target="_blank" rel="noopener">лист</a>'
            : job.build_list
              ? "—"
              : "без списка";
          var cancel =
            job.status === "open" || job.status === "in_progress"
              ? '<button type="button" class="wh-btn wh-btn-sm wh-fbs-job-cancel" data-id="' +
                esc(job.id) +
                '">Отменить</button>'
              : "";
          return (
            "<tr><td>#" +
            esc(job.id) +
            "</td><td>" +
            esc(jobStatusLabel(job.status)) +
            "</td><td>" +
            esc((job.order_substatus || "").toUpperCase()) +
            "</td><td>" +
            esc(job.line_done) +
            " / " +
            esc(job.line_total) +
            " (ост. " +
            esc(job.line_pending) +
            ")</td><td>" +
            esc((job.packer_names || []).join(", ") || "—") +
            "</td><td>" +
            sheet +
            "</td><td>" +
            cancel +
            "</td></tr>"
          );
        })
        .join("") +
      "</tbody></table>";
    wrap.querySelectorAll(".wh-fbs-job-cancel").forEach(function (btn) {
      btn.addEventListener("click", function () {
        cancelJob(root, parseInt(btn.getAttribute("data-id"), 10));
      });
    });
  }

  function loadJobs(root) {
    if (activeMarketplace !== "yandex") return;
    shell()
      .fetchJson("/api/warehouse/fbs-packing/jobs")
      .then(function (data) {
        renderJobs(root, data.jobs || []);
      })
      .catch(function () {
        renderJobs(root, []);
      });
  }

  function cancelJob(root, jobId) {
    if (!jobId || busy) return;
    setBusy(root, true);
    shell()
      .fetchJson("/api/warehouse/fbs-packing/jobs/" + jobId + "/cancel", { method: "POST" })
      .then(function () {
        if (currentJobId === jobId) {
          currentJobId = null;
          hasMerged = false;
        }
        setMessage(root, "Задание отменено.", false);
        loadJobs(root);
        setBusy(root, false);
      })
      .catch(function (error) {
        setMessage(root, error.message || "Не удалось отменить", true);
        setBusy(root, false);
      });
  }

  function renderRows(root, rows) {
    rows = rows || [];
    var wrap = root.querySelector("#whFbsResult");
    if (!wrap) return;
    if (!rows.length) {
      wrap.innerHTML = '<p class="wh-msg">Подходящих заказов нет.</p>';
      return;
    }
    var body = rows
      .map(function (row, index) {
        return (
          "<tr><td>" +
          esc(row.seq != null ? row.seq : index + 1) +
          "</td><td><code>" +
          esc(row.sku) +
          "</code></td><td>" +
          esc(row.quantity) +
          "</td><td>" +
          esc(row.order_display || row.order_id || row.posting_number) +
          "</td></tr>"
        );
      })
      .join("");
    wrap.innerHTML =
      '<p class="wh-muted">Единиц в списке: ' +
      rows.length +
      "</p>" +
      '<table class="wh-employees-table wh-crm-table"><thead><tr>' +
      "<th>№</th><th>Артикул</th><th>Кол-во</th><th>Заказ / отправление</th>" +
      "</tr></thead><tbody>" +
      body +
      "</tbody></table>";
  }

  function queryOrForm(root, asForm) {
    if (activeMarketplace === "yandex") {
      var limitEl = root.querySelector("#whFbsYandexItemLimit");
      var itemLimit = limitEl ? String(limitEl.value || "").trim() : "";
      var substatusEl = root.querySelector("#whFbsYandexSubstatus");
      var substatus = substatusEl ? String(substatusEl.value || "STARTED") : "STARTED";
      if (asForm) {
        var yandexForm = new FormData();
        if (itemLimit) yandexForm.append("item_limit", itemLimit);
        yandexForm.append("order_substatus", substatus);
        return yandexForm;
      }
      var yandexParams = new URLSearchParams();
      if (itemLimit) yandexParams.set("item_limit", itemLimit);
      yandexParams.set("order_substatus", substatus);
      yandexParams.set("build_list", buildListChecked(root) ? "1" : "0");
      return "?" + yandexParams.toString();
    }
    var first = String(root.querySelector("#whFbsFirstPosting").value || "").trim();
    var last = String(root.querySelector("#whFbsLastPosting").value || "").trim();
    if (asForm) {
      var form = new FormData();
      if (first) form.append("first_posting", first);
      if (last) form.append("last_posting", last);
      return form;
    }
    var params = new URLSearchParams();
    if (first) params.set("first_posting", first);
    if (last) params.set("last_posting", last);
    var query = params.toString();
    return query ? "?" + query : "";
  }

  function refreshList(root) {
    if (busy) return;
    setBusy(root, true);
    setMessage(root, "Получение заказов…", false);
    var url =
      activeMarketplace === "ozon"
        ? "/api/ozon/awaiting-shipment" + queryOrForm(root, false)
        : "/api/warehouse/fbs-packing/preview" + queryOrForm(root, false);
    shell()
      .fetchJson(url)
      .then(function (data) {
        if (activeMarketplace === "ozon") labelTokens.ozon = null;
        renderRows(root, data.list_rows || []);
        var warnings = data.warnings || [];
        if (activeMarketplace === "yandex") {
          warnings.unshift(
            "Показано товаров: " +
              (data.count || 0) +
              " из " +
              (data.available_count != null ? data.available_count : data.count || 0) +
              "."
          );
        }
        setMessage(root, warnings.length ? warnings.join("\n") : "Список обновлён.", false);
      })
      .catch(function (error) {
        setMessage(root, error.message || "Ошибка получения заказов", true);
      })
      .finally(function () {
        setBusy(root, false);
      });
  }

  function generate(root) {
    if (busy) return;
    if (activeMarketplace === "yandex") {
      createJob(root);
      return;
    }
    setBusy(root, true);
    setMessage(root, "Формирование списка и этикеток…", false);
    shell()
      .fetchJson("/api/fbs/ozon/generate", { method: "POST", body: queryOrForm(root, true) })
      .then(function (data) {
        labelTokens.ozon = data.labels_token || null;
        renderRows(root, data.list_rows || []);
        var notes = [];
        if (data.sheet_url) notes.push("Список создан: " + data.sheet_url);
        notes = notes.concat(data.warnings || []);
        if (!notes.length) notes.push("Список и этикетки сформированы.");
        setMessage(root, notes.join("\n"), false);
      })
      .catch(function (error) {
        labelTokens.ozon = null;
        setMessage(root, error.message || "Ошибка формирования", true);
      })
      .finally(function () {
        setBusy(root, false);
      });
  }

  function createJob(root) {
    var packers = selectedPackerIds(root);
    if (!packers.length) {
      setMessage(root, "Назначьте хотя бы одного упаковщика.", true);
      return;
    }
    setBusy(root, true);
    setMessage(root, "Создание задания и загрузка ярлыков…", false);
    var limitEl = root.querySelector("#whFbsYandexItemLimit");
    var itemLimit = limitEl ? String(limitEl.value || "").trim() : "";
    var substatusEl = root.querySelector("#whFbsYandexSubstatus");
    var body = {
      order_substatus: substatusEl ? String(substatusEl.value || "STARTED") : "STARTED",
      build_list: buildListChecked(root),
      packer_user_ids: packers,
    };
    if (itemLimit) body.item_limit = parseInt(itemLimit, 10);
    shell()
      .fetchJson("/api/warehouse/fbs-packing/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      .then(function (data) {
        var job = data.job || {};
        currentJobId = job.id || null;
        hasMerged = !!job.has_merged_labels;
        renderRows(root, job.lines || []);
        var notes = ["Задание #" + (job.id || "—") + ": строк " + (job.line_total || 0) + "."];
        if (job.sheet_url) notes.push("Список создан: " + job.sheet_url);
        notes = notes.concat(job.warnings || []);
        setMessage(root, notes.join("\n"), false);
        loadJobs(root);
      })
      .catch(function (error) {
        currentJobId = null;
        hasMerged = false;
        setMessage(root, error.message || "Ошибка создания задания", true);
      })
      .finally(function () {
        setBusy(root, false);
      });
  }

  function download(root) {
    if (busy) return;
    if (activeMarketplace === "yandex") {
      if (!currentJobId || !hasMerged) return;
      setBusy(root, true);
      fetch("/api/warehouse/fbs-packing/jobs/" + currentJobId + "/labels", {
        credentials: "include",
      })
        .then(function (response) {
          if (!response.ok) {
            return response.text().then(function (text) {
              throw new Error(text || "Не удалось скачать PDF");
            });
          }
          return response.blob();
        })
        .then(function (blob) {
          var objectUrl = URL.createObjectURL(blob);
          var link = document.createElement("a");
          link.href = objectUrl;
          link.download = "yandex_fbs_labels.pdf";
          document.body.appendChild(link);
          link.click();
          link.remove();
          setTimeout(function () {
            URL.revokeObjectURL(objectUrl);
          }, 1000);
        })
        .catch(function (error) {
          setMessage(root, error.message || "Ошибка скачивания", true);
        })
        .finally(function () {
          setBusy(root, false);
        });
      return;
    }
    var token = labelTokens.ozon;
    if (!token) return;
    setBusy(root, true);
    var url = "/api/fbs/ozon/labels?token=" + encodeURIComponent(token);
    fetch(url, { credentials: "include" })
      .then(function (response) {
        if (!response.ok) {
          return response.text().then(function (text) {
            var detail = text;
            try {
              var data = text ? JSON.parse(text) : null;
              if (data && data.detail) detail = data.detail;
            } catch (e) {
              // keep text
            }
            throw new Error(detail || "Не удалось скачать этикетки");
          });
        }
        return response.blob().then(function (blob) {
          var disposition = response.headers.get("Content-Disposition") || "";
          var match = /filename="?([^";]+)"?/i.exec(disposition);
          var filename = match && match[1] ? match[1] : "ozon_fbs_labels.pdf";
          var objectUrl = URL.createObjectURL(blob);
          var link = document.createElement("a");
          link.href = objectUrl;
          link.download = filename;
          document.body.appendChild(link);
          link.click();
          link.remove();
          setTimeout(function () {
            URL.revokeObjectURL(objectUrl);
          }, 1000);
          labelTokens.ozon = null;
        });
      })
      .catch(function (error) {
        setMessage(root, error.message || "Ошибка скачивания", true);
      })
      .finally(function () {
        setBusy(root, false);
      });
  }

  function bindPanel(root) {
    root.querySelectorAll(".wh-fbs-marketplace-tab").forEach(function (button) {
      button.addEventListener("click", function () {
        activeMarketplace = button.getAttribute("data-marketplace") || "yandex";
        renderBody(root);
      });
    });
    root.querySelector("#whFbsRefresh").addEventListener("click", function () {
      refreshList(root);
    });
    root.querySelector("#whFbsGenerate").addEventListener("click", function () {
      generate(root);
    });
    root.querySelector("#whFbsDownload").addEventListener("click", function () {
      download(root);
    });
  }

  function renderBody(root) {
    root.innerHTML =
      '<div class="wh-route-tabs">' +
      '<button type="button" class="wh-btn wh-route-tab wh-fbs-marketplace-tab' +
      (activeMarketplace === "yandex" ? " active" : "") +
      '" data-marketplace="yandex">Яндекс Маркет</button>' +
      '<button type="button" class="wh-btn wh-route-tab wh-fbs-marketplace-tab' +
      (activeMarketplace === "ozon" ? " active" : "") +
      '" data-marketplace="ozon">Ozon</button>' +
      "</div>" +
      marketplacePanelHtml() +
      '<p class="wh-msg" id="whFbsMessage"></p>' +
      '<div id="whFbsResult"></div>' +
      (activeMarketplace === "yandex"
        ? '<h4 class="wh-crm-section-title">Задания упаковки</h4><div id="whFbsJobs"></div>'
        : "");
    bindPanel(root);
    if (activeMarketplace === "yandex") loadJobs(root);
    setBusy(root, false);
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
    shell()
      .fetchJson("/api/warehouse/fbs-packing/meta")
      .then(function (data) {
        assignees = data.assignees || [];
        renderBody(root);
      })
      .catch(function () {
        assignees = [];
        renderBody(root);
      });
  }

  global.WhFbs = { render: render };
})(window);
