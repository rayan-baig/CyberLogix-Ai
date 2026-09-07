/* The background: a field of the mark, breathing.
 *
 * Same grammar as the logo — a vertical rail, branches leaving it at 45°
 * and then running vertical to a plated pad — scattered across the page
 * at varying scale and drifting slowly up and down. Not decoration
 * borrowed from the mark: at this size the rails read as the signal
 * paths they are, and the light that runs up one of them every few
 * seconds is a reading arriving from the estate.
 *
 * Dark only, deliberately. The product commits to one visual world and
 * this is tuned against #05070D; on paper it would be invisible.
 *
 * Usage:
 *   <canvas id="circuit" class="circuit"></canvas>
 *   <script src="/static/circuit.js"></script>
 *
 * Each structure is drawn once into an offscreen sprite and then blitted
 * per frame, so an animated frame costs a handful of drawImage calls
 * rather than a few hundred path operations. Anyone who asked for less
 * motion gets one still frame and nothing after it; a hidden tab gets
 * nothing at all.
 */
(function () {
  "use strict";

  var cv = document.getElementById("circuit");
  if (!cv || !cv.getContext) return;

  var REDUCED = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var VIOLET = "#7C3FE4";
  var MID = "#5A4DE8";
  var BLUE = "#2F8BE8";
  var TIP = "#9BD2FF";
  var GROUND = "#05070D";

  var ctx = cv.getContext("2d");
  var W = 0, H = 0, dpr = 1;
  var field = [];
  var raf = 0;
  var started = 0;
  var last = 0;

  /* ---- one structure ------------------------------------------------ */

  /* Geometry in local coordinates, origin at the foot of the rail.
   * Branches leave in symmetric pairs, take their corner at exactly 45°,
   * then run vertical — the same three moves every time, which is what
   * makes a field of these read as laid out rather than grown. */
  function design(h) {
    var pairs = 2 + ((Math.random() * 2) | 0);
    var reach = h * (0.20 + Math.random() * 0.10);
    var branches = [];
    for (var i = 0; i < pairs; i++) {
      var t = (i + 1) / (pairs + 1);
      branches.push({
        leave: h * (0.30 + t * 0.42),      // further down the rail
        out: reach * (0.55 + t * 0.85),    // and further out
        tip: h * (0.74 + t * 0.16)         // stopping shorter
      });
    }
    return { h: h, branches: branches, tipR: Math.max(1.7, h * 0.030) };
  }

  function sprite(d) {
    var pad = d.tipR * 2 + 8;
    var maxOut = 0;
    for (var i = 0; i < d.branches.length; i++) {
      if (d.branches[i].out > maxOut) maxOut = d.branches[i].out;
    }
    var w = (maxOut + pad) * 2;
    var hh = d.h + pad * 2;

    var c = document.createElement("canvas");
    c.width = Math.ceil(w * dpr);
    c.height = Math.ceil(hh * dpr);
    var g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.translate(w / 2, hh - pad);          // origin: foot of the rail

    var wash = g.createLinearGradient(0, 0, 0, -d.h);
    wash.addColorStop(0, VIOLET);
    wash.addColorStop(0.5, MID);
    wash.addColorStop(1, BLUE);

    g.strokeStyle = wash;
    g.lineCap = "round";
    g.lineJoin = "round";

    // A soft pass under the crisp one: a trace on a dark board should
    // read as lit rather than as drawn.
    for (var pass = 0; pass < 2; pass++) {
      g.globalAlpha = pass === 0 ? 0.13 : 0.60;
      g.lineWidth = pass === 0
        ? Math.max(3, d.h * 0.055) : Math.max(1, d.h * 0.020);
      g.beginPath();
      g.moveTo(0, 0);
      g.lineTo(0, -d.h);
      g.stroke();

      g.lineWidth = pass === 0
        ? Math.max(2.4, d.h * 0.042) : Math.max(0.9, d.h * 0.015);
      g.beginPath();
      for (var j = 0; j < d.branches.length; j++) {
        var b = d.branches[j];
        for (var s = -1; s <= 1; s += 2) {
          g.moveTo(0, -b.leave);
          g.lineTo(s * b.out, -(b.leave + b.out));   // exactly 45°
          g.lineTo(s * b.out, -b.tip);
        }
      }
      g.stroke();
    }

    // Plated pads: the ground punched through, the wash as the ring —
    // the same construction as the mark.
    g.globalAlpha = 1;
    function plate(x, y, r) {
      g.beginPath(); g.arc(x, y, r, 0, 6.2832);
      g.fillStyle = GROUND; g.fill();
      g.strokeStyle = wash; g.lineWidth = Math.max(0.8, r * 0.55); g.stroke();
    }
    plate(0, -d.h, d.tipR * 1.3);
    for (var k = 0; k < d.branches.length; k++) {
      plate(-d.branches[k].out, -d.branches[k].tip, d.tipR);
      plate(d.branches[k].out, -d.branches[k].tip, d.tipR);
    }

    return { canvas: c, w: w, h: hh, footY: hh - pad, cx: w / 2 };
  }

  /* ---- the field ---------------------------------------------------- */

  function build() {
    field = [];
    var count = Math.round(Math.max(5, Math.min(20, (W * H) / 82000)));
    var lane = W / count;

    for (var i = 0; i < count; i++) {
      var h = (H * 0.15) + Math.random() * (H * 0.30);
      var sp = sprite(design(h));
      field.push({
        sp: sp,
        // Along the width with a little jitter, so it reads as a field
        // and not as a picket fence.
        x: lane * (i + 0.5) + (Math.random() - 0.5) * lane * 0.7,
        baseY: H * (0.18 + Math.random() * 0.76),
        // Up and down: slow, and no two on the same clock.
        amp: 16 + Math.random() * 48,
        period: 9000 + Math.random() * 17000,
        phase: Math.random() * 6.2832,
        railTop: h,
        spark: null,
        nextSpark: 900 + Math.random() * 9000
      });
    }
  }

  /* ---- animation ----------------------------------------------------- */

  function frame(now) {
    if (!started) { started = now; last = now; }
    var t = now - started;
    var dt = Math.min(64, now - last);
    last = now;

    ctx.clearRect(0, 0, W, H);

    for (var i = 0; i < field.length; i++) {
      var f = field[i];
      var y = f.baseY + (REDUCED
        ? 0 : Math.sin(t / f.period * 6.2832 + f.phase) * f.amp);

      ctx.drawImage(f.sp.canvas, f.x - f.sp.cx, y - f.sp.footY,
                    f.sp.w, f.sp.h);

      if (REDUCED) continue;

      // A reading arriving: a light running up the rail to the top pad.
      if (f.spark === null) {
        f.nextSpark -= dt;
        if (f.nextSpark <= 0) {
          f.spark = 0;
          f.nextSpark = 2600 + Math.random() * 14000;
        }
        continue;
      }

      f.spark += dt / 1100;
      if (f.spark >= 1) { f.spark = null; continue; }

      var head = y - f.railTop * f.spark;
      var tail = Math.min(y, head + f.railTop * 0.22);
      var lg = ctx.createLinearGradient(0, tail, 0, head);
      lg.addColorStop(0, "rgba(0,0,0,0)");
      lg.addColorStop(1, TIP);
      ctx.strokeStyle = lg;
      ctx.lineWidth = Math.max(1.2, f.railTop * 0.022);
      ctx.lineCap = "round";
      ctx.globalAlpha = 0.78;
      ctx.beginPath();
      ctx.moveTo(f.x, tail);
      ctx.lineTo(f.x, head);
      ctx.stroke();

      // and the pad it lands on takes the light for a moment
      if (f.spark > 0.9) {
        ctx.globalAlpha = (1 - f.spark) / 0.1 * 0.65;
        ctx.beginPath();
        ctx.arc(f.x, y - f.railTop, Math.max(2.6, f.railTop * 0.05), 0, 6.2832);
        ctx.fillStyle = TIP;
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    if (!REDUCED) raf = requestAnimationFrame(frame);
  }

  /* ---- lifecycle ------------------------------------------------------ */

  function size() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    var r = cv.getBoundingClientRect();
    W = Math.max(1, r.width);
    H = Math.max(1, r.height);
    cv.width = Math.round(W * dpr);
    cv.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    build();
  }

  var pending = 0;
  function onResize() {
    clearTimeout(pending);
    pending = setTimeout(function () {
      cancelAnimationFrame(raf);
      started = 0;
      size();
      raf = requestAnimationFrame(frame);
    }, 180);
  }

  size();
  raf = requestAnimationFrame(frame);
  window.addEventListener("resize", onResize);

  // A background must never keep a laptop awake in a tab nobody is
  // looking at.
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      cancelAnimationFrame(raf);
    } else if (!REDUCED) {
      started = 0;
      raf = requestAnimationFrame(frame);
    }
  });
})();
