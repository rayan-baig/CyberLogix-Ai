// Pilot core 1.1.1 · pilot-mascot.js
// The Pilot robot and the P mark.
//
// Also where the robot may sit (placement): it steps back while a page's
// own pop-up is open, keeps clear of things an app asks it to avoid (and of
// another helper character), and of the page's own words and buttons
// wherever it is scrolled to (contentIn), picking a free corner, or tucks
// half away at the edge until tapped. A box being typed in always comes
// first: the robot never sits on it (hard blocks).
//
// The robot is the owner's own picture (pilot-robot-128.webp and
// pilot-robot-256.webp, transparent), shown as an <img> at the size the
// screen needs. pilot.css gives it life: a gentle float, a soft blue glow
// while Pilot is thinking, and nothing moving under "reduce motion".
// The P with its orbit, for the chat window's header, is drawn as SVG with
// attributes only (no style attributes), so it keeps a strict
// Content-Security-Policy. The desktop app reuses this file and the pictures.
(function (root) {
  "use strict";

  const VERSION = "1.1.1";
  const NS = "http://www.w3.org/2000/svg";
  let made = 0;

  function el(doc, name, attrs, kids) {
    const node = doc.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
    for (const kid of kids || []) node.appendChild(kid);
    return node;
  }

  // The robot picture. urls: {small, large} (the 128 and 256 pixel files).
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
    if (u.large) img.setAttribute("srcset", `${u.small} 128w, ${u.large} 256w`);
    img.setAttribute("sizes", "(max-width: 720px) 56px, 88px");
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

  // Is a pop-up of the page showing? own: Pilot's root (its window doesn't count).
  function modalOpen(doc, own, win) {
    const d = doc || root.document;
    for (const el of d.querySelectorAll(MODALS)) {
      if (own && own.contains(el)) continue;
      if (shown(el, win)) return true;
    }
    return false;
  }

  const CORNERS = ["br", "bl", "tr", "tl"];
  // The order to try corners in: the chosen one, the other side of the same
  // edge, then the other edge.
  function cornerOrder(corner) {
    const c = CORNERS.includes(corner) ? corner : "br";
    const flipSide = `${c[0]}${c[1] === "r" ? "l" : "r"}`;
    const flipEdge = `${c[0] === "b" ? "t" : "b"}${c[1]}`;
    const both = `${flipEdge[0]}${flipSide[1]}`;
    return [c, flipSide, flipEdge, both];
  }

  // The robot's box in a corner. view: {width, height}; size: {width, height}.
  function cornerBox(corner, view, size, gap) {
    const g = gap === undefined ? 16 : gap;
    const left = corner[1] === "l" ? g : view.width - g - size.width;
    const top = corner[0] === "t" ? g : view.height - g - size.height;
    return { left, top, right: left + size.width, bottom: top + size.height };
  }

  const overlaps = (a, b, pad) => {
    const p = pad || 0;
    return a.left - p < b.right && a.right + p > b.left && a.top - p < b.bottom && a.bottom + p > b.top;
  };

  // The part of the robot still showing while it's tucked away in a corner:
  // a slice `show` pixels wide at the screen's edge (pilot.css moves it by
  // the same amount: --pilot-peek-show).
  function peekBox(corner, view, size, gap, show) {
    const box = cornerBox(corner, view, size, gap);
    const w = Math.max(1, Math.min(size.width, show || size.width / 2));
    return corner[1] === "l" ? { ...box, left: 0, right: w } : { ...box, left: view.width - w, right: view.width };
  }

  // Where to sit: {corner, peek}.
  //   blocks: boxes to keep clear of (things to avoid, another helper);
  //   busy(box): true when the page's words or buttons are under that box
  //     (contentIn), asked only for the corners it needs;
  //   hard: boxes never to cover, not even tucked away (the box being typed in).
  // The chosen corner if it's free, else the first free one. If none is
  // free it tucks half away (peek), in the first corner where the slice that
  // still shows covers nothing; failing that, the first corner clear of the
  // hard boxes; failing that, the chosen corner.
  function choosePlace(opts) {
    const o = opts || {};
    const blocks = (o.blocks || []).filter(Boolean);
    const hard = (o.hard || []).filter(Boolean);
    const busy = typeof o.busy === "function" ? o.busy : () => false;
    const order = cornerOrder(o.corner);
    const clear = (box, list, pad) => !list.some((b) => overlaps(box, b, pad));
    for (const c of order) {
      const box = cornerBox(c, o.view, o.size, o.gap);
      if (clear(box, hard, 8) && clear(box, blocks, 6) && !busy(box, c)) return { corner: c, peek: false };
    }
    for (const c of order) {
      const slice = peekBox(c, o.view, o.size, o.gap, o.peekShow);
      if (clear(slice, hard, 8) && clear(slice, blocks, 2) && !busy(slice, c)) return { corner: c, peek: true };
    }
    if (hard.length) {
      for (const c of order) {
        if (clear(peekBox(c, o.view, o.size, o.gap, o.peekShow), hard, 8)) return { corner: c, peek: true };
      }
    }
    return { corner: CORNERS.includes(o.corner) ? o.corner : "br", peek: true };
  }

  // ---------------------------------------------------------------- the page's own words and buttons

  // Things a visitor reads or presses. The robot keeps off them.
  const INTERACTIVE = 'a[href], button, input:not([type="hidden"]), textarea, select, summary, label, [role="button"], '
    + '[role="link"], [role="checkbox"], [role="tab"], [role="switch"], [contenteditable=""], [contenteditable="true"]';
  // Small pictures count too (icons, logos); big ones are scenery.
  const MEDIA = /^(IMG|SVG|VIDEO|CANVAS|PICTURE)$/i;
  const ICON_MOST = 160;
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

  const toBox = (r) => ({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });

  // The part of one element (the top one at a point) that is something to
  // read or press inside `area`, or null.
  function contentBox(el, area, own, doc) {
    if (!el || el.nodeType !== 1) return null;
    const tag = String(el.tagName || "").toUpperCase();
    if (tag === "HTML" || tag === "BODY") return null;
    const control = el.closest ? el.closest(INTERACTIVE) : null;
    if (control && !(own && own.contains(control))) {
      const r = control.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && overlaps(r, area, 0)) return toBox(r);
    }
    if (MEDIA.test(tag)) {
      const r = el.getBoundingClientRect();
      if (r.width <= ICON_MOST && r.height <= ICON_MOST && overlaps(r, area, 0)) return toBox(r);
      return null;
    }
    // Words written straight into this element: only the lines that are
    // really there count, not the empty end of a short line.
    const range = doc && doc.createRange ? doc.createRange() : null;
    if (!range) return null;
    for (const node of el.childNodes || []) {
      if (node.nodeType !== 3 || !/\S/.test(node.nodeValue)) continue;
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (r.width > 0 && r.height > 0 && overlaps(r, area, 0)) return toBox(r);
      }
    }
    return null;
  }

  // What the page shows to read or press under `area` (the robot's box in a
  // corner, in screen pixels): the boxes of those things, [] when it's free.
  // Looks at a grid of points (the top element at each one, never Pilot's
  // own), so it costs a handful of lookups, not a walk over the page.
  function contentIn(doc, area, own, grid) {
    const d = doc || root.document;
    if (!d || typeof d.elementsFromPoint !== "function" || !area) return [];
    const n = Math.max(2, grid || 4);
    const inset = { left: area.left + 3, top: area.top + 3, right: area.right - 3, bottom: area.bottom - 3 };
    const out = [];
    const seen = new Set();
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
        const hit = contentBox(top, inset, own, d);
        if (hit) out.push(hit);
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
  function placeNow(o) {
    const blocks = boxesOf(o.doc, o.avoid, o.own, o.win);
    const field = typingIn(o.doc, o.own);
    const r = o.launcher.getBoundingClientRect();
    const size = { width: r.width, height: r.height };
    const view = { width: o.win.innerWidth, height: o.win.innerHeight };
    const css = o.win.getComputedStyle(o.own);
    const gap = parseFloat(css.getPropertyValue("--pilot-gap")) || 16;
    const peekShow = parseFloat(css.getPropertyValue("--pilot-peek-show")) || size.width / 2;
    const busy = o.text ? (box) => contentIn(o.doc, box, o.own).length > 0 : undefined;
    let place;
    if (o.full || field) {
      place = choosePlace({ corner: o.corner, view, size, gap, blocks, hard: field ? [field.getBoundingClientRect()] : [], busy, peekShow });
    } else {
      const spot = cornerBox(o.shown, view, size, gap);
      place = { corner: o.shown, peek: blocks.some((b) => overlaps(spot, b, 6)) || Boolean(busy && busy(spot)) };
    }
    return { ...place, typing: Boolean(field) };
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

  const api = { VERSION, robot, mark, typingIn, placeNow, sideGutter, MODALS, modalOpen, cornerOrder, cornerBox, peekBox, overlaps, choosePlace, boxesOf, shown,
    INTERACTIVE, isEditable, contentBox, contentIn };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotMascot = api;
})(typeof window !== "undefined" ? window : globalThis);
