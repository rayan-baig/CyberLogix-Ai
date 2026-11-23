"use strict";

/* A repair partner's dashboard: the leads we sent them (only what each
   business chose to send), what they owe us and what they have paid, and
   a way to mark a lead bad within the days allowed. The key lives in
   sessionStorage, so it does not outlive the tab. */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));
const $ = (id) => document.getElementById(id);
const money = (n) => "$" + Number(n || 0).toLocaleString(undefined,
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const day = (iso) => (iso ? String(iso).slice(0, 10) : "");

const KEY = "clx-repair-partner-key";
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

const STATE_CLASS = { owed: "charged", kept: "charged", disputed: "waiting",
                      refused: "refused" };

function contactLine(c) {
  return [c.contact_name, c.contact_phone, c.contact_email].filter(Boolean).join(" · ");
}

function addressLine(a) {
  return [a.street, a.city, a.postcode].filter(Boolean).join(", ");
}

function leadCard(l) {
  if (l.removed) {
    return `<div class="lead">
      <div class="lead-top"><span class="grow">${esc(l.lead_id)}</span>
        <span class="amt">${esc(money(l.fee_usd))}</span></div>
      <p class="meta">Sent ${esc(day(l.delivered_at))}. The business has since
        closed its account, so its details are no longer kept.</p>
      <p class="state ${esc(STATE_CLASS[l.status] || "")}">${esc(l.status_words)}</p>
    </div>`;
  }
  const form = l.can_mark_bad ? `<details>
      <summary>Not a real job? Mark it bad (until ${esc(day(l.mark_bad_until))})</summary>
      <form data-bad="${esc(l.lead_id)}">
        <label class="sr" for="why-${esc(l.lead_id)}">Why it was a bad lead</label>
        <input id="why-${esc(l.lead_id)}" required minlength="3" maxlength="300"
               placeholder="For example: the number did not work">
        <button type="submit">Mark it bad</button>
      </form>
    </details>` : "";
  return `<div class="lead">
    <div class="lead-top"><span class="grow">${esc(l.business_name)}</span>
      <span class="amt">${esc(money(l.fee_usd))}</span></div>
    <p class="meta">${esc(l.lead_id)} · sent ${esc(day(l.delivered_at))}</p>
    <p class="who">${esc(contactLine(l.contact))}</p>
    <p class="who">${esc(addressLine(l.address))}</p>
    <p class="problem">${esc(l.problem)}</p>
    <p class="state ${esc(STATE_CLASS[l.status] || "")}">${esc(l.status_words)}${
      l.bad_reason ? " Your reason: " + esc(l.bad_reason) : ""}</p>
    ${form}
  </div>`;
}

function render(d) {
  $("title").textContent = d.partner.company_name;
  $("areas").textContent = "Areas you cover: " + d.areas + ".";
  $("tiles").innerHTML = [
    ["Leads", String(d.leads.length)],
    ["Charged", money(d.charged_usd)],
    ["Paid", money(d.paid_usd)],
    ["To pay", money(d.owed_usd)],
  ].map(([k, v]) => `<div class="tile"><span class="k">${esc(k)}</span>
      <span class="v">${esc(v)}</span></div>`).join("");
  $("fee-words").textContent = money(d.lead_fee_usd) + " a lead. Mark a lead bad within "
    + d.bad_lead_days + " days if it was not a real job; we look at each one."
    + (d.in_question_usd ? " " + money(d.in_question_usd) + " is waiting for our decision." : "");
  $("leads").innerHTML = d.leads.length
    ? d.leads.map(leadCard).join("")
    : `<p class="empty-line">No leads yet. They come when a business near you asks for a repair quote.</p>`;
  $("payments").innerHTML = d.payments.length
    ? d.payments.map((p) => `<div class="rowline">
        <span class="grow">${esc(day(p.paid_on))}<span class="meta">${esc(p.reference)}</span></span>
        <span class="amt">${esc(money(p.amount_usd))}</span></div>`).join("")
    : `<p class="empty-line">No payments recorded yet.</p>`;
  $("billing").textContent = d.how_billing_works;
  $("privacy").textContent = d.privacy;
  $("gate").hidden = true;
  $("board").hidden = false;
  $("forget").hidden = false;
}

async function call(path, options) {
  const opts = options || {};
  return fetch(path, {
    method: opts.method || "GET",
    headers: Object.assign({ "X-Kryozyn-Repair-Partner": storedKey() },
      opts.body ? { "Content-Type": "application/json" } : {}),
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
}

async function open_() {
  if (!storedKey()) return;
  $("error").hidden = true;
  try {
    const resp = await call("/api/repair-partners/me");
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
$("leads").addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-bad]");
  if (!form) return;
  event.preventDefault();
  const id = form.dataset.bad;
  const reason = form.querySelector("input").value.trim();
  const resp = await call("/api/repair-partners/me/leads/" + encodeURIComponent(id) + "/bad",
    { method: "POST", body: { reason: reason } });
  const body = await resp.json().catch(() => ({}));
  $("said").hidden = false;
  $("said").textContent = resp.ok
    ? "Marked bad. We will look at it and you will see our decision here."
    : (typeof body.detail === "string" ? body.detail : "That was not accepted.");
  if (resp.ok) open_();
});

open_();
