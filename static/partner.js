"use strict";

const KEY_STORAGE = "cyberlogix.partner";
const POLL_MS = 45000;

const state = { key: null, book: null, timer: null };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => "$" + Number(n || 0).toLocaleString(undefined,
  { maximumFractionDigits: 0 });

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = message;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 5200);
}

async function api(path) {
  const res = await fetch(path, {
    headers: { "X-CyberLogix-Partner": state.key || "" },
  });
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (_) { body = { detail: text }; }
  if (!res.ok) {
    const detail = body && body.detail
      ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail))
      : `Request failed (${res.status})`;
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return body;
}

/* ---------------- session ---------------- */

$("key-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const btn = event.target.querySelector("button");
  btn.disabled = true;
  const key = $("pk").value.trim();
  try {
    state.key = key;
    await refresh();
    try { localStorage.setItem(KEY_STORAGE, key); } catch (_) {}
    $("gate").hidden = true;
    $("app").hidden = false;
    startPolling();
  } catch (err) {
    state.key = null;
    toast(err.message, "err");
  } finally { btn.disabled = false; }
});

$("sign-out").addEventListener("click", () => {
  try { localStorage.removeItem(KEY_STORAGE); } catch (_) {}
  clearInterval(state.timer);
  state.key = null;
  state.book = null;
  $("app").hidden = true;
  $("gate").hidden = false;
  $("pk").value = "";
});

/* ---------------- render ---------------- */

async function refresh() {
  const [book, statement] = await Promise.all([
    api("/api/partners/me"),
    api("/api/partners/me/statement"),
  ]);
  state.book = book;
  renderBook(book);
  renderStatement(statement);
}

function tile(label, value, foot) {
  return `<div class="tile"><div class="tile-label">${esc(label)}</div>`
       + `<div class="tile-value">${esc(value)}</div>`
       + `<div class="tile-foot">${esc(foot)}</div></div>`;
}

function renderBook(book) {
  $("chip-partner").textContent = book.partner.company_name;
  $("chip-rate").textContent = `${book.partner.commission_percent}% of billings`;

  $("earn").innerHTML = [
    tile("Accounts", `${book.accounts}`, "under management"),
    tile("Units", `${book.units}`, "monitored across the book"),
    tile("Monthly commission", money(book.monthly_commission_usd),
         `on ${money(book.monthly_billings_usd)} of billings`),
    tile("Annual run rate", money(book.annual_commission_usd),
         "at today's book"),
  ].join("");

  const attention = book.needs_attention;
  $("attn-note").textContent = attention.length
    ? `${attention.length} account${attention.length === 1 ? "" : "s"}`
    : "";
  $("attn-body").innerHTML = attention.length
    ? attention.map((a) => {
        const why = [
          a.open_incidents ? `${a.open_incidents} open incident${
            a.open_incidents === 1 ? "" : "s"}` : null,
          a.units_offline ? `${a.units_offline} offline` : null,
          a.low_battery ? `${a.low_battery} flat batter${
            a.low_battery === 1 ? "y" : "ies"}` : null,
        ].filter(Boolean).join(" · ");
        return `<div class="attn">
          <span class="grow">${esc(a.company_name)}</span>
          <span class="attn-why">${esc(why)}</span>
          <button class="btn btn-sm" data-open="${esc(a.tenant_id)}">Open</button>
        </div>`;
      }).join("")
    : `<div class="empty">Every account is quiet.
         <span class="sub">Nothing on the book needs an engineer today.</span>
       </div>`;

  $("book-note").textContent = `${book.accounts} account${
    book.accounts === 1 ? "" : "s"}`;
  $("book-body").innerHTML = book.book.length
    ? book.book.map((a) => {
        const flagged = a.open_incidents || a.units_offline || a.low_battery;
        return `<tr class="${flagged ? "flagged" : ""}" data-open="${
          esc(a.tenant_id)}" tabindex="0">
          <td class="name">${esc(a.company_name)}</td>
          <td>${esc(a.plan)}</td>
          <td class="money">${esc(a.units)}</td>
          <td class="money">${a.units_offline || "—"}</td>
          <td class="money">${a.low_battery || "—"}</td>
          <td class="money">${a.open_incidents || "—"}</td>
          <td class="money">${money(a.monthly_usd)}</td>
          <td class="money">${money(a.commission_usd)}</td>
        </tr>`;
      }).join("")
    : `<tr><td colspan="8"><div class="empty">No accounts yet.
         <span class="sub">Ask CyberLogix to attach the estates you manage.
         </span></div></td></tr>`;
}

function renderStatement(statement) {
  $("stmt-note").textContent = money(statement.commission_usd);
  $("stmt-body").innerHTML = statement.lines.length
    ? `<div class="readout">${statement.lines.map((line) =>
        `<div><span class="k">${esc(line.company_name)}</span>
           <span class="v">${money(line.commission_usd)}</span></div>`).join("")}
       <div><span class="k">Total</span><span class="v">${
         money(statement.commission_usd)}</span></div>
       </div>
       <div class="card-note u-mt-10">${esc(statement.note)}</div>`
    : `<div class="empty">Nothing billed yet.</div>`;
}

/* ---------------- one account ---------------- */

async function openAccount(tenantId) {
  let detail;
  try { detail = await api(`/api/partners/me/accounts/${encodeURIComponent(tenantId)}`); }
  catch (err) { toast(err.message, "err"); return; }

  $("detail-title").textContent = detail.company_name;
  $("detail-note").textContent = `${detail.units.length} unit${
    detail.units.length === 1 ? "" : "s"} · ${detail.plan} plan`;

  const rows = detail.units.map((u) => `<tr>
      <td>${esc(u.sensor_id)}</td>
      <td>${esc(u.site || "—")}</td>
      <td>${esc(u.location_name)}</td>
      <td class="money">${u.last_temperature_display === null
        || u.last_temperature_display === undefined
        ? "—" : esc(u.last_temperature_display) + "°" + esc(detail.temperature_unit)}</td>
      <td class="money${u.battery_low ? " u-c-critical" : ""}">${
        u.battery_percent === null || u.battery_percent === undefined
          ? "—" : esc(Math.round(u.battery_percent)) + "%"}</td>
      <td class="money">${u.signal_percent === null || u.signal_percent === undefined
        ? "—" : esc(Math.round(u.signal_percent)) + "%"}</td>
      <td>${u.online ? "online" : "offline"}</td>
      <td>${esc(u.external_device_sn || "native")}</td>
    </tr>`).join("");

  $("detail-body").innerHTML = `
    ${detail.open_incidents.length
      ? `<div class="card-head u-mt-0">
           <span class="card-title u-fs-16">Open right now</span>
         </div>
         ${detail.open_incidents.map((i) => `<div class="attn">
            <span class="grow"><b>${esc(i.sensor_id)}</b> — ${esc(i.detail)}</span>
            <span class="attn-why">${esc(Math.round(i.minutes_open))} min</span>
          </div>`).join("")}`
      : `<div class="empty u-p-26">Nothing open.</div>`}

    <div class="card-head u-mt-24">
      <span class="card-title u-fs-16">Units</span>
    </div>
    <div class="scrollx"><table>
      <thead><tr>
        <th>Unit</th><th>Site</th><th>Location</th>
        <th class="money">Reading</th><th class="money">Battery</th>
        <th class="money">Signal</th><th>Link</th><th>Hardware</th>
      </tr></thead>
      <tbody>${rows || `<tr><td colspan="8">No units registered.</td></tr>`}</tbody>
    </table></div>

    <div class="card-note u-mt-14">${esc(detail.service_note)}</div>`;

  $("list-view").hidden = true;
  $("detail-view").hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

$("back").addEventListener("click", () => {
  $("detail-view").hidden = true;
  $("list-view").hidden = false;
});

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-open]");
  if (target) openAccount(target.dataset.open);
});

/* A row is a control, so it answers the keyboard too. */
document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest("tr[data-open]");
  if (!row) return;
  event.preventDefault();
  openAccount(row.dataset.open);
});

/* ---------------- resume ---------------- */

/* A book that has quietly stopped updating is worse than one that admits
   it: a servicer reading "every account is quiet" off a frozen page will
   not send anybody. */
function startPolling() {
  clearInterval(state.timer);
  state.timer = setInterval(() => {
    refresh()
      .then(() => { state.failures = 0; $("stale-banner").hidden = true; })
      .catch((err) => {
        if (err && err.status === 401) {
          toast("That key is no longer valid.", "err");
          $("sign-out").click();
          return;
        }
        state.failures = (state.failures || 0) + 1;
        if (state.failures < 2) return;
        const banner = $("stale-banner");
        banner.hidden = false;
        banner.textContent =
          "Not reaching the hub. What is on screen is the last good "
          + "reading, not the current one.";
      });
  }, POLL_MS);
}

(async () => {
  let stored = null;
  try { stored = localStorage.getItem(KEY_STORAGE); } catch (_) {}
  if (!stored) return;
  state.key = stored;
  try {
    await refresh();
    $("gate").hidden = true;
    $("app").hidden = false;
    startPolling();
  } catch (_) {
    state.key = null;
    try { localStorage.removeItem(KEY_STORAGE); } catch (_) {}
  }
})();
