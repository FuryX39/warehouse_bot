(function (global) {
  var meta = { marketplaces: [], price_types: [] };

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

  function panelEl() {
    return shell().contentPanelEl;
  }

  function preparePanel(tab, item) {
    shell().contentTitleEl.textContent = item.title;
    shell().contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    shell().contentPlaceholderEl.hidden = true;
    panelEl().hidden = false;
    var card = document.querySelector(".wh-content-card");
    if (card) card.classList.add("wh-content-card--wide");
  }

  function optionsHtml(items, valueKey, labelKey, selectedId) {
    return (items || [])
      .map(function (item) {
        var id = item[valueKey];
        var sel = String(selectedId || "") === String(id) ? " selected" : "";
        return '<option value="' + esc(id) + '"' + sel + ">" + esc(item[labelKey]) + "</option>";
      })
      .join("");
  }

  function downloadBlob(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 500);
  }

  function parseHeaderJson(name, headers) {
    var raw = headers.get(name);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }

  function formatMoney(value) {
    if (value == null || value === "") return "0";
    var n = Number(value);
    if (Number.isNaN(n)) return String(value);
    return n.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function bindForm(root) {
    var form = root.querySelector("#whSalesAnalysisForm");
    var msg = root.querySelector("#whSalesAnalysisMsg");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      msg.className = "wh-msg";
      msg.textContent = "";
      var marketplace = root.querySelector("#whSalesMp").value;
      var priceTypeId = root.querySelector("#whSalesPriceType").value;
      if (!marketplace) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите маркетплейс.";
        return;
      }
      if (!priceTypeId) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите вид цен.";
        return;
      }
      var submitBtn = root.querySelector("#whSalesSubmit");
      submitBtn.disabled = true;
      msg.textContent = "Формируем отчёт…";
      var url =
        "/api/warehouse/reports/sales-analysis/export?marketplace=" +
        encodeURIComponent(marketplace) +
        "&price_type_id=" +
        encodeURIComponent(priceTypeId);
      fetch(url, { credentials: "include" })
        .then(function (r) {
          var stats = parseHeaderJson("X-Sales-Analysis-Stats", r.headers);
          if (!r.ok) {
            return r.text().then(function (text) {
              var detail = text || "HTTP " + r.status;
              try {
                var json = JSON.parse(text);
                if (json && json.detail) detail = json.detail;
              } catch (e2) {}
              throw new Error(typeof detail === "string" ? detail : "Не удалось сформировать отчёт");
            });
          }
          return r.blob().then(function (blob) {
            return { blob: blob, stats: stats };
          });
        })
        .then(function (result) {
          var stats = result.stats || {};
          var mp = String(stats.marketplace || marketplace).replace(/\s+/g, "_");
          var pt = String(stats.price_type || "prices").replace(/[^\w\-а-яА-ЯёЁ]+/gi, "_");
          downloadBlob(result.blob, "sales_analysis_" + mp + "_" + pt + ".xlsx");
          var extra = "";
          if (stats.missing_price_count) {
            extra =
              " Без цены в выбранном виде цен: " +
              stats.missing_price_count +
              " арт. (сумма по ним = 0).";
          }
          msg.className = "wh-msg wh-msg-ok";
          msg.textContent =
            "Готово. Позиций: " +
            (stats.rows || 0) +
            ", количество: " +
            (stats.quantity || 0) +
            ", сумма: " +
            formatMoney(stats.sum) +
            "." +
            extra +
            " Файл скачан.";
        })
        .catch(function (err) {
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || String(err);
        })
        .finally(function () {
          submitBtn.disabled = false;
        });
    });
  }

  function renderSalesAnalysis(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-placeholder">Загрузка...</p>';
    var fetchJson = shell().fetchJson;
    fetchJson("/api/warehouse/reports/sales-analysis/meta")
      .then(function (data) {
        meta = data || meta;
        root.innerHTML =
          '<div class="wh-route-card">' +
          '<p class="wh-muted">Отчёт по заказам выбранного маркетплейса: позиции суммируются по артикулу. ' +
          "Сумма считается по выбранному виду цен (количество × цена из каталога). Отменённые заказы не входят.</p>" +
          '<form id="whSalesAnalysisForm" class="wh-reports-form">' +
          '<label><span>Маркетплейс</span><select id="whSalesMp" required>' +
          '<option value="">— выберите —</option>' +
          optionsHtml(meta.marketplaces, "id", "title", "") +
          "</select></label>" +
          '<label><span>Вид цен</span><select id="whSalesPriceType" required>' +
          '<option value="">— выберите —</option>' +
          optionsHtml(meta.price_types, "id", "name", "") +
          "</select></label>" +
          '<div class="wh-tools-actions">' +
          '<button type="submit" class="wh-btn wh-btn-primary" id="whSalesSubmit">Сформировать Excel</button>' +
          "</div>" +
          "</form>" +
          '<p class="wh-msg" id="whSalesAnalysisMsg"></p>' +
          "</div>";
        bindForm(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function previousMonthValue() {
    var now = new Date();
    var d = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    var m = String(d.getMonth() + 1);
    if (m.length < 2) m = "0" + m;
    return d.getFullYear() + "-" + m;
  }

  function bindWbAcquiring(root) {
    var form = root.querySelector("#whWbAcqForm");
    var msg = root.querySelector("#whWbAcqMsg");
    var submitBtn = root.querySelector("#whWbAcqSubmit");
    if (!form) return;
    var pollTimer = null;

    function stopPoll() {
      if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
    }

    function setBusy(busy, text) {
      submitBtn.disabled = !!busy;
      form.querySelector("#whWbAcqMonth").disabled = !!busy;
      msg.className = busy ? "wh-msg" : msg.className;
      if (text) msg.textContent = text;
    }

    function pollJob(jobId) {
      shell()
        .fetchJson("/api/warehouse/reports/wb-acquiring/jobs/" + encodeURIComponent(jobId))
        .then(function (job) {
          if (job.status === "running") {
            setBusy(true, job.message || "Формируем отчёт…");
            pollTimer = setTimeout(function () {
              pollJob(jobId);
            }, 2000);
            return;
          }
          if (job.status === "error") {
            stopPoll();
            msg.className = "wh-msg wh-msg-error";
            msg.textContent = job.error || job.message || "Не удалось сформировать отчёт";
            setBusy(false);
            submitBtn.disabled = false;
            form.querySelector("#whWbAcqMonth").disabled = false;
            return;
          }
          return fetch(
            "/api/warehouse/reports/wb-acquiring/jobs/" + encodeURIComponent(jobId) + "/download",
            { credentials: "include" }
          ).then(function (r) {
            if (!r.ok) {
              return r.text().then(function (text) {
                throw new Error(text || "Не удалось скачать файл");
              });
            }
            return r.blob().then(function (blob) {
              downloadBlob(blob, job.filename || "wb_acquiring.xlsx");
              var stats = job.stats || {};
              stopPoll();
              msg.className = "wh-msg wh-msg-ok";
              msg.textContent =
                "Готово. Строк: " +
                (stats.rows || 0) +
                ", издержки (нетто): " +
                formatMoney(stats.net_fee) +
                ", НДС (нетто): " +
                formatMoney(stats.net_vat) +
                ". Файл скачан.";
              submitBtn.disabled = false;
              form.querySelector("#whWbAcqMonth").disabled = false;
            });
          });
        })
        .catch(function (err) {
          stopPoll();
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || String(err);
          submitBtn.disabled = false;
          form.querySelector("#whWbAcqMonth").disabled = false;
        });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      stopPoll();
      msg.className = "wh-msg";
      var month = root.querySelector("#whWbAcqMonth").value;
      if (!month) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = "Выберите месяц.";
        return;
      }
      setBusy(true, "Запускаем выгрузку. У WB лимит 1 запрос в минуту — может занять несколько минут.");
      fetch("/api/warehouse/reports/wb-acquiring/generate", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ month: month }),
      })
        .then(function (r) {
          return r.text().then(function (text) {
            var json = null;
            try {
              json = text ? JSON.parse(text) : null;
            } catch (e2) {
              json = null;
            }
            if (!r.ok) {
              var detail = (json && json.detail) || text || "HTTP " + r.status;
              throw new Error(typeof detail === "string" ? detail : "Не удалось запустить выгрузку");
            }
            return json;
          });
        })
        .then(function (data) {
          if (!data || !data.job_id) throw new Error("Нет идентификатора задания");
          pollJob(data.job_id);
        })
        .catch(function (err) {
          stopPoll();
          msg.className = "wh-msg wh-msg-error";
          msg.textContent = err.message || String(err);
          submitBtn.disabled = false;
          form.querySelector("#whWbAcqMonth").disabled = false;
        });
    });
  }

  function renderWbAcquiring(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-placeholder">Загрузка...</p>';
    shell()
      .fetchJson("/api/warehouse/reports/wb-acquiring/meta")
      .then(function (data) {
        if (!data || !data.configured) {
          root.innerHTML =
            '<div class="wh-route-card">' +
            '<p class="wh-msg wh-msg-error">Не задан WB_API_TOKEN. Для этого отчёта нужен токен WB с категорией «Финансы».</p>' +
            "</div>";
          return;
        }
        root.innerHTML =
          '<div class="wh-route-card">' +
          '<p class="wh-muted">Выгрузка отчёта Wildberries «Издержки на приём платежей» за календарный месяц. ' +
          "В Excel — сводка (нетто, продажи минус возвраты) и полная детализация, как в кабинете WB. " +
          "Запрос к WB ограничен: 1 раз в минуту, поэтому выгрузка может идти несколько минут.</p>" +
          '<form id="whWbAcqForm" class="wh-reports-form">' +
          '<label><span>Месяц</span><input type="month" id="whWbAcqMonth" required value="' +
          esc(previousMonthValue()) +
          '" /></label>' +
          '<div class="wh-tools-actions">' +
          '<button type="submit" class="wh-btn wh-btn-primary" id="whWbAcqSubmit">Сформировать Excel</button>' +
          "</div>" +
          "</form>" +
          '<p class="wh-msg" id="whWbAcqMsg"></p>' +
          "</div>";
        bindWbAcquiring(root);
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function render(tab, item) {
    if (item.id === "sales-analysis") {
      renderSalesAnalysis(tab, item);
      return;
    }
    if (item.id === "wb-acquiring") {
      renderWbAcquiring(tab, item);
      return;
    }
    shell().contentTitleEl.textContent = item.title;
    shell().contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    panelEl().hidden = true;
    var ph = shell().contentPlaceholderEl;
    ph.hidden = false;
    ph.textContent =
      "Раздел «" + item.title + "» пока не реализован. Навигация подготовлена — функционал добавим позже.";
  }

  global.WhReports = { render: render };
})(window);
