(function () {
  "use strict";

  var canvas = null;
  var ctx = null;
  var data = { trades: [], price: 0, bid: null, ask: null, robots: [] };

  var COLORS = {
    bg: "#0d1117",
    centerLine: "#30363d",
    grid: "#161b22",
    buyRobot: "#3fb950",
    sellRobot: "#f85149",
    scaleText: "#6e7681",
    live: "#3fb950",
    hist: "#d29922",
  };

  var pxPerSec = 12;
  var volFactor = 1;
  var followLive = true;
  var viewRightT = 0;
  var truncatedNote = 0;

  var historyFetcher = null;
  var historyFetching = false;
  var historyExhausted = false;

  var MIN_PX_SEC = 0.005;
  var MAX_PX_SEC = 120;
  var MIN_VOL = 0.2;
  var MAX_VOL = 20;

  function ensureCanvas() {
    if (canvas) return true;
    canvas = document.getElementById("indicator");
    if (!canvas) return false;
    ctx = canvas.getContext("2d");
    bindInput();
    return true;
  }

  function resize() {
    var rect = canvas.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    var w = Math.max(10, Math.floor(rect.width));
    var h = Math.max(10, Math.floor(rect.height));
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w: w, h: h };
  }

  function fmtVol(v) {
    if (v >= 1000) return (v / 1000).toFixed(1) + "k";
    if (v >= 1) return v.toFixed(1);
    return v.toPrecision(2);
  }

  function fmtClock(ts) {
    var d = new Date(ts);
    function p(n) { return n < 10 ? "0" + n : "" + n; }
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  function fmtAgo(ms) {
    var s = Math.floor(ms / 1000);
    if (s < 60) return "-" + s + "с";
    return "-" + Math.floor(s / 60) + "м" + (s % 60) + "с";
  }

  function render() {
    var size = resize();
    var w = size.w;
    var h = size.h;
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, w, h);

    var padL = 44;
    var padR = 16;
    var mid = Math.floor(h / 2) + 10;
    var halfH = mid - 26;

    var trades = data.trades || [];
    drawBadge(w);

    if (!trades.length) {
      drawCenter(mid, w);
      ctx.fillStyle = COLORS.scaleText;
      ctx.font = "11px 'SF Mono', Menlo, monospace";
      ctx.textAlign = "center";
      ctx.fillText("нет сделок по инструменту", w / 2, mid - 12);
      return;
    }

    var lastT = trades[trades.length - 1].t;
    var pxPerMs = pxPerSec / 1000;
    if (followLive) {
      viewRightT = lastT + 150 / pxPerMs;
    }

    function xPos(t) {
      return w - padR - (viewRightT - t) * pxPerMs;
    }

    var i = trades.length - 1;
    while (i >= 0 && xPos(trades[i].t) > w + 20) i--;
    var endIdx = i;
    while (i >= 0 && xPos(trades[i].t) >= -20) i--;
    var startIdx = i + 1;
    if (endIdx < startIdx) {
      drawCenter(mid, w);
      return;
    }

    var maxVol = 0;
    var j;
    for (j = startIdx; j <= endIdx; j++) {
      if (trades[j].s > maxVol) maxVol = trades[j].s;
    }
    if (maxVol <= 0) maxVol = 1;
    var volScale = (maxVol / volFactor);

    var gridStep = niceStep(volScale / 3.5);
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.font = "10px 'SF Mono', Menlo, monospace";
    ctx.fillStyle = COLORS.scaleText;
    ctx.textAlign = "left";
    var v;
    for (v = gridStep; v <= volScale * 1.02; v += gridStep) {
      var y = mid - (v / volScale) * halfH;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w, y);
      ctx.stroke();
      ctx.fillText(fmtVol(v), 4, y - 2);
      var y2 = mid + (v / volScale) * halfH;
      ctx.beginPath();
      ctx.moveTo(padL, y2);
      ctx.lineTo(w, y2);
      ctx.stroke();
      ctx.fillText(fmtVol(v), 4, y2 + 10);
    }

    var dts = [];
    for (j = Math.max(startIdx, endIdx - 60); j < endIdx; j++) {
      dts.push(trades[j + 1].t - trades[j].t);
    }
    dts.sort(function (a, b) { return a - b; });
    var medDt = dts.length ? dts[Math.floor(dts.length / 2)] : 1000;
    var barW = Math.max(1, Math.min(4, medDt * pxPerMs * 0.7));

    var cols = {};
    for (j = startIdx; j <= endIdx; j++) {
      var tr = trades[j];
      var xi = Math.round(xPos(tr.t));
      var col = cols[xi];
      if (!col) {
        col = [0, 0, false];
        cols[xi] = col;
      }
      if (tr.b) col[0] += tr.s; else col[1] += tr.s;
      if (tr.r) col[2] = true;
    }
    for (var key in cols) {
      var c = cols[key];
      var xi2 = parseInt(key, 10);
      var lenBuy = (c[0] / volScale) * halfH;
      var lenSell = (c[1] / volScale) * halfH;
      if (c[2]) {
        if (lenBuy > 0) {
          ctx.fillStyle = COLORS.buyRobot;
          ctx.fillRect(xi2 - barW / 2, mid - lenBuy, Math.max(barW, 1.5), lenBuy);
        }
        if (lenSell > 0) {
          ctx.fillStyle = COLORS.sellRobot;
          ctx.fillRect(xi2 - barW / 2, mid, Math.max(barW, 1.5), lenSell);
        }
      } else {
        ctx.fillStyle = "rgba(88, 166, 255, 0.30)";
        if (lenBuy > 0) ctx.fillRect(xi2 - barW / 2, mid - lenBuy, barW, lenBuy);
        ctx.fillStyle = "rgba(139, 148, 158, 0.30)";
        if (lenSell > 0) ctx.fillRect(xi2 - barW / 2, mid, barW, lenSell);
      }
    }

    maybeFetchHistory(trades, xPos, padL);
    drawNoDataShade(trades, xPos, mid, halfH, w, padL);

    drawCenter(mid, w);

    ctx.fillStyle = COLORS.scaleText;
    ctx.textAlign = "left";
    ctx.fillText(fmtVol(volScale), 4, 26 - 8);

    drawTimeAxis(mid, halfH, w, h, pxPerMs, padL, padR);
  }

  function maybeFetchHistory(trades, xPos, padL) {
    if (!historyFetcher || historyFetching || historyExhausted || !trades.length) return;
    if (followLive) return;
    if (xPos(trades[0].t) > padL + 40) {
      historyFetching = true;
      var before = trades[0].t;
      Promise.resolve()
        .then(function () { return historyFetcher(before); })
        .then(function (n) {
          if (!n) historyExhausted = true;
        })
        .catch(function () {})
        .then(function () { historyFetching = false; });
    }
  }

  function drawNoDataShade(trades, xPos, mid, halfH, w, padL) {
    if (!historyExhausted || !trades.length || followLive) return;
    var x0 = xPos(trades[0].t);
    if (x0 <= padL) return;
    ctx.save();
    ctx.fillStyle = "rgba(110, 118, 129, 0.10)";
    ctx.fillRect(0, mid - halfH, x0, halfH * 2);
    var step = 12;
    ctx.strokeStyle = "rgba(110, 118, 129, 0.35)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (var x = -halfH * 2; x < x0; x += step) {
      ctx.moveTo(x, mid - halfH);
      ctx.lineTo(x + halfH * 2, mid + halfH);
    }
    ctx.stroke();
    ctx.fillStyle = COLORS.scaleText;
    ctx.font = "10px 'SF Mono', Menlo, monospace";
    ctx.textAlign = "left";
    ctx.fillText("нет данных", 8, mid - halfH + 12);
    ctx.restore();
  }

  function drawTimeAxis(mid, halfH, w, h, pxPerMs, padL, padR) {
    var targetPx = 90;
    var stepMs = 1000;
    var steps = [100, 200, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000, 600000, 1800000, 3600000];
    for (var k = 0; k < steps.length; k++) {
      if (steps[k] * pxPerMs >= targetPx) { stepMs = steps[k]; break; }
      stepMs = steps[steps.length - 1];
    }
    var t0 = viewRightT - (w - padR - padL) / pxPerMs;
    var first = Math.ceil(t0 / stepMs) * stepMs;
    ctx.fillStyle = COLORS.scaleText;
    ctx.textAlign = "center";
    var bottom = mid + halfH;
    for (var t = first; t <= viewRightT; t += stepMs) {
      var x = w - padR - (viewRightT - t) * pxPerMs;
      if (x < padL) continue;
      ctx.fillText(fmtClock(t), x, h - 4);
      ctx.strokeStyle = COLORS.grid;
      ctx.beginPath();
      ctx.moveTo(x, mid - halfH);
      ctx.lineTo(x, bottom);
      ctx.stroke();
    }
  }

  function drawBadge(w) {
    ctx.font = "10px 'SF Mono', Menlo, monospace";
    if (followLive) {
      ctx.fillStyle = COLORS.live;
      ctx.textAlign = "right";
      ctx.fillText("● LIVE", w - 8, 12);
    } else {
      var ago = Math.max(0, (data.trades.length ? data.trades[data.trades.length - 1].t : 0) - viewRightT);
      ctx.fillStyle = COLORS.hist;
      ctx.textAlign = "right";
      ctx.fillText("ИСТОРИЯ " + fmtAgo(ago) + " · 2× клик = LIVE", w - 8, 12);
    }
    if (volFactor !== 1) {
      ctx.fillStyle = COLORS.scaleText;
      ctx.textAlign = "left";
      ctx.fillText("усиление ×" + volFactor.toFixed(1), 8, 12);
    }
    if (truncatedNote && Date.now() - truncatedNote < 5000) {
      ctx.fillStyle = COLORS.hist;
      ctx.textAlign = "center";
      ctx.fillText("старая история урезана (память)", w / 2, 12);
    }
  }

  function drawCenter(mid, w) {
    ctx.strokeStyle = COLORS.centerLine;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, mid + 0.5);
    ctx.lineTo(w, mid + 0.5);
    ctx.stroke();
  }

  function niceStep(x) {
    var pow = Math.pow(10, Math.floor(Math.log10(Math.max(x, 1e-9))));
    var n = x / pow;
    var f = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
    return f * pow;
  }

  function bindInput() {
    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      if (e.shiftKey || e.ctrlKey) {
        var f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
        volFactor = Math.min(MAX_VOL, Math.max(MIN_VOL, volFactor * f));
      } else {
        var rect = canvas.getBoundingClientRect();
        var cx = e.clientX - rect.left;
        var w = rect.width;
        var padR = 16;
        var oldPxMs = pxPerSec / 1000;
        var tCur = viewRightT - (w - padR - cx) / oldPxMs;
        pxPerSec = Math.min(MAX_PX_SEC, Math.max(MIN_PX_SEC, pxPerSec * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
        var newPxMs = pxPerSec / 1000;
        if (!followLive) {
          viewRightT = tCur + (w - padR - cx) / newPxMs;
        }
      }
    }, { passive: false });

    var dragging = false;
    var lastX = 0;
    canvas.addEventListener("mousedown", function (e) {
      dragging = true;
      lastX = e.clientX;
    });
    window.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      var dx = e.clientX - lastX;
      lastX = e.clientX;
      if (followLive && dx < 0) return;
      if (followLive) {
        followLive = false;
        viewRightT = data.trades.length ? data.trades[data.trades.length - 1].t : Date.now();
      }
      viewRightT -= dx / (pxPerSec / 1000);
      var lastT = data.trades.length ? data.trades[data.trades.length - 1].t : 0;
      if (viewRightT >= lastT) {
        viewRightT = lastT;
        followLive = true;
      }
    });
    window.addEventListener("mouseup", function () {
      dragging = false;
    });
    canvas.addEventListener("dblclick", function () {
      followLive = true;
    });
  }

  function loop() {
    render();
    requestAnimationFrame(loop);
  }

  function setData(d) {
    data = d;
  }

  function setHistoryFetcher(fn) {
    historyFetcher = fn;
  }

  function fitSpan() {
    var trades = data.trades || [];
    if (trades.length < 2 || !canvas) return;
    var spanS = (trades[trades.length - 1].t - trades[0].t) / 1000;
    if (spanS < 600) return;
    var w = canvas.getBoundingClientRect().width || 800;
    pxPerSec = Math.max(MIN_PX_SEC, Math.min(MAX_PX_SEC, (w - 60) / spanS));
    followLive = true;
  }

  function resetHistory() {
    historyExhausted = false;
    historyFetching = false;
  }

  function onHistoryTruncated() {
    truncatedNote = Date.now();
  }

  window.Indicator = {
    setData: setData,
    setHistoryFetcher: setHistoryFetcher,
    resetHistory: resetHistory,
    fitSpan: fitSpan,
    onHistoryTruncated: onHistoryTruncated,
  };

  if (ensureCanvas()) {
    loop();
  } else {
    window.addEventListener("DOMContentLoaded", function () {
      if (ensureCanvas()) loop();
    });
  }
})();
