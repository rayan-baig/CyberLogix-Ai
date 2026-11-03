"use strict";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

/* A deliberately tiny Markdown renderer.
 *
 * The alternative is pulling a library over a CDN onto the page that
 * carries the liability cap, which is exactly the page that should have
 * the fewest moving parts. This handles the six constructs the documents
 * actually use and ignores everything else.
 *
 * Every line is escaped BEFORE any markup is added, so nothing in the
 * source text can introduce a tag. The inline pass then only ever emits
 * <strong> and <code>, and only around content that has already been
 * escaped. Adding a construct here means checking that property again.
 */
function inline(text) {
  return esc(text)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function renderMarkdown(src) {
  const out = [];
  const lines = String(src).split("\n");
  let list = null;
  let table = null;

  const closeList = () => { if (list) { out.push("</ul>"); list = null; } };
  const closeTable = () => { if (table) { out.push("</tbody></table>"); table = null; } };
  const closeAll = () => { closeList(); closeTable(); };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) { closeAll(); continue; }

    // Table rows. The separator row (|---|---|) is what turns the row
    // before it into a header, so it is looked for rather than rendered.
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      const cells = trimmed.slice(1, -1).split("|").map((c) => c.trim());
      const isSeparator = cells.every((c) => /^:?-{2,}:?$/.test(c));
      if (isSeparator) continue;
      const nextIsSeparator = (lines[i + 1] || "").trim()
        .match(/^\|(\s*:?-{2,}:?\s*\|)+$/);
      if (!table) {
        closeList();
        out.push("<table>");
        table = nextIsSeparator ? "head" : "body";
        out.push(table === "head" ? "<thead>" : "<tbody>");
      }
      const tag = table === "head" ? "th" : "td";
      out.push("<tr>" + cells.map((c) => `<${tag}>${inline(c)}</${tag}>`)
        .join("") + "</tr>");
      if (table === "head") { out.push("</thead><tbody>"); table = "body"; }
      continue;
    }
    closeTable();

    if (trimmed.startsWith("* ")) {
      if (!list) { out.push("<ul>"); list = true; }
      out.push(`<li>${inline(trimmed.slice(2))}</li>`);
      continue;
    }
    closeList();

    if (trimmed.startsWith("> ")) {
      out.push(`<blockquote>${inline(trimmed.slice(2))}</blockquote>`);
      continue;
    }
    if (trimmed.startsWith("## ")) {
      out.push(`<h2>${inline(trimmed.slice(3))}</h2>`);
      continue;
    }
    if (trimmed.startsWith("# ")) {
      out.push(`<h1>${inline(trimmed.slice(2))}</h1>`);
      continue;
    }

    // A paragraph runs until a blank line. Joining the wrapped source
    // lines matters: the documents are hard-wrapped at 72 columns and
    // rendering each line as its own paragraph would shred them.
    const para = [trimmed];
    while (i + 1 < lines.length && lines[i + 1].trim()
           && !/^([*>#]|\|)/.test(lines[i + 1].trim())) {
      para.push(lines[++i].trim());
    }
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }

  closeAll();
  return out.join("\n");
}

const picker = document.getElementById("picker");
const docEl = document.getElementById("doc");
let catalogue = [];

function slugFromHash() {
  const wanted = (location.hash || "").replace(/^#/, "");
  return catalogue.some((d) => d.slug === wanted) ? wanted : "terms";
}

async function show(slug) {
  for (const button of picker.querySelectorAll("button")) {
    button.setAttribute("aria-current", String(button.dataset.slug === slug));
  }
  try {
    const resp = await fetch(`/api/legal/${encodeURIComponent(slug)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const doc = await resp.json();
    // Version and date are already the second line of the document; the
    // stamp adds only the thing the document cannot state about itself.
    docEl.innerHTML = renderMarkdown(doc.body_markdown)
      + `<p class="stamp">SHA-256 of this text: ${esc(doc.sha256)}</p>`;
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) {
    docEl.innerHTML = `<p>That agreement could not be loaded
      (${esc(err.message)}). It is served from
      <code>/api/legal/${esc(slug)}</code>.</p>`;
  }
}

async function boot() {
  try {
    const resp = await fetch("/api/legal");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const body = await resp.json();
    catalogue = body.documents || [];

    picker.innerHTML = catalogue.map((d) =>
      `<button type="button" data-slug="${esc(d.slug)}">${esc(d.title)}</button>`
    ).join("");
    for (const button of picker.querySelectorAll("button")) {
      button.addEventListener("click", () => {
        location.hash = button.dataset.slug;
      });
    }

    window.addEventListener("hashchange", () => show(slugFromHash()));
    show(slugFromHash());
  } catch (err) {
    docEl.innerHTML = `<p>The agreements could not be loaded
      (${esc(err.message)}). They are served from
      <code>/api/legal</code>.</p>`;
  }
}

boot();
