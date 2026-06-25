(function (global) {
  var DEFAULT_PORT = 18766;
  var agentHost = "";
  var agentPort = DEFAULT_PORT;
  var agentRoute = null;
  var agentCheckedAt = 0;
  var lastAgentError = "";

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function fetchJson(url, options) {
    var shell = global.WH_SHELL || {};
    if (shell.fetchJson) return shell.fetchJson(url, options);
    return fetch(url, Object.assign({ credentials: "include" }, options || {})).then(function (r) {
      return r.json().then(function (json) {
        if (!r.ok) throw new Error((json && json.detail) || "HTTP " + r.status);
        return json;
      });
    });
  }

  function directBases() {
    var port = agentPort || DEFAULT_PORT;
    var bases = ["http://127.0.0.1:" + port, "http://localhost:" + port];
    var host = String(agentHost || "").trim();
    if (host && host !== "127.0.0.1" && host !== "localhost" && host !== "0.0.0.0") {
      bases.push("http://" + host + ":" + port);
    }
    return bases;
  }

  function probeDirect(base) {
    lastAgentError = "";
    return fetch(base + "/health", { method: "GET", mode: "cors" })
      .then(function (r) {
        if (!r.ok) {
          lastAgentError = base + " вернул HTTP " + r.status;
          return null;
        }
        return r
          .json()
          .then(function (json) {
            return json && json.ok ? base : null;
          })
          .catch(function () {
            return r.ok ? base : null;
          });
      })
      .catch(function (err) {
        lastAgentError = base + ": " + (err && err.message ? err.message : "нет ответа");
        return null;
      });
  }

  function probeProxy() {
    return fetchJson("/api/warehouse/barcode-print/health")
      .then(function (json) {
        if (json && json.ok) return "proxy";
        if (json && json.error) lastAgentError = "Прокси: " + json.error;
        return null;
      })
      .catch(function (err) {
        lastAgentError = "Прокси: " + (err && err.message ? err.message : "нет ответа");
        return null;
      });
  }

  function loadAgentPort() {
    return fetchJson("/api/warehouse/barcode-print/config")
      .then(function (json) {
        var h = String((json && json.host) || "").trim();
        if (h) agentHost = h;
        var p = parseInt(json && json.port, 10);
        if (p > 0 && p <= 65535) agentPort = p;
      })
      .catch(function () {
        /* default port */
      });
  }

  var portLoaded = loadAgentPort();

  function checkAgent(force) {
    var now = Date.now();
    if (!force && agentRoute && now - agentCheckedAt < 10000) {
      return Promise.resolve(!!agentRoute);
    }
    return portLoaded.then(function () {
      var bases = directBases();
      var chain = Promise.resolve(null);
      bases.forEach(function (base) {
        chain = chain.then(function (found) {
          if (found) return found;
          return probeDirect(base);
        });
      });
      return chain.then(function (directBase) {
        if (directBase) {
          agentRoute = { type: "direct", base: directBase };
          agentCheckedAt = now;
          return true;
        }
        return probeProxy().then(function (proxy) {
          if (proxy) {
            agentRoute = { type: "proxy" };
            agentCheckedAt = now;
            return true;
          }
          agentRoute = null;
          agentCheckedAt = now;
          return false;
        });
      });
    });
  }

  function parseCopies(raw) {
    var s = String(raw || "").trim();
    if (!s) return 1;
    var n = parseInt(s, 10);
    if (!n || n < 1) return 1;
    return Math.min(9999, n);
  }

  function readCopiesFromContext(el) {
    var wrap = el && el.closest(".wh-barcode-print-wrap");
    if (!wrap) {
      var row = el && el.closest(".wh-crm-barcode-row");
      if (row) {
        var rowInp = row.querySelector(".wh-barcode-print-copies");
        return parseCopies(rowInp && rowInp.value);
      }
      return 1;
    }
    var inp = wrap.querySelector(".wh-barcode-print-copies");
    return parseCopies(inp && inp.value);
  }

  function printViaAgent(payload) {
    if (!agentRoute) {
      return Promise.reject(new Error("Агент печати недоступен"));
    }
    if (agentRoute.type === "proxy") {
      return fetchJson("/api/warehouse/barcode-print/print", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }
    return fetch(agentRoute.base + "/print", {
      method: "POST",
      mode: "cors",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) {
      return r.json().then(function (json) {
        if (!r.ok || !json.ok) {
          throw new Error((json && json.error) || "Ошибка печати");
        }
        return json;
      });
    });
  }

  function downloadLabelPdf(productId, barcode, copies) {
    var url =
      "/api/warehouse/catalog/products/" +
      encodeURIComponent(productId) +
      "/barcode-label?barcode=" +
      encodeURIComponent(barcode);
    function once() {
      return fetch(url, { credentials: "include" })
        .then(function (r) {
          if (!r.ok) {
            return r.text().then(function (t) {
              var msg = t;
              try {
                var j = JSON.parse(t);
                if (j && j.detail) {
                  msg = typeof j.detail === "string" ? j.detail : String(j.detail);
                }
              } catch (e) {
                /* ignore */
              }
              throw new Error(msg || "HTTP " + r.status);
            });
          }
          return r.blob();
        })
        .then(function (blob) {
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = "barcode_" + String(barcode).replace(/[^\w.-]+/g, "_") + ".pdf";
          document.body.appendChild(a);
          a.click();
          setTimeout(function () {
            URL.revokeObjectURL(a.href);
            a.remove();
          }, 500);
        });
    }
    var chain = Promise.resolve();
    var n = parseCopies(copies);
    for (var i = 0; i < n; i++) {
      chain = chain.then(once);
    }
    return chain;
  }

  function openPickModal(barcodes, product) {
    return new Promise(function (resolve, reject) {
      var backdrop = document.createElement("div");
      backdrop.className = "wh-modal-backdrop";
      var list = barcodes
        .map(function (bc) {
          return (
            '<button type="button" class="wh-btn wh-barcode-pick-item" data-barcode="' +
            esc(bc) +
            '">' +
            esc(bc) +
            "</button>"
          );
        })
        .join("");
      backdrop.innerHTML =
        '<div class="wh-modal wh-barcode-pick-modal">' +
        '<h3>Выберите штрихкод</h3>' +
        '<p class="wh-muted">' +
        esc(product.name || "") +
        "</p>" +
        '<div class="wh-barcode-pick-list">' +
        list +
        "</div>" +
        '<div class="wh-modal-actions">' +
        '<button type="button" class="wh-btn" id="whBcPickCancel">Отмена</button>' +
        "</div></div>";
      document.body.appendChild(backdrop);
      function close() {
        backdrop.remove();
      }
      backdrop.querySelector("#whBcPickCancel").addEventListener("click", function () {
        close();
        reject(new Error("Отменено"));
      });
      backdrop.querySelectorAll(".wh-barcode-pick-item").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var bc = btn.getAttribute("data-barcode");
          close();
          resolve(bc);
        });
      });
    });
  }

  function sendPrint(productId, barcode, sku, name, copies) {
    var payload = {
      barcode: barcode,
      sku: sku || "",
      name: name || "",
      copies: parseCopies(copies),
    };
    return checkAgent(true).then(function (ok) {
      if (ok) {
        return printViaAgent(payload).then(function () {
          return { mode: "print", copies: payload.copies, route: agentRoute };
        });
      }
      return downloadLabelPdf(productId, barcode, payload.copies).then(function () {
        return { mode: "download", copies: payload.copies };
      });
    });
  }

  function loadProduct(productId) {
    return fetchJson("/api/warehouse/catalog/products/" + encodeURIComponent(productId)).then(function (d) {
      return d.product || {};
    });
  }

  function printProduct(productId, opts) {
    opts = opts || {};
    productId = parseInt(productId, 10);
    if (!productId) {
      return Promise.reject(new Error("Нет товара"));
    }
    var directBarcode = String(opts.barcode || "").trim();
    var sku = opts.sku || "";
    var name = opts.name || "";

    var copies = parseCopies(opts.copies);

    function run(product, barcode) {
      return sendPrint(productId, barcode, product.sku || sku, product.name || name, copies);
    }

    if (directBarcode) {
      return run({ sku: sku, name: name }, directBarcode);
    }

    return loadProduct(productId).then(function (product) {
      var barcodes = (product.barcodes || []).filter(Boolean);
      if (!barcodes.length) {
        throw new Error("У товара нет штрихкодов");
      }
      if (barcodes.length === 1) {
        return run(product, barcodes[0]);
      }
      return openPickModal(barcodes, product).then(function (bc) {
        return run(product, bc);
      });
    });
  }

  function printButtonHtml(productId, sku, name) {
    if (!productId) return "";
    return (
      '<span class="wh-barcode-print-wrap">' +
      '<input type="number" class="wh-barcode-print-copies" min="1" max="9999" placeholder="1" title="Копий" aria-label="Копий" />' +
      '<button type="button" class="wh-btn wh-btn-sm wh-barcode-print-btn" data-product-id="' +
      esc(productId) +
      '" data-sku="' +
      esc(sku || "") +
      '" data-name="' +
      esc(name || "") +
      '" title="Печать штрихкода Code128">ШК</button></span>'
    );
  }

  function showPrintResult(result) {
    var copies = (result && result.copies) || 1;
    if (result && result.mode === "download") {
      var hint =
        "Агент печати недоступен — скачано PDF: " +
        copies +
        ". Запустите start.bat на ПК с принтером (порт " +
        (agentPort || DEFAULT_PORT) +
        ").";
      if (lastAgentError) {
        hint += " Детали: " + lastAgentError + ".";
      }
      if (location.protocol === "https:") {
        hint += " Для HTTPS-сайта разрешите доступ к локальной сети в браузере.";
      }
      showToast(hint, true);
    } else {
      showToast(copies > 1 ? "Отправлено на печать: " + copies + " шт." : "Отправлено на печать", false);
    }
  }

  function showToast(msg, isError) {
    var el = document.getElementById("whBarcodePrintToast");
    if (!el) {
      el = document.createElement("div");
      el.id = "whBarcodePrintToast";
      el.className = "wh-barcode-print-toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.className = "wh-barcode-print-toast" + (isError ? " wh-barcode-print-toast--error" : "");
    el.hidden = false;
    clearTimeout(el._hideTimer);
    el._hideTimer = setTimeout(function () {
      el.hidden = true;
    }, isError ? 10000 : 3500);
  }

  document.addEventListener("click", function (e) {
    if (e.target.closest(".wh-barcode-print-copies")) {
      e.stopPropagation();
      return;
    }
    var btn = e.target.closest(".wh-barcode-print-btn");
    if (btn) {
      e.preventDefault();
      e.stopPropagation();
      btn.disabled = true;
      printProduct(btn.getAttribute("data-product-id"), {
        sku: btn.getAttribute("data-sku"),
        name: btn.getAttribute("data-name"),
        copies: readCopiesFromContext(btn),
      })
        .then(showPrintResult)
        .catch(function (err) {
          showToast(err.message || "Ошибка печати", true);
        })
        .finally(function () {
          btn.disabled = false;
        });
      return;
    }
    var oneBtn = e.target.closest(".wh-barcode-print-one");
    if (oneBtn) {
      e.preventDefault();
      e.stopPropagation();
      var row = oneBtn.closest(".wh-crm-barcode-row");
      var inp = row && row.querySelector(".wh-crm-barcode-input");
      var bc = inp && inp.value.trim();
      var pid = oneBtn.getAttribute("data-product-id");
      if (!bc) {
        showToast("Введите штрихкод", true);
        return;
      }
      oneBtn.disabled = true;
      printProduct(pid, {
        barcode: bc,
        sku: oneBtn.getAttribute("data-sku"),
        name: oneBtn.getAttribute("data-name"),
        copies: readCopiesFromContext(oneBtn),
      })
        .then(showPrintResult)
        .catch(function (err) {
          showToast(err.message || "Ошибка печати", true);
        })
        .finally(function () {
          oneBtn.disabled = false;
        });
    }
  });

  global.WhBarcodePrint = {
    printProduct: printProduct,
    printButtonHtml: printButtonHtml,
    checkAgent: checkAgent,
    agentPort: function () {
      return agentPort;
    },
  };
})(window);
