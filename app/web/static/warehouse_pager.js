/** Общая пагинация списков /warehouse: 50 строк на страницу. */
(function (global) {
  var PAGE_SIZE = 50;

  function state(total, page, size) {
    size = Number(size) || PAGE_SIZE;
    total = Math.max(0, Number(total) || 0);
    var pages = total ? Math.max(1, Math.ceil(total / size)) : 1;
    page = Math.min(Math.max(1, Number(page) || 1), pages);
    var from = total ? (page - 1) * size + 1 : 0;
    var to = Math.min(page * size, total);
    return {
      total: total,
      page: page,
      pages: pages,
      size: size,
      from: from,
      to: to,
      offset: (page - 1) * size,
    };
  }

  function html(st) {
    st = st || state(0, 1);
    if (!st.total || st.pages <= 1) return "";
    return (
      '<div class="wh-cat-pager">' +
      '<button type="button" class="wh-btn" data-wh-pager="prev"' +
      (st.page <= 1 ? " disabled" : "") +
      ">Назад</button>" +
      '<span class="wh-cat-pager-info">' +
      st.from +
      "–" +
      st.to +
      " из " +
      st.total +
      " · стр. " +
      st.page +
      " из " +
      st.pages +
      "</span>" +
      '<button type="button" class="wh-btn" data-wh-pager="next"' +
      (st.page >= st.pages ? " disabled" : "") +
      ">Вперёд</button>" +
      "</div>"
    );
  }

  function slice(items, page, size) {
    items = items || [];
    var st = state(items.length, page, size);
    return { items: items.slice(st.offset, st.offset + st.size), state: st };
  }

  function bind(root, onDelta) {
    if (!root || typeof onDelta !== "function") return;
    var prev = root.querySelector('[data-wh-pager="prev"]');
    var next = root.querySelector('[data-wh-pager="next"]');
    if (prev) {
      prev.addEventListener("click", function () {
        if (prev.disabled) return;
        onDelta(-1);
      });
    }
    if (next) {
      next.addEventListener("click", function () {
        if (next.disabled) return;
        onDelta(1);
      });
    }
  }

  global.WH_PAGER = {
    PAGE_SIZE: PAGE_SIZE,
    state: state,
    html: html,
    slice: slice,
    bind: bind,
  };
})(window);
