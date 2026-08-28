(function (global) {
  var activeMarketplace = "yandex";
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
      download.disabled = on || !hasMerged;
    }
  }

  function buildListChecked(root) {
    var el = root.querySelector("#whFbsBuildList");
    return !el || el.checked;
  }

  function requireCisChecked(root) {
    var el = root.querySelector("#whFbsRequireCis");
    return !!(el && el.checked);
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
        '<p class="wh-muted">Отправления awaiting_deliver. Укажите первое и последнее отправление ' +
        "(как в ЛК Ozon сверху вниз, включительно) — в задание попадёт всё между ними. Пусто — все. " +
        "Без Google-таблицы: задание такое же, как у WB и Яндекса.</p>" +
        '<div class="wh-route-form">' +
        '<label>Первое отправление<input type="text" id="whFbsFirstPosting" placeholder="Необязательно" /></label>' +
        '<label>Последнее отправление<input type="text" id="whFbsLastPosting" placeholder="Необязательно" /></label>' +
        "</div>" +
        '<label class="wh-fbs-check"><input type="checkbox" id="whFbsRequireCis" /> Обязательная маркировка ЧЗ</label>' +
        '<p class="wh-muted">Упаковщики</p>' +
        packerPickerHtml() +
        '<div class="wh-route-actions">' +
        '<button type="button" class="wh-btn wh-fbs-action" id="whFbsRefresh">Обновить список</button>' +
        '<button type="button" class="wh-btn wh-btn-primary wh-fbs-action" id="whFbsGenerate">Создать задание</button>' +
        '<button type="button" class="wh-btn wh-fbs-action" id="whFbsDownload" disabled>Скачать общий PDF</button>' +
        "</div></div>"
      );
    }
    if (activeMarketplace === "wildberries") {
      return (
        '<div class="wh-route-card">' +
        "<h3>Wildberries FBS</h3>" +
        '<p class="wh-muted">Каждый заказ — одна строка задания. «Готовы к сборке» создаёт поставку WB и скачивает стикеры. ' +
        "«Готовы к отгрузке» — выберите существующую поставку.</p>" +
        '<div class="wh-route-form">' +
        '<label>Статус<select id="whFbsSubstatus">' +
        '<option value="STARTED">Готовы к сборке</option>' +
        '<option value="READY_TO_SHIP">Готовы к отгрузке</option>' +
        "</select></label>" +
        '<label id="whFbsWbSupplyWrap" hidden>Поставка<select id="whFbsWbSupply"><option value="">— выберите —</option></select></label>' +
        '<label>Количество товаров<input type="number" id="whFbsItemLimit" min="1" step="1" placeholder="Все товары" /></label>' +
        "</div>" +
        '<label class="wh-fbs-check"><input type="checkbox" id="whFbsRequireCis" /> Обязательная маркировка ЧЗ</label>' +
        '<p class="wh-muted">Упаковщики</p>' +
        packerPickerHtml() +
        '<div class="wh-route-actions">' +
        '<button type="button" class="wh-btn wh-fbs-action" id="whFbsRefresh">Обновить список</button>' +
        '<button type="button" class="wh-btn wh-btn-primary wh-fbs-action" id="whFbsGenerate">Создать задание</button>' +
        '<button type="button" class="wh-btn wh-fbs-action" id="whFbsDownload" disabled>Скачать общий PDF</button>' +
        "</div></div>"
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
      '<label class="wh-fbs-check"><input type="checkbox" id="whFbsRequireCis" /> Обязательная маркировка ЧЗ</label>' +
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

  function jobMarketplaceLabel(job) {
    var mp = String((job && job.marketplace) || activeMarketplace || "").toLowerCase();
    if (mp === "wildberries") return "WB";
    if (mp === "ozon") return "Ozon";
    return "Яндекс";
  }

  function jobSubstatusLabel(job) {
    var st = String((job && job.order_substatus) || "").toUpperCase();
    if (st === "AWAITING_DELIVER") return "ожидает отгрузки";
    if (st === "STARTED") return "к сборке";
    if (st === "READY_TO_SHIP") return "к отгрузке";
    return st || "—";
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
      "<th>№</th><th>Статус</th><th>Заказы</th><th>Строки</th><th>Упаковщики</th><th>Список</th><th>ЧЗ</th><th></th>" +
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
          var marking =
            '<button type="button" class="wh-btn wh-btn-sm wh-fbs-job-marking" data-id="' +
            esc(job.id) +
            '">Скачать ЧЗ</button>';
          return (
            "<tr><td>#" +
            esc(job.id) +
            "</td><td>" +
            esc(jobStatusLabel(job.status)) +
            "</td><td>" +
            esc(jobMarketplaceLabel(job)) +
            " · " +
            esc(jobSubstatusLabel(job)) +
            (job.supply_id ? " · " + esc(job.supply_id) : "") +
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
            (job.require_cis ? "да" : "—") +
            "</td><td>" +
            marking +
            " " +
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
    wrap.querySelectorAll(".wh-fbs-job-marking").forEach(function (btn) {
      btn.addEventListener("click", function () {
        downloadMarking(root, parseInt(btn.getAttribute("data-id"), 10));
      });
    });
  }

  function packingMarketplaceParam() {
    if (activeMarketplace === "wildberries") return "wildberries";
    if (activeMarketplace === "ozon") return "ozon";
    return "yandex";
  }

  function isPackingJobsMarketplace() {
    return (
      activeMarketplace === "yandex" ||
      activeMarketplace === "wildberries" ||
      activeMarketplace === "ozon"
    );
  }

  function wbSubstatus(root) {
    var el = root.querySelector("#whFbsSubstatus");
    return el ? String(el.value || "STARTED") : "STARTED";
  }

  function toggleWbSupply(root) {
    if (activeMarketplace !== "wildberries") return;
    var wrap = root.querySelector("#whFbsWbSupplyWrap");
    if (!wrap) return;
    var show = wbSubstatus(root) === "READY_TO_SHIP";
    wrap.hidden = !show;
    if (show) loadWbSupplies(root);
  }

  function loadWbSupplies(root) {
    var select = root.querySelector("#whFbsWbSupply");
    if (!select) return;
    shell()
      .fetchJson("/api/warehouse/fbs-packing/wb/supplies")
      .then(function (data) {
        var prev = String(select.value || "");
        select.innerHTML = '<option value="">— выберите поставку —</option>';
        (data.supplies || []).forEach(function (item) {
          var opt = document.createElement("option");
          opt.value = String(item.id || "");
          opt.textContent = (item.name || item.id || "") + " (" + item.id + ")";
          select.appendChild(opt);
        });
        if (prev) select.value = prev;
      })
      .catch(function () {
        select.innerHTML = '<option value="">Не удалось загрузить поставки</option>';
      });
  }

  function loadJobs(root) {
    if (!isPackingJobsMarketplace()) return;
    var mp = packingMarketplaceParam();
    shell()
      .fetchJson("/api/warehouse/fbs-packing/jobs?marketplace=" + encodeURIComponent(mp))
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

  function downloadMarking(root, jobId) {
    if (!jobId || busy) return;
    setBusy(root, true);
    fetch("/api/warehouse/fbs-packing/jobs/" + jobId + "/marking.xlsx", {
      credentials: "include",
    })
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
            throw new Error(detail || "Не удалось скачать ЧЗ");
          });
        }
        return response.blob();
      })
      .then(function (blob) {
        var objectUrl = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = objectUrl;
        link.download = "fbs_marking_" + jobId + ".xlsx";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () {
          URL.revokeObjectURL(objectUrl);
        }, 1000);
        setMessage(root, "Файл ЧЗ скачан.", false);
      })
      .catch(function (error) {
        setMessage(root, error.message || "Ошибка скачивания ЧЗ", true);
      })
      .finally(function () {
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
    if (activeMarketplace === "wildberries") {
      var wbLimitEl = root.querySelector("#whFbsItemLimit");
      var wbLimit = wbLimitEl ? String(wbLimitEl.value || "").trim() : "";
      var substatus = wbSubstatus(root);
      var supplyEl = root.querySelector("#whFbsWbSupply");
      var supplyId = supplyEl ? String(supplyEl.value || "").trim() : "";
      if (asForm) {
        var wbForm = new FormData();
        wbForm.append("marketplace", "wildberries");
        wbForm.append("order_substatus", substatus);
        if (wbLimit) wbForm.append("item_limit", wbLimit);
        if (supplyId) wbForm.append("supply_id", supplyId);
        return wbForm;
      }
      var wbParams = new URLSearchParams();
      wbParams.set("marketplace", "wildberries");
      wbParams.set("order_substatus", substatus);
      if (wbLimit) wbParams.set("item_limit", wbLimit);
      if (supplyId) wbParams.set("supply_id", supplyId);
      return "?" + wbParams.toString();
    }
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
      yandexParams.set("marketplace", "yandex");
      return "?" + yandexParams.toString();
    }
    var first = String((root.querySelector("#whFbsFirstPosting") || {}).value || "").trim();
    var last = String((root.querySelector("#whFbsLastPosting") || {}).value || "").trim();
    var ozonParams = new URLSearchParams();
    ozonParams.set("marketplace", "ozon");
    if (first) ozonParams.set("first_posting", first);
    if (last) ozonParams.set("last_posting", last);
    return "?" + ozonParams.toString();
  }

  function refreshList(root) {
    if (busy) return;
    setBusy(root, true);
    setMessage(root, "Получение заказов…", false);
    var url = "/api/warehouse/fbs-packing/preview" + queryOrForm(root, false);
    shell()
      .fetchJson(url)
      .then(function (data) {
        renderRows(root, data.list_rows || []);
        var warnings = data.warnings || [];
        if (activeMarketplace === "ozon") {
          warnings.unshift(
            "Показано отправлений: " +
              (data.orders_count || 0) +
              " из " +
              (data.available_count != null ? data.available_count : data.orders_count || 0) +
              ", строк: " +
              (data.count || 0) +
              "."
          );
        } else {
          warnings.unshift(
            "Показано товаров: " +
              (data.count || 0) +
              " из " +
              (data.available_count != null ? data.available_count : data.count || 0) +
              "."
          );
        }
        setMessage(root, warnings.join("\n"), false);
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
    createJob(root);
  }

  function createJob(root) {
    var packers = selectedPackerIds(root);
    if (!packers.length) {
      setMessage(root, "Назначьте хотя бы одного упаковщика.", true);
      return;
    }
    setBusy(root, true);
    setMessage(root, "Создание задания и загрузка ярлыков…", false);
    var substatusEl = root.querySelector("#whFbsYandexSubstatus") || root.querySelector("#whFbsSubstatus");
    var body = {
      marketplace: packingMarketplaceParam(),
      order_substatus: substatusEl ? String(substatusEl.value || "STARTED") : "STARTED",
      build_list: buildListChecked(root),
      require_cis: requireCisChecked(root),
      packer_user_ids: packers,
    };
    if (activeMarketplace === "wildberries") {
      body.build_list = false;
      var supplyEl = root.querySelector("#whFbsWbSupply");
      var supplyId = supplyEl ? String(supplyEl.value || "").trim() : "";
      if (body.order_substatus === "READY_TO_SHIP") {
        if (!supplyId) {
          setBusy(root, false);
          setMessage(root, "Выберите поставку WB для «готовы к отгрузке».", true);
          return;
        }
        body.supply_id = supplyId;
      }
    }
    if (activeMarketplace === "ozon") {
      body.build_list = false;
      body.order_substatus = "awaiting_deliver";
      var firstEl = root.querySelector("#whFbsFirstPosting");
      var lastEl = root.querySelector("#whFbsLastPosting");
      body.first_posting = firstEl ? String(firstEl.value || "").trim() : "";
      body.last_posting = lastEl ? String(lastEl.value || "").trim() : "";
    }
    var limitEl =
      root.querySelector("#whFbsYandexItemLimit") || root.querySelector("#whFbsItemLimit");
    var itemLimit = limitEl ? String(limitEl.value || "").trim() : "";
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
        var names = {
          wildberries: "wb_fbs_labels.pdf",
          ozon: "ozon_fbs_labels.pdf",
        };
        link.download = names[activeMarketplace] || "yandex_fbs_labels.pdf";
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
      '<button type="button" class="wh-btn wh-route-tab wh-fbs-marketplace-tab' +
      (activeMarketplace === "wildberries" ? " active" : "") +
      '" data-marketplace="wildberries">Wildberries</button>' +
      "</div>" +
      marketplacePanelHtml() +
      '<p class="wh-msg" id="whFbsMessage"></p>' +
      '<div id="whFbsResult"></div>' +
      (isPackingJobsMarketplace()
        ? '<h4 class="wh-crm-section-title">Задания упаковки</h4><div id="whFbsJobs"></div>'
        : "");
    bindPanel(root);
    if (activeMarketplace === "wildberries") {
      toggleWbSupply(root);
      var subEl = root.querySelector("#whFbsSubstatus");
      if (subEl) subEl.addEventListener("change", function () {
        toggleWbSupply(root);
      });
    }
    if (isPackingJobsMarketplace()) loadJobs(root);
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
