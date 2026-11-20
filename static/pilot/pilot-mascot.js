// Pilot core 1.1.0 · pilot-mascot.js
// The Pilot robot and the P mark.
//
// Also where the robot may sit (placement): it steps back while a page's
// own pop-up is open, keeps clear of things an app asks it to avoid (and of
// another helper character), picking a free corner, or tucks half away at
// the edge until tapped.
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

  const VERSION = "1.1.0";
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

  // Where to sit: {corner, peek}. blocks: the boxes to keep clear of (things
  // to avoid, another helper). The chosen corner if it's free, else the first
  // free one; if none is free, the chosen corner, tucked half away (peek).
  function choosePlace(opts) {
    const o = opts || {};
    const blocks = (o.blocks || []).filter(Boolean);
    for (const c of cornerOrder(o.corner)) {
      const box = cornerBox(c, o.view, o.size, o.gap);
      if (!blocks.some((b) => overlaps(box, b, 6))) return { corner: c, peek: false };
    }
    return { corner: CORNERS.includes(o.corner) ? o.corner : "br", peek: true };
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

  const api = { VERSION, robot, mark, MODALS, modalOpen, cornerOrder, cornerBox, overlaps, choosePlace, boxesOf, shown };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotMascot = api;
})(typeof window !== "undefined" ? window : globalThis);
