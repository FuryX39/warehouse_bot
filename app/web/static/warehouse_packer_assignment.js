(function (global) {
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function idSet(values) {
    var result = {};
    (values || []).forEach(function (value) {
      var id = parseInt(value, 10);
      if (id > 0) result[id] = true;
    });
    return result;
  }

  function pickerHtml(assignees, options) {
    var opts = options || {};
    var selected = idSet(opts.selectedIds || []);
    var checkboxClass = opts.checkboxClass || "wh-packer-assignment-cb";
    var users = assignees || [];
    var allSelected = users.length > 0 && users.every(function (user) {
      return selected[parseInt(user.id, 10)];
    });
    return (
      '<div class="wh-fbs-packers wh-packer-assignment" data-packer-picker>' +
      '<label class="wh-fbs-packer-option"><input type="checkbox" data-packer-all' +
      (allSelected ? " checked" : "") +
      (users.length ? "" : " disabled") +
      " /> <strong>Все</strong></label>" +
      users
        .map(function (user) {
          var id = parseInt(user.id, 10);
          return (
            '<label class="wh-fbs-packer-option"><input type="checkbox" class="' +
            esc(checkboxClass) +
            '" data-packer-user value="' +
            esc(id) +
            '"' +
            (selected[id] ? " checked" : "") +
            " /> " +
            esc(user.display_name || user.login || "Сотрудник #" + id) +
            "</label>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  function syncAll(picker) {
    var all = picker.querySelector("[data-packer-all]");
    var users = Array.prototype.slice.call(picker.querySelectorAll("[data-packer-user]"));
    if (!all) return;
    all.checked = users.length > 0 && users.every(function (input) {
      return input.checked;
    });
    all.indeterminate =
      !all.checked &&
      users.some(function (input) {
        return input.checked;
      });
  }

  function bind(root) {
    root.querySelectorAll("[data-packer-picker]").forEach(function (picker) {
      var all = picker.querySelector("[data-packer-all]");
      if (all && all.getAttribute("data-packer-bound") !== "1") {
        all.setAttribute("data-packer-bound", "1");
        all.addEventListener("change", function () {
          picker.querySelectorAll("[data-packer-user]").forEach(function (input) {
            input.checked = all.checked;
          });
          all.indeterminate = false;
        });
      }
      picker.querySelectorAll("[data-packer-user]").forEach(function (input) {
        if (input.getAttribute("data-packer-bound") === "1") return;
        input.setAttribute("data-packer-bound", "1");
        input.addEventListener("change", function () {
          syncAll(picker);
        });
      });
      syncAll(picker);
    });
  }

  function collect(root, checkboxClass) {
    var ids = [];
    root
      .querySelectorAll("." + (checkboxClass || "wh-packer-assignment-cb") + ":checked")
      .forEach(function (input) {
        var id = parseInt(input.value, 10);
        if (id > 0) ids.push(id);
      });
    return ids;
  }

  function openEditor(options) {
    var opts = options || {};
    var backdrop = document.createElement("div");
    backdrop.className = "wh-modal-backdrop";
    backdrop.innerHTML =
      '<div class="wh-modal" role="dialog" aria-modal="true">' +
      '<div class="wh-modal-header"><h3>' +
      esc(opts.title || "Упаковщики задания") +
      '</h3><button type="button" class="wh-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="wh-modal-body"><p class="wh-muted">Выберите сотрудников, которым доступно задание.</p>' +
      pickerHtml(opts.assignees || [], {
        selectedIds: opts.selectedIds || [],
        checkboxClass: "wh-packer-assignment-modal-cb",
      }) +
      '<p class="wh-msg" data-packer-message></p></div>' +
      '<div class="wh-modal-footer"><button type="button" class="wh-btn wh-btn-primary" data-packer-save>Сохранить</button>' +
      '<button type="button" class="wh-btn" data-packer-cancel>Отмена</button></div></div>';
    document.body.appendChild(backdrop);
    bind(backdrop);

    function close() {
      backdrop.remove();
    }
    backdrop.querySelector(".wh-modal-close").addEventListener("click", close);
    backdrop.querySelector("[data-packer-cancel]").addEventListener("click", close);
    backdrop.addEventListener("click", function (event) {
      if (event.target === backdrop) close();
    });
    backdrop.querySelector("[data-packer-save]").addEventListener("click", function () {
      var ids = collect(backdrop, "wh-packer-assignment-modal-cb");
      var message = backdrop.querySelector("[data-packer-message]");
      if (!ids.length) {
        message.className = "wh-msg wh-msg-error";
        message.textContent = "Назначьте хотя бы одного упаковщика.";
        return;
      }
      var save = backdrop.querySelector("[data-packer-save]");
      save.disabled = true;
      message.className = "wh-msg";
      message.textContent = "Сохранение…";
      Promise.resolve(opts.onSave(ids))
        .then(close)
        .catch(function (error) {
          save.disabled = false;
          message.className = "wh-msg wh-msg-error";
          message.textContent = error.message || "Не удалось сохранить упаковщиков";
        });
    });
  }

  global.WhPackerAssignment = {
    bind: bind,
    collect: collect,
    openEditor: openEditor,
    pickerHtml: pickerHtml,
  };
})(window);
