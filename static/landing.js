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
    const all = body.industries || body;
    // The page names the sectors it shows (data-show); the rest are on /for.
    const wanted = (grid.dataset.show || "").split(/\s+/).filter(Boolean);
    const rows = wanted.length
      ? wanted.map((key) => all.find((r) => r.vertical === key)).filter(Boolean)
      : all;
    // A trade priced by quote has no figure in the catalogue at all; its
    // card says so and offers the support address (set on the grid by the
    // server), as a link and as text that can be copied.
    const talkHref = grid.dataset.talkHref || "/help#help";
    const talkAddress = grid.dataset.talkAddress || "";
    const talk = `<a class="talk-link" href="${esc(talkHref)}">Talk to us</a>${
      talkAddress ? ` <span class="talk-address">${esc(talkAddress)}</span>` : ""}`;
    grid.innerHTML = rows.map(r => `
      <article class="sector">
        <p class="sector-fault">${esc(r.catastrophe)}</p>
        <h3>${esc(r.name)}</h3>
        <p class="sector-line">${esc(r.slogan)}</p>
        ${r.monthly_usd != null ? `<div class="sector-price">
          <b>${money(r.monthly_usd)}</b>
          <span>per ${esc(r.billing_unit || r.asset_noun || "unit")} / month</span>
        </div>` : `<div class="sector-price sector-custom">
          <b>Custom pricing</b>
          <span>Ask for a quote. ${talk}</span>
        </div>`}
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
    const body = await res.json();
    const plans = body.plans || [];
    // Each plan's own lines, from pricing.plan_features: what it gives and
    // what it does not, so no two cards read the same.
    grid.innerHTML = plans.map(p => `
      <article class="plan" data-featured="${p.plan === "growth" ? "yes" : "no"}">
        <div>
          <div class="plan-name">${esc(p.name)}</div>
          ${p.monthly_usd != null
            ? `<div class="plan-price">${money(p.monthly_usd)} a month, flat</div>`
            : p.plan === "trial" ? `<div class="plan-price">Free</div>`
              : `<div class="plan-price">Priced by the rate card</div>`}
        </div>
        <ul>${(p.features || []).map(f => `<li${/^No /.test(f) ? ' data-has="no"' : ""}>${esc(f)}</li>`).join("")}</ul>
        <!-- Every one of these starts a trial -- signup cannot hand out a
             paid plan, and should not -- so none of them may promise one.
             "Start on Enterprise" used to produce a 14-day, 5-sensor trial
             with nothing on the page to say so. -->
        <a class="btn ${p.plan === "growth" ? "btn-primary" : ""}"
           href="/signup">${p.plan === "trial"
             ? "Start the free trial"
             : "Start free, then move to " + esc(p.name)}</a>
      </article>`).join("");
    const addons = document.getElementById("plans-addons");
    if (addons && body.add_ons) addons.textContent = body.add_ons;
  } catch (err) {
    grid.innerHTML = `<p class="empty">Plans could not be loaded
      (${esc(err.message)}). They are served from
      <code>/api/licenses/plans</code>.</p>`;
  }
}

paintSectors();
paintPlans();
