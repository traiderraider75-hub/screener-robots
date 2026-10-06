(function () {
  "use strict";

  var tbody = document.getElementById("screener-body");
  var rowsCount = document.getElementById("rows-count");
  var selKey = document.getElementById("sel-key");
  var selPrice = document.getElementById("sel-price");
  var selBid = document.getElementById("sel-bid");
  var selAsk = document.getElementById("sel-ask");
  var statusDot = document.querySelector("#status .dot");
  var statusText = document.getElementById("status-text");
  var robotDetail = document.getElementById("robot-detail");
  var indSymbol = document.getElementById("ind-symbol");

  var selectedKey = null;
  var knownKeys = {};
  var tradesAll = [];
  var lastTradeTs = 0;
  var MAX_TRADES = 300000;

  function api() {
    return window.pywebview && window.pywebview.api;
  }

  function fmtNum(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    if (v >= 1000) return v.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
    return Number(v).toFixed(digits === undefined ? 4 : digits);
  }

  function microCell(score, info, kind) {
    var v = score || 0;
    var cls = "micro micro-" + kind + (v >= 50 ? " hot" : v >= 25 ? " warm" : "");
    return '<td class="' + cls + '" title="' + (info || "") + '">' + (v >= 25 ? v : "—") + "</td>";
  }

  function growthCell(g) {
    if (g === null || g === undefined) return '<td class="dim">—</td>';
    var cls = g > 0 ? "growth-pos" : g < 0 ? "growth-neg" : "dim";
    return '<td class="' + cls + '">' + (g > 0 ? "+" : "") + g.toFixed(1) + "%</td>";
  }

  function rvolCell(v, matched) {
    if (v === null || v === undefined || v <= 0) return '<td class="dim">—</td>';
    var cls = "rvol" + (matched ? " hot" : v >= 3 ? " warm" : "");
    return '<td class="' + cls + '" title="' + (matched ? "топ роста + RVOL + сделки" : "") + '">' + v.toFixed(1) + "×</td>";
  }

  var filterText = "";
  var scanOnly = false;
  document.getElementById("filter").addEventListener("input", function (e) {
    filterText = e.target.value.trim().toUpperCase();
    renderRows(lastRows);
  });
  document.getElementById("scan-filter").addEventListener("click", function (e) {
    scanOnly = !scanOnly;
    e.target.classList.toggle("active", scanOnly);
    renderRows(lastRows);
  });

  var lastRows = [];

  function renderRows(rows) {
    lastRows = rows;
    var visible = rows;
    if (filterText) {
      visible = visible.filter(function (r) {
        return r.symbol.toUpperCase().indexOf(filterText) !== -1 || r.exchange.toUpperCase().indexOf(filterText) !== -1;
      });
    }
    if (scanOnly) {
      visible = visible.filter(function (r) { return r.match; });
    }
    rowsCount.textContent = visible.length ? "(" + visible.length + ")" : "";
    var html = "";
    for (var i = 0; i < visible.length; i++) {
      var r = visible[i];
      var cls = "dir-" + r.direction;
      var rcls = "reaction-" + r.reaction;
      var selected = r.exchange.toLowerCase() + ":" + r.symbol === selectedKey ? " selected" : "";
      var fresh = "";
      if (!knownKeys[r.key]) {
        fresh = " new-robot";
        knownKeys[r.key] = true;
      }
      html +=
        '<tr data-key="' + r.exchange.toLowerCase() + ":" + r.symbol + '"' + selected + fresh + ">" +
        "<td>" + r.symbol + "</td>" +
        "<td>" + r.exchange + "</td>" +
        '<td class="score">' + r.score + "</td>" +
        '<td class="radar' + (r.radar >= 50 ? " hot" : r.radar >= 25 ? " warm" : "") + '">' + (r.radar >= 25 ? r.radar : "—") + "</td>" +
        growthCell(r.growth24) +
        "<td>" + (r.count24 !== null && r.count24 !== undefined ? fmtNum(r.count24, 0) : "—") + "</td>" +
        rvolCell(r.rvol, r.match) +
        '<td class="' + cls + '">' + (r.direction === "LONG" ? "▲ LONG" : r.direction === "SHORT" ? "▼ SHORT" : "—") + "</td>" +
        "<td>" + (r.tick_ms ? r.tick_ms + " мс" : "—") + "</td>" +
        "<td>" + (r.prints || "—") + "</td>" +
        "<td>" + (r.strength ? fmtNum(r.strength, 3) : "—") + "</td>" +
        microCell(r.hft, r.hft_info, "hft") +
        microCell(r.spoof, r.spoof_info, "spoof") +
        microCell(r.iceberg, r.iceberg_info, "iceberg") +
        '<td class="' + rcls + '">' + (r.reaction === "WITH" ? "по рынку" : r.reaction === "AGAINST" ? "против рынка" : "—") + "</td>" +
        "<td>" + (r.duration_s ? r.duration_s + "с" : "—") + "</td>" +
        "</tr>";
    }
    tbody.innerHTML = html;
    statusDot.classList.add("ok");
    statusText.textContent = "в эфире";
  }

  tbody.addEventListener("click", function (e) {
    var tr = e.target.closest("tr");
    if (!tr || !tr.dataset.key) return;
    var rows = tbody.querySelectorAll("tr");
    for (var i = 0; i < rows.length; i++) rows[i].classList.remove("selected");
    tr.classList.add("selected");
    selectedKey = tr.dataset.key;
    selKey.textContent = selectedKey;
    if (api()) api().select(selectedKey);
  });

  function renderDetail(data) {
    if (!data.robots || !data.robots.length) {
      robotDetail.innerHTML = '<span class="dim">Активных роботов на инструменте нет</span>';
      return;
    }
    var parts = [];
    for (var i = 0; i < Math.min(data.robots.length, 3); i++) {
      var r = data.robots[i];
      parts.push(
        "<b>" + r.direction + "</b> · такт <b>" + r.tick_ms + " мс</b> · принтов " + r.prints +
        " · сила <b>" + fmtNum(r.strength, 3) + "</b> · score " + r.score +
        " · <span class='dim'>" + r.duration_s + "с работы</span>"
      );
    }
    robotDetail.innerHTML = parts.join("<br>");
  }

  function pollScreener() {
    if (api()) {
      api().get_screener().then(function (json) {
        try { renderRows(JSON.parse(json)); } catch (err) {}
      });
    }
    setTimeout(pollScreener, 1000);
  }

  var pollKey = null;
  var zoomFitted = false;
  var preloadToken = 0;

  function pollIndicator() {
    if (api()) {
      var after = (selectedKey && selectedKey === pollKey) ? lastTradeTs : 0;
      api().get_indicator(after).then(function (json) {
        try {
          var data = JSON.parse(json);
          if (data.key && data.key !== selectedKey) {
            selectedKey = data.key;
            selKey.textContent = selectedKey;
          }
          indSymbol.textContent = selectedKey ? selectedKey.replace(":", " · ") : "";
          if (data.full || data.key !== pollKey) {
            tradesAll = [];
            lastTradeTs = 0;
            zoomFitted = false;
            window.Indicator.resetHistory();
            pollKey = data.key;
            preloadToken++;
            preloadHistory(data.key, preloadToken);
          }
          pollKey = data.key;
          if (data.trades.length) {
            for (var i = 0; i < data.trades.length; i++) {
              tradesAll.push(data.trades[i]);
            }
            lastTradeTs = data.trades[data.trades.length - 1].t;
            if (!zoomFitted && tradesAll.length > 50) {
              var spanS = (tradesAll[tradesAll.length - 1].t - tradesAll[0].t) / 1000;
              if (spanS > 1200) {
                window.Indicator.fitSpan();
                zoomFitted = true;
              }
            }
            if (tradesAll.length > MAX_TRADES) {
              tradesAll = tradesAll.slice(-Math.floor(MAX_TRADES / 2));
              window.Indicator.onHistoryTruncated();
            }
          }
          selPrice.textContent = data.price ? fmtNum(data.price, 1) : "";
          selBid.textContent = data.bid ? "bid " + fmtNum(data.bid, 1) : "";
          selAsk.textContent = data.ask ? "ask " + fmtNum(data.ask, 1) : "";
          data.trades = tradesAll;
          window.Indicator.setData(data);
          renderDetail(data);
        } catch (err) {
          statusText.textContent = "JS: " + (err && err.message ? err.message : err);
        }
      }, function (err) {
        statusText.textContent = "JS bridge: " + err;
      });
    }
    setTimeout(pollIndicator, 300);
  }

  function preloadHistory(key, token) {
    var parts = key.split(":");
    var since = Date.now() - 24 * 3600 * 1000;
    var cursor = Date.now();

    function step() {
      if (token !== preloadToken || !api()) return;
      api().get_history(parts[0], parts[1], cursor, 20000).then(function (json) {
        if (token !== preloadToken) return;
        var rows;
        try { rows = JSON.parse(json); } catch (err) { return; }
        if (!rows || !rows.length) return;
        var head = tradesAll.length ? tradesAll[0].t : Infinity;
        var fresh = rows.filter(function (r) { return r.t < head; });
        if (fresh.length) {
          tradesAll = fresh.concat(tradesAll);
        }
        var newCursor = fresh.length ? fresh[0].t : rows[rows.length - 1].t;
        if (newCursor >= cursor) newCursor = cursor - 600000;
        cursor = newCursor;
        if (cursor > since) {
          setTimeout(step, 40);
        }
      });
    }

    step();
  }

  function beep() {
    if (api()) {
      api().get_robot_events().then(function (json) {
        var events;
        try { events = JSON.parse(json); } catch (err) { return; }
        if (!events || !events.length) return;
        try {
          var ctx = new (window.AudioContext || window.webkitAudioContext)();
          var osc = ctx.createOscillator();
          var gain = ctx.createGain();
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.frequency.value = 880;
          gain.gain.value = 0.08;
          osc.start();
          osc.stop(ctx.currentTime + 0.15);
        } catch (err) {}
      });
    }
  }

  var statsEl = document.getElementById("stats");

  function pollStats() {
    if (api()) {
      api().get_stats().then(function (json) {
        try {
          var s = JSON.parse(json);
          statsEl.textContent = "мониторов " + s.monitored + " · в пуле " + s.pool +
            " · активных " + s.active + " · роботов " + s.robots + " · ★ " + (s.scan || 0);
        } catch (err) {}
      });
    }
    setTimeout(pollStats, 2000);
  }

  window.Indicator.setHistoryFetcher(function (beforeMs) {
    if (!api() || !selectedKey) return Promise.resolve(0);
    var parts = selectedKey.split(":");
    return api().get_history(parts[0], parts[1], beforeMs, 8000).then(function (json) {
      var rows;
      try { rows = JSON.parse(json); } catch (err) { return 0; }
      if (!rows.length) return 0;
      var head = tradesAll.length ? tradesAll[0].t : Infinity;
      var fresh = rows.filter(function (r) { return r.t < head; });
      if (!fresh.length) return 0;
      tradesAll = fresh.concat(tradesAll);
      return fresh.length;
    });
  });

  function waitApi() {
    if (api()) {
      pollScreener();
      pollIndicator();
      pollStats();
      setInterval(beep, 1500);
      setTimeout(function () {
        if (!selectedKey && lastRows.length) {
          var tr = tbody.querySelector("tr");
          if (tr && tr.dataset.key) {
            selectedKey = tr.dataset.key;
            selKey.textContent = selectedKey;
            api().select(selectedKey);
          }
        }
      }, 4000);
    } else {
      setTimeout(waitApi, 200);
    }
  }

  window.onerror = function (msg) {
    if (statusText) statusText.textContent = "JS error: " + msg;
    return false;
  };

  window.Indicator = window.Indicator || { setData: function () {} };
  waitApi();
})();
