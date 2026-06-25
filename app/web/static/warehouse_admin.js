(function (global) {
  var fields = [];

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

  function preparePanel(tab, item) {
    shell().contentTitleEl.textContent = item.title;
    shell().contentBreadcrumbEl.textContent = tab.title + " → " + item.title;
    shell().contentPlaceholderEl.hidden = true;
    panelEl().hidden = false;
    var card = document.querySelector(".wh-content-card");
    if (card) card.classList.add("wh-content-card--wide");
  }

  function isSecretKey(key) {
    return /(TOKEN|SECRET|PASSWORD|API_KEY|KEY|CLIENT_ID|CREDENTIALS)/i.test(String(key || ""));
  }

  function renderRows() {
    if (!fields.length) return '<p class="wh-msg">Переменные не найдены.</p>';
    var rows = fields
      .map(function (f, idx) {
        var secret = isSecretKey(f.key);
        return (
          '<tr data-index="' +
          idx +
          '">' +
          '<td><input type="text" class="wh-admin-env-key" value="' +
          esc(f.key) +
          '" /></td>' +
          '<td><input type="' +
          (secret ? "password" : "text") +
          '" class="wh-admin-env-value" value="' +
          esc(f.value) +
          '" autocomplete="off" /></td>' +
          '<td class="wh-admin-env-comment">' +
          esc(f.comment || "") +
          "</td>" +
          '<td><button type="button" class="wh-btn wh-btn-sm wh-admin-env-toggle">Показать</button> ' +
          '<button type="button" class="wh-btn wh-btn-sm wh-btn-danger wh-admin-env-remove">Удалить</button></td>' +
          "</tr>"
        );
      })
      .join("");
    return (
      '<table class="wh-employees-table wh-crm-table wh-admin-env-table">' +
      "<thead><tr><th>Переменная</th><th>Значение</th><th>Описание</th><th></th></tr></thead><tbody>" +
      rows +
      "</tbody></table>"
    );
  }

  function syncFromDom(root) {
    var next = [];
    root.querySelectorAll("tr[data-index]").forEach(function (tr) {
      var key = tr.querySelector(".wh-admin-env-key").value.trim();
      var value = tr.querySelector(".wh-admin-env-value").value;
      var comment = tr.querySelector(".wh-admin-env-comment").textContent || "";
      if (!key && !value) return;
      next.push({ key: key, value: value, comment: comment });
    });
    fields = next;
  }

  function refreshRows(root) {
    var box = root.querySelector("#whAdminEnvRows");
    box.innerHTML = renderRows();
    bindRowEvents(root);
  }

  function bindRowEvents(root) {
    root.querySelectorAll(".wh-admin-env-toggle").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var inp = btn.closest("tr").querySelector(".wh-admin-env-value");
        inp.type = inp.type === "password" ? "text" : "password";
        btn.textContent = inp.type === "password" ? "Показать" : "Скрыть";
      });
    });
    root.querySelectorAll(".wh-admin-env-remove").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (!confirm("Удалить переменную из .env?")) return;
        syncFromDom(root);
        var idx = parseInt(btn.closest("tr").getAttribute("data-index"), 10);
        fields.splice(idx, 1);
        refreshRows(root);
      });
    });
  }

  function saveEnv(root) {
    var msg = root.querySelector("#whAdminEnvMsg");
    msg.className = "wh-msg";
    msg.textContent = "Сохранение…";
    syncFromDom(root);
    fetchJson("/api/warehouse/admin/env", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields: fields }),
    })
      .then(function (data) {
        fields = data.fields || fields;
        msg.className = "wh-msg wh-msg-ok";
        msg.textContent = "Сохранено. Перезапустите веб-сервер и бота, чтобы настройки применились полностью.";
        refreshRows(root);
      })
      .catch(function (err) {
        msg.className = "wh-msg wh-msg-error";
        msg.textContent = err.message || "Ошибка сохранения";
      });
  }

  function renderEnv(tab, item) {
    preparePanel(tab, item);
    var root = panelEl();
    root.innerHTML = '<p class="wh-msg">Загрузка настроек…</p>';
    fetchJson("/api/warehouse/admin/env")
      .then(function (data) {
        fields = data.fields || [];
        root.innerHTML =
          '<div class="wh-crm-toolbar">' +
          '<button type="button" class="wh-btn" id="whAdminEnvAdd">+ Переменная</button>' +
          '<button type="button" class="wh-btn wh-btn-primary" id="whAdminEnvSave">Сохранить .env</button>' +
          "</div>" +
          '<p class="wh-msg" id="whAdminEnvMsg"></p>' +
          '<p class="wh-muted">Доступно только администраторам. Секреты скрыты в полях пароля, но сохраняются в обычный файл <code>.env</code>. ' +
          esc(data.restart_required_note || "После сохранения перезапустите сервис.") +
          "</p>" +
          '<div id="whAdminEnvRows">' +
          renderRows() +
          "</div>";
        bindRowEvents(root);
        root.querySelector("#whAdminEnvAdd").addEventListener("click", function () {
          syncFromDom(root);
          fields.push({ key: "", value: "", comment: "Новая переменная" });
          refreshRows(root);
        });
        root.querySelector("#whAdminEnvSave").addEventListener("click", function () {
          if (!confirm("Сохранить изменения в .env? Неверные значения могут остановить бота или веб-панель после перезапуска.")) return;
          saveEnv(root);
        });
      })
      .catch(function (err) {
        root.innerHTML = '<p class="wh-msg wh-msg-error">' + esc(err.message) + "</p>";
      });
  }

  function render(tab, item) {
    if (item.id === "env") {
      renderEnv(tab, item);
      return;
    }
    preparePanel(tab, item);
    panelEl().innerHTML = '<p class="wh-msg">Раздел админ-панели пока не реализован.</p>';
  }

  global.WhAdmin = { render: render };
})(window);
