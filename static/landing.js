// Every number on this page comes off the running product. A marketing page
// that keeps its own copy of the price list is a marketing page that will
// eventually quote a price the product does not charge.
const money = n => "$" + Number(n).toLocaleString("en-US", {
  minimumFractionDigits: 0, maximumFractionDigits: 0 });

const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function paintSectors() {
  const grid = document.getElementById("sectors-grid");
  try {
    const res = await fetch("/api/industries");
    if (!res.ok) throw new Error(res.status);
    const body = await res.json();
    const rows = body.industries || body;
    grid.innerHTML = rows.map(r => `
      <article class="sector">
        <p class="sector-fault">${esc(r.catastrophe)}</p>
        <h3>${esc(r.name)}</h3>
        <p class="sector-line">${esc(r.slogan)}</p>
        <div class="sector-price">
          <b>${r.monthly_usd != null ? money(r.monthly_usd) : "—"}</b>
          <span>per ${esc(r.billing_unit || r.asset_noun || "unit")} / month</span>
        </div>
      </article>`).join("");
  } catch (err) {
    // A blank grid would read as "no sectors". Say what actually happened.
    grid.innerHTML = `<p class="empty">The sector list could not be loaded
      (${esc(err.message)}). It is served from
      <code>/api/industries</code>.</p>`;
  }
}

async function paintPlans() {
  const grid = document.getElementById("plans-grid");
  try {
    const res = await fetch("/api/licenses/plans");
    if (!res.ok) throw new Error(res.status);
    const plans = (await res.json()).plans || [];
    grid.innerHTML = plans.map(p => `
      <article class="plan" data-featured="${p.plan === "growth" ? "yes" : "no"}">
        <div>
          <div class="plan-name">${esc(p.name)}</div>
          <div class="plan-seats">Up to ${p.max_sensors.toLocaleString("en-US")}
            sensor${p.max_sensors === 1 ? "" : "s"}${p.max_locations
              ? ` at ${p.max_locations} location${p.max_locations === 1 ? "" : "s"}` : ""}</div>
          ${p.monthly_usd != null
            ? `<div class="plan-price">${money(p.monthly_usd)} a month, flat</div>` : ""}
        </div>
        <ul>
          <li>Breach detection on every reading</li>
          <li>The whole on-call roster texted</li>
          <li data-has="${p.voice_escalation ? "yes" : "no"}">
            Spoken escalation call until somebody presses 1</li>
          <li data-has="${p.predictive_forecasting ? "yes" : "no"}">
            Forecasting — warned before it breaches</li>
          <li>Tamper-evident log and claim packets</li>
          <li>${p.term_days < 30
                ? p.term_days + "-day trial"
                : "Annual term"}</li>
        </ul>
        <!-- Every one of these starts a trial -- signup cannot hand out a
             paid plan, and should not -- so none of them may promise one.
             "Start on Enterprise" used to produce a 14-day, 5-sensor trial
             with nothing on the page to say so. -->
        <a class="btn ${p.plan === "growth" ? "btn-primary" : ""}"
           href="/signup">${p.plan === "trial"
             ? "Start the free trial"
             : "Start free, then move to " + esc(p.name)}</a>
      </article>`).join("");
  } catch (err) {
    grid.innerHTML = `<p class="empty">Plans could not be loaded
      (${esc(err.message)}). They are served from
      <code>/api/licenses/plans</code>.</p>`;
  }
}

paintSectors();
paintPlans();
