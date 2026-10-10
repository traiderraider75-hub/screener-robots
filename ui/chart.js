(function () {
  "use strict";

  var canvas = null;
  var ctx = null;
  var candles = [];      // [[t, o, h, l, c, vol], ...]
  var levels = [];
  var symbol = "";
  var barsVisible = 180; // окно зума (в свечах)
  var MIN_BARS = 30;
  var MAX_BARS = 1500;
  var livePrice = 0;
  var pendingFetch = false;

  var C = {
    bg: "#0d1117",
    grid: "#161b22",
    text: "#6e7681",
    up: "#3fb950",
    down: "#f85149",
    upFill: "rgba(63,185,80,0.9)",
    downFill: "rgba(248,81,73,0.9)",
    priceLine: "rgba(210,153,34,0.9)",
    level: "rgba(88,166,255,0.6)",
  };

  function ensure() {
    if (canvas) return true;
    canvas = document.getElementById("chart");
    if (!canvas) return false;
    ctx = canvas.getContext("2d");
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return true;
  }

  function onWheel(e) {
    e.preventDefault();
    var f = e.deltaY < 0 ? 0.8 : 1.25;
    barsVisible = Math.max(MIN_BARS, Math.min(MAX_BARS, Math.round(barsVisible * f)));
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

  function fmtPrice(p) {
    if (p >= 1000) return p.toFixed(0);
    if (p >= 1) return p.toFixed(2);
    return p.toPrecision(4);
  }

  function render() {
    if (!ensure()) {
      requestAnimationFrame(render);
      return;
    }
    var size = resize();
    var w = size.w, h = size.h;
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, w, h);

    var padR = 62;
    var padT = 10;
    var padB = 18;
    var padL = 6;
    var plotW = w - padR - padL;
    var plotH = h - padT - padB;

    if (!candles.length) {
      ctx.fillStyle = C.text;
      ctx.font = "11px 'SF Mono', Menlo, monospace";
      ctx.textAlign = "center";
      ctx.fillText("нет данных (уровни строятся для ★-монет Binance)", w / 2, h / 2);
      requestAnimationFrame(render);
      return;
    }

    var n = candles.length;
    var start = Math.max(0, n - barsVisible);
    var vis = candles.slice(start);
    var count = vis.length;

    var lo = Infinity, hi = -Infinity;
    var i, k;
    for (i = 0; i < count; i++) {
      var c = vis[i];
      lo = Math.min(lo, parseFloat(c[3]));
      hi = Math.max(hi, parseFloat(c[2]));
    }
    for (k = 0; k < levels.length; k++) {
      if (levels[k] > lo - (hi - lo) && levels[k] < hi + (hi - lo)) {
        lo = Math.min(lo, levels[k]);
        hi = Math.max(hi, levels[k]);
      }
    }
    if (livePrice) {
      lo = Math.min(lo, livePrice);
      hi = Math.max(hi, livePrice);
    }
    var pad = (hi - lo) * 0.06 || Math.max(hi * 0.0005, 0.0001);
    lo -= pad; hi += pad;

    function yPos(p) { return padT + (hi - p) / (hi - lo) * plotH; }
    var barW = plotW / count;
    var bodyW = Math.max(1, Math.min(barW * 0.65, 14));

    // сетка + шкала цены справа
    ctx.font = "9px 'SF Mono', Menlo, monospace";
    var gridN = 5;
    ctx.textAlign = "left";
    for (i = 0; i <= gridN; i++) {
      var p = lo + (hi - lo) * i / gridN;
      var y = Math.round(yPos(p)) + 0.5;
      ctx.strokeStyle = C.grid;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
      ctx.fillStyle = C.text;
      ctx.fillText(fmtPrice(p), w - padR + 4, y + 3);
    }

    // уровни стратегии: полоса зоны + пунктир средней
    ctx.setLineDash([4, 4]);
    for (k = 0; k < levels.length; k++) {
      var lv = levels[k];
      var lp = typeof lv === "object" ? lv.price : lv;
      if (lp < lo || lp > hi) continue;
      var ly = Math.round(yPos(lp)) + 0.5;
      // полоса зоны
      if (typeof lv === "object" && lv.hi !== undefined) {
        var yLo = Math.max(padT, yPos(Math.min(lv.hi, hi)));
        var yHi = Math.min(padT + plotH, yPos(Math.max(lv.lo, lo)));
        if (yHi > yLo) {
          ctx.setLineDash([]);
          ctx.fillStyle = "rgba(88, 166, 255, 0.08)";
          ctx.fillRect(padL, yLo, plotW, yHi - yLo);
        }
      }
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = C.level;
      ctx.beginPath();
      ctx.moveTo(padL, ly);
      ctx.lineTo(w - padR, ly);
      ctx.stroke();
      ctx.fillStyle = C.level;
      var touchesTxt = typeof lv === "object" && lv.touches ? " (" + lv.touches + ")" : "";
      ctx.fillText(fmtPrice(lp) + touchesTxt, w - padR + 4, ly - 2);
    }
    ctx.setLineDash([]);

    // свечи
    var t0 = vis[0][0];
    var t1 = vis[vis.length - 1][0];
    var dt = Math.max(t1 - t0, 60000);
    function xPos(t) { return padL + (t - t0) / dt * (plotW - barW) + barW / 2; }

    for (i = 0; i < count; i++) {
      c = vis[i];
      var o = parseFloat(c[1]), hh = parseFloat(c[2]), ll = parseFloat(c[3]), cl = parseFloat(c[4]);
      var x = xPos(c[0]);
      var up = cl >= o;
      var color = up ? C.upFill : C.downFill;
      // фитиль
      ctx.strokeStyle = up ? C.up : C.down;
      ctx.beginPath();
      ctx.moveTo(x, yPos(hh));
      ctx.lineTo(x, yPos(ll));
      ctx.stroke();
      // тело
      var yO = yPos(o), yC = yPos(cl);
      var top = Math.min(yO, yC);
      var hgt = Math.max(Math.abs(yC - yO), 1);
      ctx.fillStyle = color;
      ctx.fillRect(x - bodyW / 2, top, bodyW, hgt);
    }

    // живая цена
    if (livePrice && livePrice >= lo && livePrice <= hi) {
      var py = Math.round(yPos(livePrice)) + 0.5;
      ctx.strokeStyle = C.priceLine;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(padL, py);
      ctx.lineTo(w - padR, py);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = C.priceLine;
      ctx.fillRect(w - padR, py - 7, padR, 14);
      ctx.fillStyle = "#0d1117";
      ctx.textAlign = "left";
      ctx.font = "bold 10px 'SF Mono', Menlo, monospace";
      ctx.fillText(fmtPrice(livePrice), w - padR + 4, py + 3);
    }

    // метка времени последней свечи
    ctx.fillStyle = C.text;
    ctx.textAlign = "left";
    ctx.font = "9px 'SF Mono', Menlo, monospace";
    var d = new Date(t1);
    function p2(x) { return x < 10 ? "0" + x : "" + x; }
    ctx.fillText(p2(d.getHours()) + ":" + p2(d.getMinutes()), padL + 2, h - 4);

    requestAnimationFrame(render);
  }

  function setData(d) {
    if (!d) return;
    if (d.candles && d.candles.length) {
      candles = d.candles;
    } else if (d.reset) {
      candles = [];
    }
    levels = d.levels || levels;
    if (d.price) livePrice = d.price;
    if (d.symbol !== undefined) symbol = d.symbol;
  }

  function setLive(price) {
    if (price && candles.length) {
      livePrice = price;
      // доращивание последней свечи (когда бэктфил-тикеры не успели)
      var last = candles[candles.length - 1];
      var minute = Math.floor(Date.now() / 60000) * 60000;
      if (parseInt(last[0]) < minute) {
        candles.push([minute, String(price), String(price), String(price), String(price), "0"]);
        if (candles.length > MAX_BARS + 100) candles.splice(0, 50);
      }
    }
  }

  function isPending() { return pendingFetch; }
  function setPending(v) { pendingFetch = v; }

  window.Chart = { setData: setData, setLive: setLive, isPending: isPending, setPending: setPending };

  requestAnimationFrame(render);
})();
