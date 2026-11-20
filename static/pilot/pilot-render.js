// Pilot core 1.1.0 · pilot-render.js
// A tiny, safe markdown renderer for Pilot's answers.
//
// An AI answer is untrusted text: it can contain anything, including words
// copied from a web page. So nothing here ever uses innerHTML. `parse` turns
// the text into a small tree of plain objects (paragraphs, lists, code,
// bold, italic, links), and `render` builds DOM nodes from that tree with
// createElement and text nodes only. "<script>" in an answer shows as the
// letters "<script>". Links work only for http(s) addresses and open in a
// new tab without the page's address.
//
// `parse` needs no DOM (Node tests use it); `render` takes a document.
(function (root) {
  "use strict";

  const VERSION = "1.1.0";
  const MAX = 40000;

  // Inline marks in one line of text: `code`, **bold**, *italic* or _italic_,
  // and [words](https://address). Returns [{t: "text"|"code"|"b"|"i"|"a", ...}].
  function inline(text) {
    const out = [];
    let rest = text;
    const pattern = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[[^\]\n]{1,200}\]\((https?:\/\/[^\s)]{1,500})\))|(\*[^*\s][^*\n]*\*)|(\b_[^_\s][^_\n]*_\b)/;
    while (rest) {
      const m = rest.match(pattern);
      if (!m) { out.push({ t: "text", v: rest }); break; }
      if (m.index > 0) out.push({ t: "text", v: rest.slice(0, m.index) });
      const hit = m[0];
      if (m[1]) out.push({ t: "code", v: hit.slice(1, -1) });
      else if (m[2]) out.push({ t: "b", c: inline(hit.slice(2, -2)) });
      else if (m[3]) {
        const label = hit.slice(1, hit.indexOf("]("));
        out.push({ t: "a", href: m[4], c: inline(label) });
      } else out.push({ t: "i", c: inline(hit.slice(1, -1)) });
      rest = rest.slice(m.index + hit.length);
    }
    return out;
  }

  // The whole answer, as blocks: {t: "p"|"h"|"ul"|"ol"|"pre", ...}.
  function parse(source) {
    const text = String(source == null ? "" : source).slice(0, MAX).replace(/\r\n?/g, "\n");
    const lines = text.split("\n");
    const blocks = [];
    let para = [];
    let list = null;
    const endPara = () => { if (para.length) { blocks.push({ t: "p", c: inline(para.join(" ")) }); para = []; } };
    const endList = () => { if (list) { blocks.push(list); list = null; } };
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const fence = line.match(/^\s*```/);
      if (fence) {
        endPara(); endList();
        const code = [];
        for (i += 1; i < lines.length && !/^\s*```/.test(lines[i]); i += 1) code.push(lines[i]);
        blocks.push({ t: "pre", v: code.join("\n") });
        continue;
      }
      if (!line.trim()) { endPara(); endList(); continue; }
      const heading = line.match(/^\s{0,3}#{1,6}\s+(.*)$/);
      if (heading) { endPara(); endList(); blocks.push({ t: "h", c: inline(heading[1].trim()) }); continue; }
      const bullet = line.match(/^\s{0,6}[-*•]\s+(.*)$/);
      const number = line.match(/^\s{0,6}(\d{1,3})[.)]\s+(.*)$/);
      if (bullet || number) {
        endPara();
        const kind = bullet ? "ul" : "ol";
        if (!list || list.t !== kind) { endList(); list = { t: kind, items: [] }; if (number) list.start = Number(number[1]); }
        list.items.push(inline((bullet ? bullet[1] : number[2]).trim()));
        continue;
      }
      if (list && /^\s{2,}\S/.test(line)) { // a list item carried on to the next line
        const last = list.items[list.items.length - 1];
        last.push({ t: "text", v: " " }, ...inline(line.trim()));
        continue;
      }
      endList();
      para.push(line.trim());
    }
    endPara(); endList();
    return blocks;
  }

  function safeHref(href) {
    try {
      const url = new URL(href);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  function inlineNodes(doc, parts, parent) {
    for (const p of parts) {
      if (p.t === "text") parent.appendChild(doc.createTextNode(p.v));
      else if (p.t === "code") { const c = doc.createElement("code"); c.textContent = p.v; parent.appendChild(c); }
      else if (p.t === "b" || p.t === "i") {
        const el = doc.createElement(p.t === "b" ? "strong" : "em");
        inlineNodes(doc, p.c, el);
        parent.appendChild(el);
      } else if (p.t === "a") {
        const href = safeHref(p.href);
        if (!href) { inlineNodes(doc, p.c, parent); continue; }
        const a = doc.createElement("a");
        a.setAttribute("href", href);
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noopener noreferrer nofollow");
        inlineNodes(doc, p.c, a);
        parent.appendChild(a);
      }
    }
  }

  // A DocumentFragment for `text`, built only from elements and text nodes.
  function render(text, doc) {
    const d = doc || root.document;
    const frag = d.createDocumentFragment();
    for (const b of parse(text)) {
      let el;
      if (b.t === "p") { el = d.createElement("p"); inlineNodes(d, b.c, el); }
      else if (b.t === "h") { el = d.createElement("p"); el.className = "pilot-h"; const s = d.createElement("strong"); inlineNodes(d, b.c, s); el.appendChild(s); }
      else if (b.t === "pre") { el = d.createElement("pre"); const c = d.createElement("code"); c.textContent = b.v; el.appendChild(c); }
      else {
        el = d.createElement(b.t);
        if (b.t === "ol" && b.start && b.start !== 1) el.setAttribute("start", String(b.start));
        for (const item of b.items) { const li = d.createElement("li"); inlineNodes(d, item, li); el.appendChild(li); }
      }
      frag.appendChild(el);
    }
    return frag;
  }

  // The same text with the marks taken out, for "Copy".
  function plain(text) {
    return parse(text).map((b) => {
      const flat = (parts) => parts.map((p) => (p.t === "text" || p.t === "code" ? p.v : flat(p.c))).join("");
      if (b.t === "pre") return b.v;
      if (b.t === "ul") return b.items.map((i) => `- ${flat(i)}`).join("\n");
      if (b.t === "ol") return b.items.map((i, n) => `${(b.start || 1) + n}. ${flat(i)}`).join("\n");
      return flat(b.c);
    }).join("\n\n");
  }

  const api = { VERSION, parse, render, plain, safeHref };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotRender = api;
})(typeof window !== "undefined" ? window : globalThis);
