// Pilot core 1.0.1 · pilot-mascot.js
// The Pilot robot and the P mark.
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

  const VERSION = "1.0.1";
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
    img.setAttribute("sizes", "(max-width: 720px) 72px, 88px");
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

  const api = { VERSION, robot, mark };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotMascot = api;
})(typeof window !== "undefined" ? window : globalThis);
