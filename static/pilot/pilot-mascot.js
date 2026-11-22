// Pilot core 1.4.2 · pilot-mascot.js
// The Pilot robot and the P mark.
//
// Also where the robot may sit (placement): it steps back while a page's
// own pop-up is open, keeps clear of things an app asks it to avoid (and of
// another helper character), and of the page's own words and buttons
// wherever it is scrolled to (contentIn), picking a free corner, or tucks
// half away at the edge until tapped (or, with keepInViewport, shrinks
// into the corner, fully on screen). A box being typed in always comes
// first: the robot never sits on it (hard blocks). A page's own pop-up
// counts even when it is not marked as one: anything fixed that covers the
// whole screen above the page (a backdrop) makes the robot step back too.
//
// The robot is the owner's own picture (pilot-robot-128.webp and
// pilot-robot-256.webp, transparent), shown as an <img> at the size the
// screen needs. pilot.css gives it life: a gentle float, a soft blue glow
// while Pilot is thinking, and nothing moving under "reduce motion".
// The P with its orbit, for the chat window's header, is drawn as SVG with
// attributes only (no style attributes), so it keeps a strict
// Content-Security-Policy. The desktop app reuses this file and the pictures.
//
// Core 1.4.1: on a phone, or while a box is typed in, when every spot would
// cover a link, a button, a bar that stays put or something to avoid, the
// robot fades out (hide) instead, until there is room again; and on a phone
// once the page is scrolled it keeps to one bottom corner, tucked away
// (scrollPlace), moving only when that corner would cover something pressed.
(function (root) {
  "use strict";

  const VERSION = "1.4.2";
  const NS = "http://www.w3.org/2000/svg";
  let made = 0;

  function el(doc, name, attrs, kids) {
    const node = doc.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
    for (const kid of kids || []) node.appendChild(kid);
    return node;
  }

  // The robot picture. urls: {small, large} (the 128 and 256 pixel files),
  // and (core 1.4.1) `set`: more sizes as srcset candidates, "url 112w, url
  // 168w, url 176w": a phone's 56 px robot at 2x and 3x and a computer's
  // 88 px at 2x, so the browser shows a file at its own size instead of
  // shrinking a bigger one (soft on phones). Without it, as before.
  // SIZES: how wide the robot is drawn (pilot.css --pilot-size).
  const SIZES = "(max-width: 720px) 56px, 88px";
  function robot(doc, urls) {
    const d = doc || root.document;
    const u = urls || {};
    const img = d.createElement("img");
    img.className = "pilot-robot";
    img.setAttribute("alt", "");
    img.setAttribute("width", "128");
    img.setAttribute("height", "124");
    img.setAttribute("decoding", "async");
    img.setAttribute("draggable", "false");
    const more = typeof u.set === "string" && u.set.trim() ? `, ${u.set.trim()}` : "";
    if (u.large) img.setAttribute("srcset", `${u.small} 128w, ${u.large} 256w${more}`);
    img.setAttribute("sizes", SIZES);
    img.setAttribute("src", u.small || "");
    return img;
  }

  // The "P" with an orbit swoosh around it, for the chat window's header.
  function mark(doc) {
    const d = doc || root.document;
    const n = (made += 1);
    const svg = el(d, "svg", { viewBox: "0 0 80 64", class: "pilot-mark", "aria-hidden": "true", focusable: "false" });
    svg.appendChild(el(d, "defs", {}, [
      el(d, "linearGradient", { id: `pm-p-${n}`, x1: 0, y1: 0, x2: 1, y2: 1 }, [
        el(d, "stop", { offset: "0", "stop-color": "#7cc4ff" }), el(d, "stop", { offset: "0.5", "stop-color": "#3b82f6" }),
        el(d, "stop", { offset: "1", "stop-color": "#4f46e5" })]),
      el(d, "linearGradient", { id: `pm-o-${n}`, x1: 0, y1: 0, x2: 1, y2: 0 }, [
        el(d, "stop", { offset: "0", "stop-color": "#38bdf8" }), el(d, "stop", { offset: "1", "stop-color": "#6366f1" })]),
    ]));
    // The back half of the orbit, behind the P.
    svg.appendChild(el(d, "path", { d: "M14 44 C 2 34, 30 18, 62 16", fill: "none", stroke: `url(#pm-o-${n})`, "stroke-width": 4,
      "stroke-linecap": "round", opacity: 0.75 }));
    // The P: a slanted stem and a round bowl.
    svg.appendChild(el(d, "path", { d: "M30 56 L37 12 H52 A12.5 12.5 0 0 1 52 37 H40", fill: "none", stroke: `url(#pm-p-${n})`,
      "stroke-width": 9, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    // The front half of the orbit, sweeping across the P.
    svg.appendChild(el(d, "path", { d: "M62 16 C 84 15, 74 32, 40 42 C 26 46, 16 47, 14 44", fill: "none", stroke: `url(#pm-o-${n})`,
      "stroke-width": 4, "stroke-linecap": "round" }));
    return svg;
  }

  // ---------------------------------------------------------------- placement

  // A page's own pop-ups: while one is showing, the robot steps back.
  const MODALS = 'dialog[open], [aria-modal="true"], [role="dialog"], [role="alertdialog"]';

  function shown(el, win) {
    if (!el || !el.getClientRects || el.getClientRects().length === 0) return false;
    if (el.closest && el.closest("[hidden]")) return false;
    const w = win || root;
    const st = w && w.getComputedStyle ? w.getComputedStyle(el) : null;
    return !st || (st.visibility !== "hidden" && st.display !== "none" && Number(st.opacity) !== 0);
  }

  // Is a pop-up of the page showing? own: Pilot's root (its window doesn't
  // count). extra: the app's own pop-up selectors (config.modals), for
  // pop-ups that aren't marked as dialogs (a CSS :target sheet, say).
  // Also true while something fixed covers the whole screen above the page
  // (coveredAt), so an unmarked pop-up's backdrop is never under the robot.
  function modalOpen(doc, own, win, extra) {
    const d = doc || root.document;
    const sels = [MODALS, ...(Array.isArray(extra) ? extra.filter((x) => typeof x === "string" && x) : [])];
    for (const sel of sels) {
      let found = [];
      try { found = d.querySelectorAll(sel); } catch (_) { continue; }
      for (const el of found) {
        if (own && own.contains(el)) continue;
        if (shown(el, win)) return true;
      }
    }
    return covered(d, own, win);
  }

  // A layer that covers the screen: fixed, at least 90% of the window each
  // way, not behind the page (a negative z-index), not see-through to taps
  // (elementsFromPoint skips pointer-events: none, so page scenery like a
  // starry sky never counts).
  function coverLayer(el, win) {
    const w = win || root;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const tag = String(n.tagName || "").toUpperCase();
      if (tag === "BODY" || tag === "HTML") return null;
      const st = w.getComputedStyle ? w.getComputedStyle(n) : null;
      if (!st || st.position !== "fixed") continue;
      if (parseInt(st.zIndex, 10) < 0) return null;
      const r = n.getBoundingClientRect();
      if (r.width >= w.innerWidth * 0.9 && r.height >= w.innerHeight * 0.9) return n;
    }
    return null;
  }

  // Is the screen covered at its middle and near the bottom corner by the
  // same fixed layer (a pop-up's backdrop)?
  function covered(doc, own, win) {
    const d = doc || root.document;
    const w = win || root;
    if (!d || typeof d.elementsFromPoint !== "function" || !w || !w.innerWidth) return false;
    const topAt = (x, y) => {
      let stack = [];
      try { stack = d.elementsFromPoint(x, y) || []; } catch (_) { return null; }
      return stack.find((el) => !(own && own.contains(el))) || null;
    };
    const a = topAt(w.innerWidth / 2, w.innerHeight / 2);
    const layer = a && coverLayer(a, w);
    if (!layer) return false;
    const b = topAt(w.innerWidth - 40, w.innerHeight - 40);
    return Boolean(b && (b === layer || layer.contains(b)));
  }

  const CORNERS = ["br", "bl", "tr", "tl"];
  // The smallest the robot may be drawn where it can be tapped (core 1.3.0):
  // 44 pixels each way, tucked away or not.
  const TAP_LEAST = 44;
  // The tucked-away robot's scale with keepInViewport: `scale` (0.4 if not
  // given), but never below a 44-pixel tap target, and never above 1.
  function keepScale(scale, size) {
    const want = Math.max(0.2, Math.min(1, Number(scale) || 0.4));
    const least = size ? TAP_LEAST / Math.max(1, Math.min(size.width || 0, size.height || 0)) : 0;
    return Math.min(1, Math.max(want, least));
  }
  // The order to try corners in: the chosen one, the other side of the same
  // edge, then the other edge.
  function cornerOrder(corner) {
    const c = CORNERS.includes(corner) ? corner : "br";
    const flipSide = `${c[0]}${c[1] === "r" ? "l" : "r"}`;
    const flipEdge = `${c[0] === "b" ? "t" : "b"}${c[1]}`;
    const both = `${flipEdge[0]}${flipSide[1]}`;
    return [c, flipSide, flipEdge, both];
  }

  // Phones (core 1.4.0): up to this width the top corners are the last
  // choice. A phone's top corners hold its header (the logo, a "Home" link,
  // a menu button) and sit under the thumb's least reach.
  const NARROW_MOST = 720;
  // How much covering something costs when no corner is free (core 1.4.0).
  // A link, a button or a box: never, while any corner avoids one (WEIGHT
  // .control times the share of it covered, at least a tenth: covering most
  // of a small "Home" link is far worse than a corner of a big card). A
  // header or bar that stays put (fixed or sticky): a lot. Something the
  // app asked it to avoid: a lot more than words. A busy top corner on a
  // phone: more than anything at the bottom, so it tucks there only when
  // the bottom corners can't be used at all (a box being typed in).
  // Words and small pictures: their area in square pixels.
  const WEIGHT = { control: 1e6, block: 1e5, pinned: 5e4, top: 1e7 };
  // How near a link, a button or a box counts as under the robot on a
  // computer (core 1.4.2: contentIn's pad).
  const CONTROL_PAD = 8;
  // What touching a link, a button or a box costs at the least (costOf: a
  // tenth of WEIGHT.control), or one thing the app asked it to avoid. On a
  // computer (core 1.4.2) the robot hides rather than pay this much.
  const TOUCH = WEIGHT.control * 0.1;

  // A corner's room from the screen's edges (core 1.4.2): {x, y}, x from its
  // side edge, y from its top or bottom edge. gap: a number (both ways, as
  // before), or {x, b, t}: the side room and the room the app really gives
  // the bottom and the top corners (placeNow reads them from the robot's own
  // place, so a corner an app raises above a bottom tab bar is measured
  // there).
  function gapAt(gap, corner) {
    if (gap && typeof gap === "object") {
      const x = Number.isFinite(gap.x) ? gap.x : 16;
      const y = corner[0] === "t" ? gap.t : gap.b;
      return { x, y: Number.isFinite(y) ? y : x };
    }
    const g = gap === undefined ? 16 : gap;
    return { x: g, y: g };
  }

  // The robot's box in a corner. view: {width, height}; size: {width, height};
  // gap: as gapAt.
  function cornerBox(corner, view, size, gap) {
    const g = gapAt(gap, corner);
    const left = corner[1] === "l" ? g.x : view.width - g.x - size.width;
    const top = corner[0] === "t" ? g.y : view.height - g.y - size.height;
    return { left, top, right: left + size.width, bottom: top + size.height };
  }

  const overlaps = (a, b, pad) => {
    const p = pad || 0;
    return a.left - p < b.right && a.right + p > b.left && a.top - p < b.bottom && a.bottom + p > b.top;
  };

  // The part of the robot still showing while it's tucked away in a corner:
  // a slice `show` pixels wide at the screen's edge (pilot.css moves it by
  // the same amount: --pilot-peek-show).
  // keep: {scale, margin} with keepInViewport. Then nothing hangs off the
  // edge: the robot shrinks to `scale` in its corner, `margin` from the
  // screen's edges, and this is the small robot's box. It never shrinks
  // below a tap target of TAP_LEAST pixels each way (core 1.3.0): pilot.css
  // keeps --pilot-peek-scale at or above that too.
  // Core 1.4.2: keep.pull, how far pilot.css moves the small robot toward
  // its top or bottom edge (--pilot-keep-dy: the core's own room, never the
  // extra an app adds to raise its corners). With it the box is measured
  // from the corner's own room (gap, as gapAt), so a corner raised above a
  // tab bar is measured above the bar, where it is drawn, never over it.
  // Without it, `margin` from the edge, as before.
  function peekBox(corner, view, size, gap, show, keep) {
    if (keep) {
      const s = keepScale(keep.scale, size);
      const m = Math.max(0, Number(keep.margin) || 0);
      const w = size.width * s;
      const h = size.height * s;
      const pull = Number(keep.pull);
      const y = Number.isFinite(pull) ? Math.max(m, gapAt(gap, corner).y - Math.max(0, pull)) : m;
      const left = corner[1] === "l" ? m : view.width - m - w;
      const top = corner[0] === "t" ? y : view.height - y - h;
      return { left, top, right: left + w, bottom: top + h };
    }
    const box = cornerBox(corner, view, size, gap);
    const w = Math.max(1, Math.min(size.width, show || size.width / 2));
    return corner[1] === "l" ? { ...box, left: 0, right: w } : { ...box, left: view.width - w, right: view.width };
  }

  // How much of box b lies inside box a, in square pixels.
  const areaOf = (b, a) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
    * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));

  // A box grown by x and y pixels: the room the robot keeps from the page's
  // words (its hand reaches past its middle).
  const grow = (b, x, y) => ({ left: b.left - x, top: b.top - y, right: b.right + x, bottom: b.bottom + y });

  // Is this view a phone's (core 1.4.0)? Then top corners come last.
  const isNarrow = (view) => Boolean(view && view.width > 0 && view.width <= NARROW_MOST);

  // The corners in the order to try them, as passes: [[corners], ...]. On a
  // phone (core 1.4.0) the bottom corners are tried first, whole and then
  // tucked away, before any top corner, unless the visitor dragged it to a
  // top corner themselves (then that corner still comes first).
  function cornerPasses(corner, narrow) {
    const order = cornerOrder(corner);
    if (!narrow || order[0][0] === "t") return [order];
    return [order.filter((c) => c[0] === "b"), order.filter((c) => c[0] === "t")];
  }

  // Where to sit: {corner, peek}.
  //   blocks: boxes to keep clear of (things to avoid, another helper);
  //   busy(box, corner, small): true when the page's words or buttons are
  //     under that box (contentIn), asked only for the corners it needs
  //     (small: the tucked-away slice);
  //   hard: boxes never to cover, not even tucked away (the box being typed in).
  //   cost(box, corner): optional (core 1.3.0): how much of the page's words
  //     and buttons that tucked-away box covers (core 1.4.0: weighted, so a
  //     link or button costs far more than words: placeNow's costOf).
  //   narrow (core 1.4.0): a phone's view (isNarrow): top corners come last,
  //     and cost WEIGHT.top more when nothing is free. On a computer (core
  //     1.4.2) a top corner costs WEIGHT.top more too when it would touch a
  //     link or a button (the header's links: TOUCH or more), so it never
  //     parks there while a bottom corner covers less.
  //   awayAt (core 1.4.2): with `away`, the least cost at which it hides
  //     (WEIGHT.pinned unless given; placeNow gives TOUCH on a computer).
  //   tuck (core 1.4.0): don't sit whole anywhere, only tucked away (the first
  //     look on a small screen).
  //   away (core 1.4.1): it may fade out instead (placeNow allows it on a
  //     phone and while a box is typed in);
  //   stay (core 1.4.1): the corner it is in now, kept while it is hidden.
  // The chosen corner if it's free, else the first free one. If none is
  // free it tucks half away (peek), in the first corner where the slice that
  // still shows covers nothing; failing that, of the corners clear of the
  // hard boxes, the one whose tucked-away box costs least (with `cost`), or
  // else the first; failing that, the chosen corner.
  // Core 1.4.1: with `away`, when the least-cost corner would still cover a
  // link, a button, a bar that stays put or something to avoid (WEIGHT.pinned
  // or more), or every corner is on the box being typed in, it hides
  // instead: {corner, peek: true, hide: true}. It never parks over a phone's
  // header (CyberLogix's and Genesis's 3,3) or over an avoided control.
  function choosePlace(opts) {
    const o = opts || {};
    const blocks = (o.blocks || []).filter(Boolean);
    const hard = (o.hard || []).filter(Boolean);
    const busy = typeof o.busy === "function" ? o.busy : () => false;
    const narrow = o.narrow === undefined ? false : Boolean(o.narrow);
    const passes = cornerPasses(o.corner, narrow);
    const order = passes.flat();
    const clear = (box, list, pad) => !list.some((b) => overlaps(box, b, pad));
    const cost = typeof o.cost === "function" ? o.cost : null;
    for (const pass of passes) {
      if (!o.tuck) {
        for (const c of pass) {
          const box = cornerBox(c, o.view, o.size, o.gap);
          if (clear(box, hard, 8) && clear(box, blocks, 6) && !busy(box, c)) return { corner: c, peek: false };
        }
      }
      for (const c of pass) {
        const slice = peekBox(c, o.view, o.size, o.gap, o.peekShow, o.keep);
        if (clear(slice, hard, 8) && clear(slice, blocks, 2) && !busy(slice, c, true)) return { corner: c, peek: true };
      }
      // A phone's bottom corners (core 1.4.0): tucked over a few words or a
      // small picture is still better than any top corner; over a link, a
      // button, a bar that stays put or something to avoid, it isn't.
      if (cost && passes.length > 1 && pass === passes[0]) {
        let light = null;
        for (const c of pass) {
          const slice = peekBox(c, o.view, o.size, o.gap, o.peekShow, o.keep);
          if (!clear(slice, hard, 8) || !clear(slice, blocks, 0)) continue;
          const n = cost(slice, c);
          if (n < WEIGHT.pinned && (!light || n < light.n)) light = { corner: c, n };
        }
        if (light) return { corner: light.corner, peek: true };
      }
    }
    const mine = CORNERS.includes(o.corner) ? o.corner : "br";
    // Hidden (core 1.4.1): it keeps its corner if that is on the chosen
    // edge, so it comes back where it was.
    const gone = () => ({ corner: o.stay && CORNERS.includes(o.stay) && o.stay[0] === order[0][0] ? o.stay : order[0], peek: true, hide: true });
    if (hard.length || cost) {
      let best = null;
      for (const c of order) {
        const slice = peekBox(c, o.view, o.size, o.gap, o.peekShow, o.keep);
        if (!clear(slice, hard, 8)) continue;
        if (!cost) return { corner: c, peek: true };
        // Something the app asked it to avoid: a lot, and more the more of it.
        const n = cost(slice, c) + blocks.filter((b) => overlaps(slice, b, 0)).reduce((sum, b) => sum + WEIGHT.block + areaOf(b, slice), 0)
          + (c[0] === "t" && c !== mine && (narrow || cost(slice, c) >= TOUCH) ? WEIGHT.top : 0);
        if (!best || n < best.n) best = { corner: c, n };
      }
      const most = Number.isFinite(o.awayAt) ? o.awayAt : WEIGHT.pinned;
      if (o.away && (!best || (cost && best.n >= most))) return gone();
      if (best) return { corner: best.corner, peek: true };
    }
    return { corner: mine, peek: true };
  }

  // Where to sit on a phone once the page is scrolled (core 1.4.1): always
  // tucked away, in one corner of the chosen edge (the bottom, unless the
  // visitor dragged it to the top), kept there while the page moves under
  // it. It moves only when that corner would cover something pressed (a
  // link, a button, a bar that stays put: cost WEIGHT.pinned or more) or
  // something to avoid; words under it are fine. Then (with `full`, once the
  // scrolling stops) the other corner of the same edge; else (with `away`)
  // it hides until there is room. It never goes to the other edge while
  // scrolled. Before 1.4.1 it took any free corner, whole, every
  // time the scrolling stopped: it changed corner every 100 px or so and sat
  // whole in a top corner over a card. o: as choosePlace, plus `stay` (the
  // corner it is in now) and `full`.
  function scrollPlace(o) {
    const order = cornerOrder(o.corner);
    const edge = order[0][0];
    const here = o.stay && CORNERS.includes(o.stay) && o.stay[0] === edge ? o.stay : order[0];
    const there = `${edge}${here[1] === "r" ? "l" : "r"}`;
    const blocks = (o.blocks || []).filter(Boolean);
    const cost = typeof o.cost === "function" ? o.cost : null;
    const slice = (c) => peekBox(c, o.view, o.size, o.gap, o.peekShow, o.keep);
    const clear = (box) => !blocks.some((b) => overlaps(box, b, 2));
    const fine = (c) => { const s = slice(c); return clear(s) && (!cost || cost(s, c) < WEIGHT.pinned); };
    if (fine(here)) return { corner: here, peek: true };
    if (o.full && fine(there)) return { corner: there, peek: true };
    return o.away ? { corner: here, peek: true, hide: true } : { corner: here, peek: true };
  }

  // ---------------------------------------------------------------- the page's own words and buttons

  // Things a visitor reads or presses. The robot keeps off them.
  const INTERACTIVE = 'a[href], button, input:not([type="hidden"]), textarea, select, summary, label, [role="button"], '
    + '[role="link"], [role="checkbox"], [role="tab"], [role="switch"], [contenteditable=""], [contenteditable="true"]';
  // Small pictures count too (icons, logos); big ones are scenery.
  const MEDIA = /^(IMG|SVG|VIDEO|CANVAS|PICTURE)$/i;
  const ICON_MOST = 160;
  const MEANS = '[title]:not(html):not(body), [role="img"], [role="progressbar"], [role="meter"], [data-pilot-clear]';
  const NOT_TYPED = /^(button|submit|reset|checkbox|radio|range|color|file|image|hidden)$/i;

  // Is this a box someone types into: a text input, a textarea or an
  // editable element?
  function isEditable(el) {
    if (!el || el.nodeType !== 1) return false;
    const tag = String(el.tagName || "").toUpperCase();
    if (tag === "TEXTAREA") return !el.disabled && !el.readOnly;
    if (tag === "INPUT") return !el.disabled && !el.readOnly && !NOT_TYPED.test(el.type || el.getAttribute("type") || "text");
    return Boolean(el.isContentEditable);
  }

  // A box, and (core 1.4.0) what kind of thing it is: "control" (a link, a
  // button, a box: something pressed), "media" (a small picture), "mark" (a
  // small thing that means something), "text" (words) or "pinned" (a header
  // or bar that stays put).
  const toBox = (r, kind) => ({ left: r.left, top: r.top, right: r.right, bottom: r.bottom, kind: kind || "text" });

  // The bar that stays put which this element is in (core 1.4.0): its
  // nearest ancestor (or itself) that is position: fixed or sticky and
  // smaller than PINNED_MOST of the screen (a header, a tab bar, a cookie
  // note; never a whole-screen layer, which is a pop-up's backdrop or the
  // app's own frame), or null. memo: a Map kept for one look, so each
  // element's style is read once.
  const PINNED_MOST = 0.4;
  function pinnedBar(el, own, win, memo) {
    const w = win || root;
    if (!w || typeof w.getComputedStyle !== "function") return null;
    const seen = [];
    let found = null;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      if (memo && memo.has(n)) { found = memo.get(n); break; }
      seen.push(n);
      const tag = String(n.tagName || "").toUpperCase();
      if (tag === "BODY" || tag === "HTML" || (own && own.contains(n))) break;
      const st = w.getComputedStyle(n);
      if (st && (st.position === "fixed" || st.position === "sticky")) {
        const r = n.getBoundingClientRect();
        const screen = Math.max(1, (w.innerWidth || 0) * (w.innerHeight || 0));
        if (r.width > 0 && r.height > 0 && (r.width * r.height) / screen <= PINNED_MOST) { found = n; break; }
        if (st.position === "fixed") break; // a whole-screen layer: what's in it isn't a bar
      }
    }
    if (memo) for (const n of seen) memo.set(n, found);
    return found;
  }

  // The part of one element (the top one at a point) that is something to
  // read or press inside `area`, or null. (Core 1.4.0: with its `kind`; and
  // the empty part of a header or bar that stays put counts too, as
  // "pinned".)
  function contentBox(el, area, own, doc, memo) {
    if (!el || el.nodeType !== 1) return null;
    const tag = String(el.tagName || "").toUpperCase();
    if (tag === "HTML" || tag === "BODY") return null;
    const control = el.closest ? el.closest(INTERACTIVE) : null;
    if (control && !(own && own.contains(control))) {
      const r = control.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && overlaps(r, area, 0)) return toBox(r, "control");
    }
    const bar = () => {
      const b = pinnedBar(el, own, (doc && doc.defaultView) || root, memo);
      if (!b) return null;
      const r = b.getBoundingClientRect();
      return overlaps(r, area, 0) ? toBox(r, "pinned") : null;
    };
    if (MEDIA.test(tag)) {
      const r = el.getBoundingClientRect();
      if (r.width <= ICON_MOST && r.height <= ICON_MOST && overlaps(r, area, 0)) return toBox(r, "media");
      return bar();
    }
    // Small things that mean something without words (a row of streak
    // dots, a status light): anything with a tooltip or a picture role,
    // or marked data-pilot-clear.
    const marked = el.closest ? el.closest(MEANS) : null;
    if (marked && !(own && own.contains(marked))) {
      const r = marked.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.width <= ICON_MOST * 3 && r.height <= ICON_MOST && overlaps(r, area, 0)) return toBox(r, "mark");
    }
    // Words written straight into this element: only the lines that are
    // really there count, not the empty end of a short line.
    const range = doc && doc.createRange ? doc.createRange() : null;
    if (range) {
      for (const node of el.childNodes || []) {
        if (node.nodeType !== 3 || !/\S/.test(node.nodeValue)) continue;
        range.selectNodeContents(node);
        for (const r of range.getClientRects()) {
          if (r.width > 0 && r.height > 0 && overlaps(r, area, 0)) return toBox(r, "text");
        }
      }
    }
    return bar();
  }

  // What covering these content boxes costs, from inside `box` (core 1.4.0):
  // a link, a button or a box WEIGHT.control times the share covered (at
  // least a tenth), a bar that stays put WEIGHT.pinned, words and small
  // pictures their area.
  // Core 1.4.1: the share is the larger of the share of the link covered and
  // the share of the robot's own box that lies over the link. The corner of
  // a big card link under the whole robot is as bad as a small "Home" link:
  // a tap there could mean either (1.4.0 counted only the card's share, a
  // tenth, so the robot tucked over Orbyt's path cards).
  function costOf(hits, box) {
    const mine = box ? Math.max(1, (box.right - box.left) * (box.bottom - box.top)) : 1;
    return (hits || []).reduce((sum, b) => {
      const area = areaOf(b, box);
      const whole = Math.max(1, (b.right - b.left) * (b.bottom - b.top));
      const share = Math.max(area / whole, box ? area / mine : 0);
      const extra = b.kind === "control" ? WEIGHT.control * Math.max(0.1, Math.min(1, share)) : b.kind === "pinned" ? WEIGHT.pinned : 0;
      return sum + extra + area;
    }, 0);
  }

  // What the page shows to read or press under `area` (the robot's box in a
  // corner, in screen pixels): the boxes of those things, [] when it's free.
  // Looks at a grid of points (the top element at each one, never Pilot's
  // own), so it costs a handful of lookups, not a walk over the page.
  // pad (core 1.4.2): links, buttons and boxes count within this many
  // pixels around the area too (placeNow: CONTROL_PAD on a computer), so the
  // robot never sits touching one (Genesis's "Save my spark" by 7 px, the
  // header's last link at the top right of Orbyt at 1024 px); words and bars
  // still count only under the area itself.
  function contentIn(doc, area, own, grid, pad) {
    const d = doc || root.document;
    if (!d || typeof d.elementsFromPoint !== "function" || !area) return [];
    const p = Math.max(0, Number(pad) || 0);
    const n = Math.max(2, grid || 4, p ? 5 : 0);
    const exact = { left: area.left + 3, top: area.top + 3, right: area.right - 3, bottom: area.bottom - 3 };
    const inset = p ? grow(area, p, p) : exact; // the full pad from the area's own edge
    const out = [];
    const seen = new Set();
    const memo = new Map();
    for (let i = 0; i < n; i += 1) {
      for (let j = 0; j < n; j += 1) {
        // From edge to edge, so a line just inside the box is found too.
        const x = inset.left + ((inset.right - inset.left) * i) / (n - 1);
        const y = inset.top + ((inset.bottom - inset.top) * j) / (n - 1);
        let stack = [];
        try { stack = d.elementsFromPoint(x, y) || []; } catch (_) { continue; }
        const top = stack.find((el) => !(own && own.contains(el)));
        if (!top || seen.has(top)) continue;
        seen.add(top);
        const hit = contentBox(top, inset, own, d, memo);
        if (hit && p && hit.kind !== "control" && !overlaps(hit, exact, 0)) continue;
        // The same thing found at two points counts once (core 1.4.0).
        const key = hit && `${hit.kind}:${hit.left},${hit.top},${hit.right},${hit.bottom}`;
        if (hit && !seen.has(key)) { seen.add(key); out.push(hit); }
      }
    }
    return out;
  }

  // The boxes of the visible elements matching any of the selectors.
  function boxesOf(doc, selectors, own, win) {
    const out = [];
    for (const sel of selectors || []) {
      let list = [];
      try { list = (doc || root.document).querySelectorAll(sel); } catch (_) { continue; }
      for (const el of list) {
        if (own && own.contains(el)) continue;
        if (!shown(el, win)) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) out.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
      }
    }
    return out;
  }

  // ---------------------------------------------------------------- where to sit now (pilot.js asks)

  // The room the robot's corners have from the bottom and the top edge (core
  // 1.4.2): {x, b, t} for gapAt. Read from the robot's own place (its root's
  // `bottom` or `top`, as the page's styles set it: an app's raised corner,
  // the phone's safe area) for the edge it is on now, and kept for the
  // other edge, so a corner is measured the same whichever one it is in
  // (no flipping between them). Until it has been on an edge, `side`
  // (--pilot-gap), as before.
  const rooms = new WeakMap();
  function edgeRoom(own, css, side, shown) {
    const keyed = own && typeof own === "object";
    const seen = (keyed && rooms.get(own)) || {};
    const edge = CORNERS.includes(shown) ? shown[0] : "";
    const v = edge && css ? parseFloat(edge === "t" ? css.top : css.bottom) : NaN;
    if (Number.isFinite(v) && v >= 0) {
      seen[edge] = v;
      if (keyed) rooms.set(own, seen);
    }
    return { x: side, b: Number.isFinite(seen.b) ? seen.b : side, t: Number.isFinite(seen.t) ? seen.t : side };
  }

  // The box being typed in (an input, a textarea or an editable element of
  // the page, never Pilot's own), or null.
  function typingIn(doc, own) {
    const el = doc.activeElement;
    return el && el !== doc.body && !(own && own.contains(el)) && isEditable(el) ? el : null;
  }

  // Where the robot should sit now: {corner, peek, typing}. o: {doc, win,
  // own (Pilot's root), launcher, avoid (selectors), corner (the visitor's
  // own), shown (where it is now), full, text}. With `full` (not mid-scroll)
  // it may move to another corner; mid-scroll it only checks its own spot
  // and tucks away there or comes back out, so it never hops about. A box
  // being typed in is never covered (hard). `text`: keep off the page's
  // words and buttons (contentIn).
  // `keepIn` (keepInViewport): when tucked away it shrinks into its corner,
  // fully on screen (--pilot-peek-scale, --pilot-keep-margin), instead of
  // hanging off the edge. (The first look of a visit plays its glow
  // wherever this puts the robot: whole in a free corner, or tucked away.)
  // Core 1.4.0: on a phone (isNarrow) the top corners come last; with no
  // corner free, covering a link or button costs most (costOf), so it never
  // tucks over one while another corner avoids one; `tuck`: tucked away
  // only (pilot.js asks for that on a small screen's first look, until the
  // visitor scrolls).
  // Core 1.4.1: `scrolled` (pilot.js: the page is scrolled away from its
  // top): on a phone it keeps to one bottom corner, tucked away
  // (scrollPlace). `away` (unless false): on a phone, or while a box is
  // typed in, it hides rather than cover a link, a button, a bar that stays
  // put or something to avoid: the answer then has `hide: true`. Core
  // 1.4.2: on a computer too, rather than touch a link, a button or
  // something to avoid (within CONTROL_PAD); pilot.js then offers the handle.
  function placeNow(o) {
    const blocks = boxesOf(o.doc, o.avoid, o.own, o.win);
    const field = typingIn(o.doc, o.own);
    // Its own size, not as drawn: tucked away it may be shrunk (keepIn).
    const r = o.launcher.getBoundingClientRect();
    const size = { width: o.launcher.offsetWidth || r.width, height: o.launcher.offsetHeight || r.height };
    const view = { width: o.win.innerWidth, height: o.win.innerHeight };
    const css = o.win.getComputedStyle(o.own);
    const num = (name, dflt) => { const v = parseFloat(css.getPropertyValue(name)); return Number.isFinite(v) ? v : dflt; };
    const side = num("--pilot-gap", 16);
    // Core 1.4.2: the room its bottom (or top) corners really have, read
    // from its own place (an app may raise them above a tab bar), and how far
    // pilot.css pulls the tucked robot toward that edge: the core's own room
    // (--pilot-keep-room), never the app's extra.
    const gap = edgeRoom(o.own, css, side, o.shown);
    const peekShow = num("--pilot-peek-show", size.width / 2);
    const margin = num("--pilot-keep-margin", 4);
    const keep = o.keepIn ? { scale: num("--pilot-peek-scale", 0.4), margin, pull: Math.min(side, num("--pilot-keep-room", side)) - margin } : null;
    // The room kept from the page's words: a little more across than up and
    // down, where the robot's hand reaches.
    const cx = num("--pilot-clear-x", 12);
    const cy = num("--pilot-clear-y", 4);
    // (The small tucked-away slice is checked as it is: it sits in the
    // page's side room.)
    // Core 1.4.2: on a computer, links and buttons count within CONTROL_PAD.
    const narrow = isNarrow(view);
    const pad = narrow ? 0 : CONTROL_PAD;
    const busy = o.text ? (box, c, small) => contentIn(o.doc, small ? box : grow(box, cx, cy), o.own, 4, pad).length > 0 : undefined;
    // With nowhere free, how much of the page the tucked-away robot would
    // cover in a corner: the least is best (core 1.3.0).
    const cost = o.text ? (box) => costOf(contentIn(o.doc, box, o.own, 4, pad), box) : undefined;
    // Core 1.4.2: on a computer too, but only rather than touch a link, a
    // button or something to avoid (a bar that stays put is fine there).
    const away = o.away !== false;
    const awayAt = narrow || field ? WEIGHT.pinned : TOUCH;
    let place;
    if (o.scrolled && narrow && !field) {
      place = scrollPlace({ corner: o.corner, stay: o.shown, view, size, gap, blocks, busy, cost, peekShow, keep, full: Boolean(o.full), away });
    } else if (o.full || field) {
      place = choosePlace({ corner: o.corner, view, size, gap, blocks, hard: field ? [field.getBoundingClientRect()] : [], busy, cost, peekShow, keep,
        narrow, tuck: Boolean(o.tuck), away, awayAt, stay: o.shown });
    } else {
      const spot = cornerBox(o.shown, view, size, gap);
      place = { corner: o.shown, peek: Boolean(o.tuck) || blocks.some((b) => overlaps(spot, b, 6)) || Boolean(busy && busy(spot)) };
    }
    // The first look (core 1.3.0) follows the same rules: a free corner if
    // there is one, else it glows where it is tucked away. It never sits on
    // the page's words or buttons just to be noticed.
    return { ...place, typing: Boolean(field) };
  }

  // The first look of a visit (pilot.js): how long to show the robot whole
  // before it tucks away, so people notice it: `ms` (4000 by default, 0 for
  // never, at most 10 s), but only once per visit, kept in this tab's
  // storage under `key`. Storage blocked: shown this once.
  function firstLook(tabStore, key, ms) {
    const n = ms === undefined || ms === null || !Number.isFinite(Number(ms)) ? 4000 : Math.max(0, Math.min(10000, Number(ms)));
    if (!n) return 0;
    try {
      if (tabStore && tabStore.getItem(key) === "1") return 0;
      if (tabStore) tabStore.setItem(key, "1");
    } catch (_) { /* blocked */ }
    return n;
  }

  // On phones, room at the side for the tucked-away robot: these elements
  // get .pilot-gutter-side (pilot.css: --pilot-side, 24 px), but only where
  // their own right padding is narrower. An app's own use of the class is
  // left alone.
  function sideGutter(doc, win, selectors) {
    const want = parseFloat(win.getComputedStyle(doc.documentElement).getPropertyValue("--pilot-side")) || 0;
    for (const sel of selectors || []) {
      let found = [];
      try { found = doc.querySelectorAll(sel); } catch (_) { continue; }
      for (const el of found) {
        const auto = el.hasAttribute("data-pilot-gutter-auto");
        if (el.classList.contains("pilot-gutter-side") && !auto) continue;
        el.classList.remove("pilot-gutter-side");
        el.removeAttribute("data-pilot-gutter-auto");
        if (want > 0 && (parseFloat(win.getComputedStyle(el).paddingRight) || 0) < want) {
          el.classList.add("pilot-gutter-side");
          el.setAttribute("data-pilot-gutter-auto", "");
        }
      }
    }
  }

  // Is any of these boxes where the web robot strolls (core 1.4.0): the
  // bottom of the screen, two robots tall (and a gap)? pilot.js holds its
  // wandering only then; before, anything the app asked it to avoid,
  // anywhere on the page (even far off screen), kept it still for good.
  function inStroll(boxes, win, launcher) {
    const w = win || root;
    const tall = ((launcher && launcher.offsetHeight) || 88) * 2 + 32;
    const strip = { left: 0, top: (w.innerHeight || 0) - tall, right: w.innerWidth || 0, bottom: w.innerHeight || 0 };
    return (boxes || []).some((b) => overlaps(b, strip, 0));
  }

  // A small screen (core 1.4.0): the first look plays tucked away there,
  // until the visitor scrolls (pilot.js), so a whole robot never lands on a
  // card the moment the page opens.
  const COMPACT = { width: 360, height: 700 };
  const isCompact = (view) => Boolean(view && ((view.width > 0 && view.width <= COMPACT.width) || (view.height > 0 && view.height <= COMPACT.height)));

  // Is the page scrolled away from its top (core 1.4.1; pilot.js asks on
  // every scroll)? The window, or the box that scrolled (t) when it is at
  // least half the screen tall (an app's own shell that scrolls in its place).
  function scrolledAway(win, doc, t) {
    if ((win.scrollY || win.pageYOffset || 0) > 8) return true;
    const inner = t && t.nodeType === 1 && t !== doc.documentElement && t !== doc.body ? t : null;
    return Boolean(inner && inner.clientHeight >= win.innerHeight * 0.5 && inner.scrollTop > 8);
  }

  // ---------------------------------------------------------------- the handle (core 1.4.2)

  // Hidden at rest, the robot could stay out of sight for a whole visit (at
  // the top of a page with a link in every corner, say), and a visitor might
  // never find Pilot. So after HANDLE_MS hidden with the page still (not
  // scrolling, nothing typed in), a handle shows at the screen's left or
  // right edge: a slice of the robot peeking in (pilot.css .pilot-handle), a
  // HANDLE-pixel square to tap, which opens Pilot. It goes only where that
  // whole square covers nothing pressed (a link, a button, a box), no bar
  // that stays put and nothing the app asks the robot to avoid; with no such
  // spot it stays hidden. A spot with nothing under it at all is first;
  // failing that, one with only words under its see-through part.
  const HANDLE = 44;
  const HANDLE_MS = 2000;
  const HANDLE_STEP = 12;

  // The handle's square at the `side` edge ("l" or "r"), `top` pixels down.
  function handleBox(side, top, view) {
    const left = side === "l" ? 0 : view.width - HANDLE;
    return { left, top, right: left + HANDLE, bottom: top + HANDLE };
  }

  // The boxes of the things a visitor presses that reach into a strip at the
  // left or right edge (wide enough for the handle), on screen: [{left, ...}].
  function controlsAtEdges(doc, own, win, view) {
    const out = [];
    const near = HANDLE + 8;
    let found = [];
    try { found = doc.querySelectorAll(INTERACTIVE); } catch (_) { return out; }
    for (const el of found) {
      if (own && own.contains(el)) continue;
      for (const r of (el.getClientRects ? el.getClientRects() : [])) {
        if (r.width <= 0 || r.height <= 0 || r.bottom < 0 || r.top > view.height) continue;
        if (r.left > near && r.right < view.width - near) continue;
        if (!shown(el, win)) break;
        out.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
      }
    }
    return out;
  }

  // How free the handle's square is: "free" (nothing under it), "words"
  // (only words or a small picture), or "" (something pressed, a bar that
  // stays put, or something to avoid). controls: controlsAtEdges.
  function handleRoom(o, box, controls, blocks) {
    if (controls.some((b) => overlaps(box, b, 4)) || blocks.some((b) => overlaps(box, b, 4))) return "";
    const hits = contentIn(o.doc, box, o.own, 4);
    if (hits.some((h) => h.kind === "control" || h.kind === "pinned")) return "";
    return hits.length ? "words" : "free";
  }

  // Where the handle may go: {side, top}, or null when nowhere is free. o:
  // {doc, win, own, avoid (selectors), corner (the side it is tried at
  // first)}. Each edge is tried from the bottom up (where the robot sits,
  // and the thumb reaches), the robot's own side first.
  function handleSpot(o) {
    const view = { width: o.win.innerWidth, height: o.win.innerHeight };
    if (!(view.width > HANDLE * 2 && view.height > HANDLE * 2)) return null;
    const blocks = boxesOf(o.doc, o.avoid, o.own, o.win);
    const controls = controlsAtEdges(o.doc, o.own, o.win, view);
    const first = CORNERS.includes(o.corner) ? o.corner[1] : "r";
    let words = null;
    for (const side of [first, first === "r" ? "l" : "r"]) {
      for (let top = view.height - HANDLE - 12; top >= 8; top -= HANDLE_STEP) {
        const room = handleRoom(o, handleBox(side, top, view), controls, blocks);
        if (room === "free") return { side, top };
        if (room === "words" && !words) words = { side, top };
      }
    }
    return words;
  }

  // Is the handle's spot still free (the page may have changed under it)?
  function handleFree(o, spot) {
    const view = { width: o.win.innerWidth, height: o.win.innerHeight };
    if (!spot || spot.top < 0 || spot.top + HANDLE > view.height) return false;
    return handleRoom(o, handleBox(spot.side, spot.top, view), controlsAtEdges(o.doc, o.own, o.win, view),
      boxesOf(o.doc, o.avoid, o.own, o.win)) !== "";
  }

  // The handle for pilot.js: {update(rest)}, called after each look. rest:
  // the robot is hidden (.pilot-away) and the page is still. o: {doc, win,
  // root (Pilot's root), launcher (its robot picture is used), open (opens
  // Pilot), avoid () => selectors, corner () => the corner it is in}.
  // The handle is a <button> in Pilot's root, so it steps back with the robot
  // for a page's pop-up, and placement never counts it as the page's.
  function restHandle(o) {
    const d = o.doc;
    let timer = 0;
    let btn = null;
    let spot = null;
    const ask = () => ({ doc: d, win: o.win, own: o.root, avoid: o.avoid(), corner: o.corner() });
    const off = () => { spot = null; o.root.classList.remove("pilot-handle-on"); };
    function make() {
      btn = d.createElement("button");
      btn.type = "button";
      btn.className = "pilot-handle";
      btn.setAttribute("aria-label", "Open Pilot, your AI helper");
      const pic = o.launcher && o.launcher.querySelector(".pilot-robot");
      if (pic) {
        const img = pic.cloneNode(false);
        img.className = "pilot-handle-robot";
        btn.appendChild(img);
      }
      btn.addEventListener("click", () => o.open());
      o.root.appendChild(btn);
    }
    function look() {
      timer = 0;
      if (!o.root.classList.contains("pilot-away") || typingIn(d, o.root)) return;
      spot = handleSpot(ask());
      if (!spot) { off(); return; }
      if (!btn) make();
      btn.classList.toggle("pilot-handle-l", spot.side === "l");
      btn.classList.toggle("pilot-handle-r", spot.side !== "l");
      btn.style.top = `${Math.round(spot.top)}px`; // the CSS object model: allowed by a strict CSP
      o.root.classList.add("pilot-handle-on");
    }
    return {
      update(rest) {
        if (!rest) { clearTimeout(timer); timer = 0; off(); return; }
        if (spot && !handleFree(ask(), spot)) off();
        if (!spot && !timer) timer = setTimeout(look, HANDLE_MS);
      },
      get spot() { return spot; },
    };
  }

  // The resting robot's layers for pilot-actor.js (core 1.4.1): the
  // addresses on Pilot's script tag (data-rest-body, -head, -eyes) and, with
  // data-rest-*-set, the sizes it is drawn at too ({src, srcset, sizes}), so a
  // phone shows them crisp (the 192 px files shrunk to 56 px looked soft).
  function restArt(a) {
    const layer = (url, set) => (set ? { src: url, srcset: `${set}, ${url} 192w`, sizes: SIZES } : url);
    return { body: layer(a.restBody, a.restBodySet), head: layer(a.restHead, a.restHeadSet), eyes: layer(a.restEyes, a.restEyesSet) };
  }

  const api = { VERSION, SIZES, scrolledAway, restArt, TAP_LEAST, keepScale, robot, mark, typingIn, placeNow, sideGutter, MODALS, modalOpen, covered, coverLayer, cornerOrder, cornerBox, peekBox, overlaps,
    choosePlace, scrollPlace, boxesOf, shown, grow, areaOf, firstLook, INTERACTIVE, MEANS, isEditable, contentBox, contentIn,
    NARROW_MOST, WEIGHT, isNarrow, cornerPasses, pinnedBar, costOf, COMPACT, isCompact, inStroll,
    gapAt, edgeRoom, CONTROL_PAD, TOUCH, HANDLE, HANDLE_MS, handleBox, handleSpot, handleFree, restHandle };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotMascot = api;
})(typeof window !== "undefined" ? window : globalThis);
