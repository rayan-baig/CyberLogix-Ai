"use strict";
// The product's and the company's names, from the page: brand.py puts
// them in <meta name="brand">, so no script spells them.
const BRAND = (() => {
  const m = typeof document !== "undefined" && document.querySelector
    ? document.querySelector('meta[name="brand"]') : null;
  const d = (m && m.dataset) || {};
  return { app: (m && m.content) || "", short: d.short || "", company: d.company || "",
    checklist: d.checklist || "" };
})();

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
            '"': "&quot;", "'": "&#39;" }[c]));

/* The vertical is the last path segment. /for on its own is the index. */
const slug = location.pathname.replace(/\/+$/, "").split("/").pop();

/* The food trades ahead of the niche ones (3.39.0): restaurants, other
   food service and the food trades' own pages (a butcher, a grocery
   store), then pharmacies and a vaccine clinic, then everything else A to
   Z under "More trades with cold stock". Cannabis was fourth, ahead of
   every food trade but two. Every page is still listed. */
function indexOrder(rows, trades) {
  const card = (row) => (row.vertical
    ? { href: `/for/${row.vertical}`, name: row.name, line: row.slogan }
    : { href: `/for/${row.slug}`, name: row.name, line: row.lede });
  const byName = (a, b) => String(a.name).localeCompare(String(b.name));
  const food = rows.filter((r) => r.group === "food")
    .concat(trades.filter((t) => t.plan === "restaurant").sort(byName));
  const pharmacy = rows.filter((r) => r.group === "pharmacy")
    .concat(trades.filter((t) => t.plan === "pharmacy").sort(byName));
  const first = food.concat(pharmacy);
  const rest = rows.filter((r) => !first.includes(r))
    .concat(trades.filter((t) => !first.includes(t))).sort(byName);
  return { first: first.map(card), rest: rest.map(card) };
}

function renderIndex(rows, trades) {
  document.title = `${BRAND.short} — who this is for`;
  const { first, rest } = indexOrder(rows, trades);
  const grid = (cards) => cards.map((c) => `
       <a class="ind" href="${esc(c.href)}">
         <b>${esc(c.name)}</b>
         <span>${esc(c.line)}</span>
       </a>`).join("");
  $("page").innerHTML =
    `<div class="eyebrow">Who this is for</div>
     <h1 class="lede">${esc(String(first.length + rest.length))} kinds of business with the same problem.</h1>
     <p class="standfirst">Something you own has to stay at a temperature,
        it fails quietly and usually overnight, and afterwards somebody
        asks you to prove what happened. Pick yours.</p>
     <div class="grid">${grid(first)}</div>
     ${rest.length ? `<h2 class="more-head">More trades with cold stock</h2>
     <div class="grid">${grid(rest)}</div>` : ""}`;
}

function renderIndustry(d) {
  document.title = `${BRAND.short} — for ${d.name}`;
  $("page").innerHTML = `
    <div class="eyebrow">${esc(d.name)}</div>
    <h1 class="lede">We watch ${esc(d.protects)}, and wake somebody up
      when it goes wrong.</h1>
    <p class="standfirst">${esc(d.slogan)}</p>

    <section class="slab bad" id="problem">
      <h2>The problem</h2>
      <p>${esc(d.loses)}</p>
      <p>The one we see most: <strong>${esc(d.catastrophe)}</strong>.</p>
    </section>

    <section class="slab" id="fix">
      <h2>The fix</h2>
      <p class="fix-line">${esc(d.fix)}</p>
      ${d.controls_line ? `<p class="controls-line">${esc(d.controls_line)}</p>` : ""}
      <ol class="does">
        <li><b>We watch every ${esc(d.asset_noun)}, all night.</b>
          A small sensor reads the temperature every few minutes and sends
          it to us. Your ${esc(d.asset_plural)} should stay
          ${esc(d.band)}, and we know when one stays out of it.</li>
        <li><b>We text, then phone a person until one answers.</b>
          Everyone on call gets a text. On Growth and up, if nobody answers,
          a real phone call follows, then the next name on the list. An
          alert nobody picks up is the same as no alert.</li>
        <li><b>We tell a real fault from a normal one.</b>
          Equipment warms up on purpose sometimes, and a door gets left
          open. A unit has to stay out of range for a while before anybody
          is texted, we learn each ${esc(d.asset_noun)}'s defrost, and an
          alert closes itself if the unit comes back before anybody
          answers &mdash; so the call you do get means something.</li>
        <li><b>We keep the proof.</b>
          Every reading is saved and sealed. When ${esc(d.asks_after)}
          asks what happened, the answer is a record you can print rather
          than a promise.${d.paperwork ? ` It prints as a ready-made
          <strong>${esc(d.paperwork)}</strong>.` : ""}</li>
      </ol>
    </section>

    <section class="slab">
      <h2>And the people, not just the equipment</h2>
      <p>The same shift has people in it who need a valid card to be
         standing there. We track when each one runs out and tell you
         before it does, not after an inspector does.</p>
      <div class="tags">${(d.licences || []).map((l) =>
         `<span class="tag-pill">${esc(l)}</span>`).join("")}</div>
      <p class="u-mt-14">When somebody leaves, we find what you
         are still paying for them &mdash; the phone, the software seat,
         the insurance &mdash; and how much of it is still going out.</p>
    </section>

    ${d.price_label ? `<section class="slab" id="cost">
      <h2>What it costs</h2>
      <p class="price-line"><strong>${esc(d.price_label)}</strong></p>
      ${d.price_note ? `<p>${esc(d.price_note)}</p>` : ""}
      ${d.price_custom ? `<p><a class="talk-link" href="${esc(d.talk_href)}">Talk to us</a>${
        d.talk_address
          ? ` <span class="talk-address">${esc(d.talk_address)}</span>` : ""}</p>`
        : `<p><a href="/pricing">Work out your price</a></p>`}
    </section>` : ""}

    <div class="foot">
      <a class="btn btn-sm btn-primary u-w-auto" href="/signup">
        Start watching</a>
      <a class="btn btn-sm" href="/for/${esc(d.vertical)}/sheet">
        One-page sheet to print</a>
      <a class="btn btn-sm" href="/for">Another industry</a>
    </div>`;
}

(async () => {
  try {
    if (!slug || slug === "for") {
      const rows = await (await fetch("/api/for")).json();
      renderIndex(rows.industries, rows.trades || []);
      return;
    }
    const got = await fetch(`/api/for/${encodeURIComponent(slug)}`);
    if (!got.ok) { location.href = "/for"; return; }
    renderIndustry(await got.json());
  } catch (err) {
    $("page").innerHTML =
      `<h1 class="lede">Could not load this page.</h1>
       <p class="standfirst">Try again in a moment.</p>`;
  }
})();
