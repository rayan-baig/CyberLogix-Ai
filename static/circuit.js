/* Circuit-trace background.
 *
 * The house motif is routed board traces — orthogonal runs with 45°
 * doglegs and a pad at every terminus, carrying a cyan → blue → violet
 * wash across the page. It is not decoration borrowed from a logo: on a
 * telemetry product the traces are the signal paths, and the lights
 * running along them are readings arriving from the estate. One trace is
 * always carrying a breach, in the alarm colour, doing what a breach
 * does — travelling somewhere and not stopping.
 *
 * Usage:
 *   <canvas id="circuit" class="circuit"></canvas>
 *   <script src="/static/circuit.js"></script>
 *
 * It attaches to any canvas with id "circuit", sizes itself to the
 * element, redraws on resize, and honours prefers-reduced-motion by
 * painting one still frame and stopping. Nothing else on the page needs
 * to know it exists.
 */
(function () {
  "use strict";

  var cv = document.getElementById("circuit");
  if (!cv || !cv.getContext) return;

  var REDUCED = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var CYAN = "#22B8E6";
  var BLUE = "#2F6BE8";
  var VIOLET = "#6B3FE4";
  var ALARM = "#FF6B4A";

  var ctx = cv.getContext("2d");
  var W = 0, H = 0, dpr = 1;
  var board = null;      // offscreen canvas holding the static network
  var traces = [];       // [{pts, len, cum}]
  var sparks = [];
  var raf = 0;

  /* ---- routing ---------------------------------------------------- */

  /* One PCB dogleg: run along the dominant axis, then take the corner at
   * 45°. This is what makes a board look like a board rather than like a
   * maze — the diagonal is always exactly 45°, never an arbitrary angle. */
  function dogleg(ax, ay, bx, by, out) {
    var dx = bx - ax, dy = by - ay;
    var sx = dx < 0 ? -1 : 1, sy = dy < 0 ? -1 : 1;
    var adx = Math.abs(dx), ady = Math.abs(dy);
    var diag = Math.min(adx, ady);

    if (adx >= ady) {
      out.push([ax + sx * (adx - diag), ay]);
    } else {
      out.push([ax, ay + sy * (ady - diag)]);
    }
    out.push([bx, by]);
  }

  function route(ax, ay, bx, by) {
    var pts = [[ax, ay]];
    var hops = 1 + ((Math.random() * 2) | 0);
    var px = ax, py = ay;
    for (var i = 1; i <= hops; i++) {
      var t = i / hops;
      // A waypoint pulled off the straight line, so runs bend like a
      // board being routed around something rather than heading straight.
      var wx = ax + (bx - ax) * t + (Math.random() - 0.5) * W * 0.16;
      var wy = ay + (by - ay) * t + (Math.random() - 0.5) * H * 0.22;
      if (i === hops) { wx = bx; wy = by; }
      dogleg(px, py, wx, wy, pts);
      px = wx; py = wy;
    }
    return pts;
  }

  function measure(pts) {
    var cum = [0], total = 0;
    for (var i = 1; i < pts.length; i++) {
      total += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      cum.push(total);
    }
    return { pts: pts, len: total, cum: cum };
  }

  function pointAt(tr, d) {
    var cum = tr.cum;
    for (var i = 1; i < cum.length; i++) {
      if (d <= cum[i]) {
        var seg = cum[i] - cum[i - 1] || 1;
        var f = (d - cum[i - 1]) / seg;
        var a = tr.pts[i - 1], b = tr.pts[i];
        return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
      }
    }
    var last = tr.pts[tr.pts.length - 1];
    return [last[0], last[1]];
  }

  /* ---- building --------------------------------------------------- */

  function build() {
    traces = [];
    // Density scales with area so a phone is not a tangle and a wall
    // display is not empty.
    var count = Math.round(Math.max(18, Math.min(60, (W * H) / 19000)));

    for (var i = 0; i < count; i++) {
      // Traces enter from an edge and run across, the way a board's
      // signals come in from a connector.
      var edge = (Math.random() * 4) | 0;
      var ax, ay;
      if (edge === 0) { ax = -20; ay = Math.random() * H; }
      else if (edge === 1) { ax = W + 20; ay = Math.random() * H; }
      else if (edge === 2) { ax = Math.random() * W; ay = -20; }
      else { ax = Math.random() * W; ay = H + 20; }

      var bx = W * (0.12 + Math.random() * 0.76);
      var by = H * (0.10 + Math.random() * 0.80);
      var tr = measure(route(ax, ay, bx, by));
      if (tr.len > 40) traces.push(tr);
    }

    paintBoard();
    sparks = [];
  }

  function wash(c) {
    // The logo's gradient, laid across the whole surface rather than per
    // trace, so every run belongs to one wash instead of repeating it.
    var g = c.createLinearGradient(0, H, W, 0);
    g.addColorStop(0.00, CYAN);
    g.addColorStop(0.34, "#2C97EA");
    g.addColorStop(0.62, BLUE);
    g.addColorStop(1.00, VIOLET);
    return g;
  }

  function paintBoard() {
    board = document.createElement("canvas");
    board.width = cv.width;
    board.height = cv.height;
    var c = board.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.lineCap = "round";
    c.lineJoin = "round";

    var g = wash(c);

    // A soft under-glow first, then the crisp line over it: a trace on a
    // dark board reads as lit rather than drawn.
    c.globalAlpha = 0.16;
    c.strokeStyle = g;
    c.lineWidth = 6;
    strokeAll(c);

    c.globalAlpha = 0.66;
    c.lineWidth = 1.5;
    strokeAll(c);

    // Pads. A trace that stops at nothing looks unfinished.
    c.globalAlpha = 1;
    for (var i = 0; i < traces.length; i++) {
      var pts = traces[i].pts;
      pad(c, pts[pts.length - 1][0], pts[pts.length - 1][1], g);
      // and at the interior corners, where a real board vias through
      for (var j = 1; j < pts.length - 1; j++) {
        if (Math.random() < 0.22) via(c, pts[j][0], pts[j][1], g);
      }
    }
  }

  function strokeAll(c) {
    c.beginPath();
    for (var i = 0; i < traces.length; i++) {
      var pts = traces[i].pts;
      c.moveTo(pts[0][0], pts[0][1]);
      for (var j = 1; j < pts.length; j++) c.lineTo(pts[j][0], pts[j][1]);
    }
    c.stroke();
  }

  function pad(c, x, y, g) {
    c.globalAlpha = 0.30;
    c.beginPath(); c.arc(x, y, 6, 0, 6.2832);
    c.fillStyle = g; c.fill();
    c.globalAlpha = 0.95;
    c.beginPath(); c.arc(x, y, 3.1, 0, 6.2832);
    c.fillStyle = g; c.fill();
    c.globalAlpha = 1;
    c.beginPath(); c.arc(x, y, 1.3, 0, 6.2832);
    c.fillStyle = "#05070D"; c.fill();
  }

  function via(c, x, y, g) {
    c.globalAlpha = 0.7;
    c.beginPath(); c.arc(x, y, 2.1, 0, 6.2832);
    c.fillStyle = g; c.fill();
    c.globalAlpha = 1;
  }

  /* ---- animation -------------------------------------------------- */

  function frame(t) {
    ctx.clearRect(0, 0, W, H);
    if (board) ctx.drawImage(board, 0, 0, W, H);

    if (!REDUCED) {
      if (sparks.length < 16 && Math.random() < 0.14 && traces.length) {
        var tr = traces[(Math.random() * traces.length) | 0];
        sparks.push({
          tr: tr,
          d: 0,
          v: 0.9 + Math.random() * 1.9,
          // Most runs are ordinary telemetry. Occasionally one is not.
          hot: Math.random() < 0.13
        });
      }

      ctx.lineCap = "round";
      for (var i = sparks.length - 1; i >= 0; i--) {
        var s = sparks[i];
        s.d += s.v;
        if (s.d > s.tr.len) { sparks.splice(i, 1); continue; }

        var head = pointAt(s.tr, s.d);
        var tail = pointAt(s.tr, Math.max(0, s.d - 26));
        var life = 1 - s.d / s.tr.len;

        var lg = ctx.createLinearGradient(tail[0], tail[1], head[0], head[1]);
        var col = s.hot ? ALARM : "#9BE0FF";
        lg.addColorStop(0, "rgba(0,0,0,0)");
        lg.addColorStop(1, col);
        ctx.strokeStyle = lg;
        ctx.lineWidth = s.hot ? 2.2 : 1.7;
        ctx.globalAlpha = 0.30 + 0.55 * life;
        ctx.beginPath();
        ctx.moveTo(tail[0], tail[1]);
        ctx.lineTo(head[0], head[1]);
        ctx.stroke();

        ctx.globalAlpha = 0.55 + 0.45 * life;
        ctx.beginPath();
        ctx.arc(head[0], head[1], s.hot ? 2.6 : 2.0, 0, 6.2832);
        ctx.fillStyle = col;
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(frame);
    }
  }

  /* ---- lifecycle -------------------------------------------------- */

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
      raf = requestAnimationFrame(frame);
    }
  });
})();
