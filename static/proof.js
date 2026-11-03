"use strict";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));
const $ = (id) => document.getElementById(id);
const pct = (n) => (n === null || n === undefined) ? "—" : n + "%";

const token = location.pathname.split("/").filter(Boolean).pop();
let record = null;

function tiles(r) {
  const answered = r.excursions
    ? `${r.excursions_with_a_recorded_action} of ${r.excursions} answered in writing`
    : "none in this period";
  return [
    ["In band", pct(r.within_band_percent),
     `${Number(r.readings).toLocaleString()} readings over ${r.period_days} days`],
    ["Excursions", String(r.excursions), answered],
    ["Signed off", `${r.excursions_reviewed_by_a_manager}`,
     "reviewed by a named manager"],
    ["Answered in", r.median_minutes_to_answer === null
      ? "—" : `${r.median_minutes_to_answer} min`, "average, to resolution"],
  ];
}

function render(r) {
  record = r;
  $("who").textContent = `${r.company_name} — cold chain record`;
  $("what").textContent =
    `${r.assets_monitored} monitored asset(s), ${r.period_days} days to `
    + `${new Date(r.issued_at).toLocaleDateString()}. Published by the `
    + `operator; checkable by anyone.`;

  $("headline").innerHTML = tiles(r).map(([k, n, s]) => `
    <div class="fig">
      <span class="k">${esc(k)}</span>
      <span class="n">${esc(n)}</span>
      <span class="s">${esc(s)}</span>
    </div>`).join("");

  $("digest").textContent = r.estate_digest;
  $("rows").innerHTML = r.chain.map((c) => `<tr>
      <td>${esc(c.asset)}</td>
      <td class="num">${esc(c.readings)}</td>
      <td class="num">${esc(c.excursions)}</td>
      <td class="hash">${esc(c.digest)}</td>
    </tr>`).join("");

  $("proves").textContent = r.proves;
  $("not").textContent = r.does_not_prove;
  $("foot").innerHTML =
    "Recorded and published through CyberLogix AI. The verification above "
    + "runs against a public endpoint that needs no account, so neither the "
    + "operator nor CyberLogix has to be taken at their word. "
    + (r.signing && r.signing.counter_signed
       ? "This record also carries a counter-signature."
       : "This deployment has no signing key configured, so the record "
         + "carries no counter-signature — the chain still stands on its own.");
  $("page").hidden = false;
}

/* Re-derive the chain in front of whoever is reading, against the same
   public endpoint they could call themselves. A verification the reader
   has to take on trust is not one. */
$("verify").addEventListener("click", async () => {
  const button = $("verify");
  const out = $("verdict");
  button.disabled = true;
  out.className = "verdict";
  out.textContent = "Re-deriving…";
  try {
    const resp = await fetch("/api/vault/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chain: record.chain.map((c) => ({ digest: c.digest })),
        claimed_head: record.estate_digest,
      }),
    });
    const body = await resp.json().catch(() => ({}));
    const ok = body.intact === true || body.verified === true;
    out.className = "verdict " + (ok ? "pass" : "fail");
    out.textContent = ok
      ? "Re-derived and matches what was attested."
      : (body.detail || body.note
         || "The public verifier did not confirm this chain.");
  } catch (err) {
    out.className = "verdict fail";
    out.textContent = "The verifier could not be reached from this browser.";
  } finally {
    button.disabled = false;
  }
});

(async () => {
  try {
    const resp = await fetch(`/api/certificate/public/${encodeURIComponent(token)}`);
    if (!resp.ok) {
      $("err").hidden = false;
      $("err").textContent =
        "No published record answers to this link. It may have been taken "
        + "down or replaced by the operator.";
      return;
    }
    render(await resp.json());
  } catch (err) {
    $("err").hidden = false;
    $("err").textContent = "This record could not be loaded.";
  }
})();
