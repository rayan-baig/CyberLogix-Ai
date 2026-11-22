// Pilot core 1.4.0 · pilot-render.js
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
// Core 1.4.0: an app's own answer (a welcome pill's `answer`, written by the
// app, not the AI) is rendered with `{ local: <this page's address> }`. Then
// a link may also be a path on the same site ("[Orbyt](/orbyt)"), and a link
// to this site opens in the same tab. AI answers never get this.
//
// `parse` needs no DOM (Node tests use it); `render` takes a document.
(function (root) {
  "use strict";

  const VERSION = "1.4.0";
  const MAX = 40000;

  // Inline marks in one line of text: `code`, **bold**, *italic* or _italic_,
  // and [words](https://address). Returns [{t: "text"|"code"|"b"|"i"|"a", ...}].
  // With `local` (core 1.4.0), a link may also be a path on this site:
  // "/orbyt", "/help#keys" (never "//elsewhere").
  const WEB = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[[^\]\n]{1,200}\]\((https?:\/\/[^\s)]{1,500})\))|(\*[^*\s][^*\n]*\*)|(\b_[^_\s][^_\n]*_\b)/;
  const LOCAL = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[[^\]\n]{1,200}\]\((https?:\/\/[^\s)]{1,500}|\/(?![\/\\])[^\s)]{0,500})\))|(\*[^*\s][^*\n]*\*)|(\b_[^_\s][^_\n]*_\b)/;
  function inline(text, local) {
    const out = [];
    let rest = text;
    const pattern = local ? LOCAL : WEB;
    while (rest) {
      const m = rest.match(pattern);
      if (!m) { out.push({ t: "text", v: rest }); break; }
      if (m.index > 0) out.push({ t: "text", v: rest.slice(0, m.index) });
      const hit = m[0];
      if (m[1]) out.push({ t: "code", v: hit.slice(1, -1) });
      else if (m[2]) out.push({ t: "b", c: inline(hit.slice(2, -2), local) });
      else if (m[3]) {
        const label = hit.slice(1, hit.indexOf("]("));
        out.push({ t: "a", href: m[4], c: inline(label, local) });
      } else out.push({ t: "i", c: inline(hit.slice(1, -1), local) });
      rest = rest.slice(m.index + hit.length);
    }
    return out;
  }

  // The whole answer, as blocks: {t: "p"|"h"|"ul"|"ol"|"pre", ...}.
  // opts.local (core 1.4.0): this site's address; paths become links too.
  function parse(source, opts) {
    const local = Boolean(opts && opts.local);
    const inl = (t) => inline(t, local);
    const text = String(source == null ? "" : source).slice(0, MAX).replace(/\r\n?/g, "\n");
    const lines = text.split("\n");
    const blocks = [];
    let para = [];
    let list = null;
    const endPara = () => { if (para.length) { blocks.push({ t: "p", c: inl(para.join(" ")) }); para = []; } };
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
      if (heading) { endPara(); endList(); blocks.push({ t: "h", c: inl(heading[1].trim()) }); continue; }
      const bullet = line.match(/^\s{0,6}[-*•]\s+(.*)$/);
      const number = line.match(/^\s{0,6}(\d{1,3})[.)]\s+(.*)$/);
      if (bullet || number) {
        endPara();
        const kind = bullet ? "ul" : "ol";
        if (!list || list.t !== kind) { endList(); list = { t: kind, items: [] }; if (number) list.start = Number(number[1]); }
        list.items.push(inl((bullet ? bullet[1] : number[2]).trim()));
        continue;
      }
      if (list && /^\s{2,}\S/.test(line)) { // a list item carried on to the next line
        const last = list.items[list.items.length - 1];
        last.push({ t: "text", v: " " }, ...inl(line.trim()));
        continue;
      }
      endList();
      para.push(line.trim());
    }
    endPara(); endList();
    return blocks;
  }

  // An http(s) address, or null. base (core 1.4.0): this site's address, so
  // a path is read against it.
  function safeHref(href, base) {
    try {
      const url = base ? new URL(href, base) : new URL(href);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  // Is this address on the same site as base (core 1.4.0)?
  function sameSite(href, base) {
    try { return Boolean(base) && new URL(href).origin === new URL(base).origin; } catch (_) { return false; }
  }

  function inlineNodes(doc, parts, parent, base) {
    for (const p of parts) {
      if (p.t === "text") parent.appendChild(doc.createTextNode(p.v));
      else if (p.t === "code") { const c = doc.createElement("code"); c.textContent = p.v; parent.appendChild(c); }
      else if (p.t === "b" || p.t === "i") {
        const el = doc.createElement(p.t === "b" ? "strong" : "em");
        inlineNodes(doc, p.c, el, base);
        parent.appendChild(el);
      } else if (p.t === "a") {
        const href = safeHref(p.href, base);
        if (!href) { inlineNodes(doc, p.c, parent, base); continue; }
        const a = doc.createElement("a");
        a.setAttribute("href", href);
        // The app's own link to its own site stays in this tab (core 1.4.0).
        if (!sameSite(href, base)) {
          a.setAttribute("target", "_blank");
          a.setAttribute("rel", "noopener noreferrer nofollow");
        }
        inlineNodes(doc, p.c, a, base);
        parent.appendChild(a);
      }
    }
  }

  // A DocumentFragment for `text`, built only from elements and text nodes.
  // opts.local (core 1.4.0): this page's address, for an app's own answer
  // (paths are links, and links to this site open in this tab).
  function render(text, doc, opts) {
    const d = doc || root.document;
    const base = opts && typeof opts.local === "string" ? opts.local : "";
    const frag = d.createDocumentFragment();
    for (const b of parse(text, { local: Boolean(base) })) {
      let el;
      if (b.t === "p") { el = d.createElement("p"); inlineNodes(d, b.c, el, base); }
      else if (b.t === "h") { el = d.createElement("p"); el.className = "pilot-h"; const s = d.createElement("strong"); inlineNodes(d, b.c, s, base); el.appendChild(s); }
      else if (b.t === "pre") { el = d.createElement("pre"); const c = d.createElement("code"); c.textContent = b.v; el.appendChild(c); }
      else {
        el = d.createElement(b.t);
        if (b.t === "ol" && b.start && b.start !== 1) el.setAttribute("start", String(b.start));
        for (const item of b.items) { const li = d.createElement("li"); inlineNodes(d, item, li, base); el.appendChild(li); }
      }
      frag.appendChild(el);
    }
    return frag;
  }

  // The same text with the marks taken out, for "Copy".
  function plain(text, opts) {
    return parse(text, opts).map((b) => {
      const flat = (parts) => parts.map((p) => (p.t === "text" || p.t === "code" ? p.v : flat(p.c))).join("");
      if (b.t === "pre") return b.v;
      if (b.t === "ul") return b.items.map((i) => `- ${flat(i)}`).join("\n");
      if (b.t === "ol") return b.items.map((i, n) => `${(b.start || 1) + n}. ${flat(i)}`).join("\n");
      return flat(b.c);
    }).join("\n\n");
  }

  const api = { VERSION, parse, render, plain, safeHref, sameSite };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotRender = api;
})(typeof window !== "undefined" ? window : globalThis);
