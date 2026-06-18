(function (global) {
  var AGENT_BASE = "http://127.0.0.1:18766";
  var agentOk = null;
  var agentCheckedAt = 0;

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

  function checkAgent(force) {
    var now = Date.now();
    if (!force && agentOk !== null && now - agentCheckedAt < 15000) {
      return Promise.resolve(agentOk);
    }
    return fetch(AGENT_BASE + "/health", { method: "GET", mode: "cors" })
      .then(function (r) {
        agentOk = r.ok;
        agentCheckedAt = now;
        return agentOk;
      })
      .catch(function () {
        agentOk = false;
        agentCheckedAt = now;
        return false;
      });
  }

  function printViaAgent(payload) {
    return fetch(AGENT_BASE + "/print", {
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

  function downloadLabelPdf(productId, barcode) {
    var url =
      "/api/warehouse/catalog/products/" +
      encodeURIComponent(productId) +
      "/barcode-label?barcode=" +
      encodeURIComponent(barcode);
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

  function sendPrint(productId, barcode, sku, name) {
    var payload = { barcode: barcode, sku: sku || "", name: name || "" };
    return checkAgent().then(function (ok) {
      if (ok) {
        return printViaAgent(payload).then(function () {
          return { mode: "print" };
        });
      }
      return downloadLabelPdf(productId, barcode).then(function () {
        return { mode: "download" };
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

    function run(product, barcode) {
      return sendPrint(productId, barcode, product.sku || sku, product.name || name);
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
      '<button type="button" class="wh-btn wh-btn-sm wh-barcode-print-btn" data-product-id="' +
      esc(productId) +
      '" data-sku="' +
      esc(sku || "") +
      '" data-name="' +
      esc(name || "") +
      '" title="Печать штрихкода Code128">ШК</button>'
    );
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
    }, isError ? 8000 : 3500);
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".wh-barcode-print-btn");
    if (btn) {
      e.preventDefault();
      btn.disabled = true;
      printProduct(btn.getAttribute("data-product-id"), {
        sku: btn.getAttribute("data-sku"),
        name: btn.getAttribute("data-name"),
      })
        .then(function (result) {
          if (result && result.mode === "download") {
            showToast(
              "Агент печати не запущен — PDF скачан. Запустите start_barcode_print_agent.bat на этом ПК.",
              true
            );
          } else {
            showToast("Отправлено на печать", false);
          }
        })
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
      var row = oneBtn.closest(".wh-crm-barcode-row");
      var inp = row && row.querySelector(".wh-crm-barcode-input");
      var bc = inp && inp.value.trim();
      var pid = oneBtn.getAttribute("data-product-id");
      if (!bc) {
        showToast("Введите штрихкод", true);
        return;
      }
      oneBtn.disabled = true;
      printProduct(pid, { barcode: bc, sku: oneBtn.getAttribute("data-sku"), name: oneBtn.getAttribute("data-name") })
        .then(function (result) {
          if (result && result.mode === "download") {
            showToast(
              "Агент печати не запущен — PDF скачан. Запустите start_barcode_print_agent.bat на этом ПК.",
              true
            );
          } else {
            showToast("Отправлено на печать", false);
          }
        })
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
    agentUrl: AGENT_BASE,
  };
})(window);
