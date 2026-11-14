"use strict";

/* An insurance partner's dashboard: their code, the businesses that joined
   with it (name and plan only), what they earned and what was paid. The
   key lives in sessionStorage, so it does not outlive the tab. */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));
const $ = (id) => document.getElementById(id);
const money = (n) => "$" + Number(n || 0).toLocaleString(undefined,
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const day = (iso) => (iso ? String(iso).slice(0, 10) : "");

const KEY = "clx-insurance-partner-key";
function storedKey() {
  try { return sessionStorage.getItem(KEY) || ""; } catch (_) { return ""; }
}
function remember(value) {
  try { sessionStorage.setItem(KEY, value); } catch (_) { /* private mode */ }
}
function forget() {
  try { sessionStorage.removeItem(KEY); } catch (_) { /* private mode */ }
}

function fail(message) {
  $("error").hidden = false;
  $("error").textContent = message;
}

function render(d) {
  $("title").textContent = d.partner.company_name;
  $("code").textContent = d.code;
  const link = d.link.url || (/^https?:$/.test(location.protocol)
    ? location.origin + d.link.path : d.link.path);
  $("link").textContent = link;
  $("copy").dataset.link = link;
  $("terms").textContent = d.terms;
  $("tiles").innerHTML = [
    ["Earned", money(d.earned_usd)],
    ["Paid to you", money(d.paid_usd)],
    ["Still owed", money(d.owed_usd)],
  ].map(([k, v]) => `<div class="tile"><span class="k">${esc(k)}</span>
      <span class="v">${esc(v)}</span></div>`).join("");
  $("businesses").innerHTML = d.businesses.length
    ? d.businesses.map((b) => `<div class="rowline">
        <span class="grow">${esc(b.business)}<span class="meta">${esc(b.plan)} · joined ${
          esc(day(b.joined_at))}${b.share_ends_at ? ` · share until ${esc(day(b.share_ends_at))}` : ""}${
          b.refunded ? " · refunded" : ""}</span></span>
        <span class="amt">${esc(money(b.earned_usd))}</span></div>`).join("")
    : `<p class="empty-line">Nobody has joined with your code yet.</p>`;
  $("payouts").innerHTML = d.payouts.length
    ? d.payouts.map((p) => `<div class="rowline">
        <span class="grow">${esc(day(p.paid_on))}<span class="meta">${esc(p.reference)}</span></span>
        <span class="amt">${esc(money(p.amount_usd))}</span></div>`).join("")
    : `<p class="empty-line">No payments yet.</p>`;
  $("privacy").textContent = d.privacy;
  $("gate").hidden = true;
  $("board").hidden = false;
  $("forget").hidden = false;
}

async function open_() {
  const key = storedKey();
  if (!key) return;
  $("error").hidden = true;
  try {
    const resp = await fetch("/api/insurance-partners/me",
      { headers: { "X-CyberLogix-Insurance-Partner": key } });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      forget();
      fail(typeof body.detail === "string" ? body.detail : "That key was not accepted.");
      return;
    }
    render(body);
  } catch (_) {
    fail("The dashboard could not be loaded.");
  }
}

$("gate").addEventListener("submit", (event) => {
  event.preventDefault();
  remember($("key").value.trim());
  $("key").value = "";
  open_();
});
$("forget").addEventListener("click", () => { forget(); location.reload(); });
$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("copy").dataset.link);
    $("copy").textContent = "Copied";
  } catch (_) {
    $("copy").textContent = "Select the link above to copy it";
  }
});

open_();
