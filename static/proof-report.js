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

/* A proof of monitoring report, drawn from /api/proof-reports/{id} with
   the console's own sign-in. Every value is escaped where it is used. */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));
const $ = (id) => document.getElementById(id);

function token() {
  try { return localStorage.getItem("cyberlogix.session"); }
  catch (_) { return null; }
}

function mins(value) {
  if (value === null || value === undefined) return "—";
  const m = Math.round(Number(value));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `${h} h ${r} min` : `${h} h`;
}
function pct(value) {
  return value === null || value === undefined ? "—" : `${value}%`;
}
function stamp(iso) {
  if (!iso) return "";
  const at = new Date(iso);
  return isNaN(at) ? String(iso) : at.toLocaleString([], {
    day: "numeric", month: "short", year: "numeric",
    hour: "numeric", minute: "2-digit", timeZone: "UTC",
  }) + " UTC";
}
function day(iso) {
  const at = new Date(iso);
  return isNaN(at) ? "" : at.toLocaleDateString([], {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

function fail(html) {
  $("err").hidden = false;
  $("err").innerHTML = html;
}

function figs(s) {
  const acknowledged = s.alerts
    ? `${s.alerts_acknowledged} of ${s.alerts} acknowledged`
    : "no alerts this month";
  return [
    ["Sensors watched", String(s.sensors_watched), "at this location"],
    // The hours behind the share, so 100% of nothing never reads as 100%.
    ["Time online", s.online_percent === null || s.online_percent === undefined
      ? (s.hours_watched === undefined ? "—" : "Not enough yet")
      : pct(s.online_percent),
     s.hours_watched === undefined ? "all sensors together"
       : `of ${s.hours_watched} sensor-hours watched`],
    ["Temperature alerts", String(s.alerts), acknowledged],
    ["Typical answer", mins(s.median_minutes_to_acknowledge),
     s.slowest_minutes_to_acknowledge === null ? "time to acknowledge"
       : `slowest ${mins(s.slowest_minutes_to_acknowledge)}`],
  ].map(([k, n, sub]) => `<div class="fig"><span class="k">${esc(k)}</span>
      <span class="n">${esc(n)}</span><span class="s">${esc(sub)}</span></div>`).join("");
}

/* "21.0°F", as the insurance pack writes a reading (3.40.0), never "21°F". */
function tenth(value) {
  const n = Number(value);
  return value === null || value === undefined || value === "" || !Number.isFinite(n) ? String(value ?? "—") : n.toFixed(1);
}

function render(seen) {
  const r = seen.report;
  const u = r.temperature_unit;
  document.title = `Proof of monitoring, ${r.month_label} — ${r.business}`;
  $("business").textContent = r.business;
  $("where").textContent = `${r.location.name} · ${r.month_label}${
    r.month_complete ? "" : " (so far)"} · ${day(r.period_start)} to ${day(r.period_end)}`;
  $("state").textContent = seen.state_words;
  $("state").classList.toggle("bad", seen.state !== "genuine");
  $("figs").innerHTML = figs(r.summary);

  $("sensors").innerHTML = r.sensors.map((s) => `<tr>
      <td>${esc(s.name)}<span class="sub">${esc(s.watched_hours)} hours watched</span></td>
      <td class="num">${esc(s.online_percent === null ? "under an hour" : pct(s.online_percent))}</td>
      <td class="num">${esc(s.longest_gap_minutes === null ? "none" : mins(s.longest_gap_minutes))}</td>
      <td class="num">${esc(s.alerts)}</td>
    </tr>`).join("");

  $("alerts").innerHTML = r.alerts.length ? r.alerts.map((a) => {
    const answered = a.minutes_to_acknowledge === null
      ? `<span class="late">Not acknowledged</span>`
      : `Acknowledged after ${esc(mins(a.minutes_to_acknowledge))}${
          a.acknowledged_by_role ? ` by the ${esc(a.acknowledged_by_role.toLowerCase())}` : ""}`;
    const limit = a.limit_above !== null && a.worst > a.limit_above
      ? `limit ${esc(a.limit_above)}°${esc(u)}`
      : a.limit_below !== null ? `limit ${esc(a.limit_below)}°${esc(u)}` : "";
    return `<div class="alert"><div><strong>${esc(a.unit)}</strong> reached
        ${esc(tenth(a.worst))}°${esc(u)}${limit ? ` (${limit})` : ""}</div>
      <div class="when">${esc(stamp(a.opened_at))}${a.closed_at
        ? ` · back to normal ${esc(stamp(a.closed_at))}` : " · still open"}</div>
      <div>${answered}</div></div>`;
  }).join("") : `<p class="none">No temperature alerts in this period.</p>`;

  const g = r.longest_gap;
  $("gap").textContent = g
    ? `${g.unit}: ${mins(g.minutes)} with no data, from ${stamp(g.started_at)}${
        g.still_going ? " (still silent when this report was made)." : ` to ${stamp(g.ended_at)}.`}`
    : `No sensor went ${r.gap_threshold_minutes} minutes or more without sending data.`;

  const who = r.alerted;
  const roster = who.on_call.length
    ? `<ul class="people">${who.on_call.map((p) => `<li>${esc(p.position)}. ${
        esc(p.role)} · gets ${esc(p.gets)} · ${esc(p.covers)}</li>`).join("")}</ul>`
    : `<p class="para">Nobody is on the on-call list, so alerts go to the business's main phone number.</p>`;
  const team = who.team.length
    ? `<p class="para">People who can sign in and acknowledge: ${who.team.map(
        (t) => `${esc(t.role)}: ${esc(t.count)}`).join(", ")}.</p>` : "";
  $("alerted").innerHTML = `${roster}<p class="para">${esc(who.rule)}</p>${team}`;

  $("how").textContent = r.how_measured;
  if (r.coverage.note) {
    $("coverage").hidden = false;
    $("coverage").textContent = r.coverage.note;
  }

  const link = seen.link.url || (/^https?:$/.test(location.protocol)
    ? location.origin + seen.link.path : seen.link.path);
  $("link").textContent = link;
  $("code").textContent = seen.verify_code;
  $("fingerprint").textContent = seen.fingerprint;
  $("fine").textContent = `Made ${stamp(r.issued_at)}. Open the link, or go to `
    + `/verify and type the code, to see that this report is genuine and `
    + `unchanged. The fingerprint there must match the one above. `
    + `${BRAND.short} is a monitoring service, not an insurer.`;

  $("report").hidden = false;
  $("print").hidden = false;
}

$("print").addEventListener("click", () => window.print());

(async () => {
  const id = location.pathname.split("/").filter(Boolean).pop();
  const t = token();
  if (!t) {
    fail(`Sign in to the <a href="/console">console</a> as an owner first, then open this report again.`);
    return;
  }
  try {
    const resp = await fetch(`/api/proof-reports/${encodeURIComponent(id)}`,
      { headers: { Authorization: `Bearer ${t}` } });
    if (resp.status === 401) {
      fail(`Your sign-in has run out. Sign in to the <a href="/console">console</a> again.`);
      return;
    }
    if (!resp.ok) {
      fail("This report could not be opened. Only an owner of the business can open it.");
      return;
    }
    render(await resp.json());
  } catch (_) {
    fail("This report could not be loaded.");
  }
})();
