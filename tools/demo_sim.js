
/* ---- the simulation -------------------------------------------------
   A frozen page is a screenshot with a cursor. This walks the estate
   forward so the thing reads as alive: temperatures drift, the back-bar
   freezer runs its defrost and comes back, and the clubhouse walk-in
   keeps climbing because it is genuinely broken.

   It is a simulation and the banner says so. Nothing here is part of
   the product -- the product is everything above this line, rendering
   whatever it is handed. */

const SIM = JSON.parse(JSON.stringify(DEMO["/api/console/overview"].body));
let simTick = 0;

/* Per unit: how it behaves. A story rather than noise, because random
   walks look like a screensaver and nobody learns anything from one. */
const BEHAVIOUR = {
  "CLUB-WALKIN-1": { kind: "failing", rate: 0.55 },
  "BACKBAR-1":     { kind: "defrost", period: 14, warmFor: 3, warm: 38, cold: -2 },
  "STORE118-WALKIN": { kind: "failing", rate: 0.22 },
  "MY-AURELIA-ENG":  { kind: "failing", rate: 0.30 },
  "RACK-A7":       { kind: "failing", rate: 0.18 },
};

function jitter(size) { return (Math.random() - 0.5) * size; }

function advance(unit) {
  const plan = BEHAVIOUR[unit.sensor_id] || { kind: "steady" };
  let next = unit.last_temperature;

  if (plan.kind === "failing") {
    // Warming towards ambient, fast at first and flattening: a dead
    // compressor does not climb for ever, it stops at room temperature.
    const ambient = 68;
    next += (ambient - next) * 0.035 + plan.rate * 0.35 + jitter(0.25);
  } else if (plan.kind === "defrost") {
    const phase = simTick % plan.period;
    const target = phase < plan.warmFor ? plan.warm : plan.cold;
    next += (target - next) * 0.55 + jitter(0.4);
  } else {
    next += jitter(0.5);
  }

  next = Math.round(next * 10) / 10;
  unit.last_temperature = next;
  unit.last_temperature_display = next;
  unit.spark = (unit.spark || []).slice(-11).concat([next]);
  unit.last_seen = new Date().toISOString().replace(/\.\d+Z$/, "Z");

  const over = unit.danger_above != null && next > unit.danger_above;
  const under = unit.danger_below != null && next < unit.danger_below;
  unit.breaching = over || under;

  // Trend from the last few samples, so the forecast column moves with
  // the reading rather than staying at whatever it was captured at.
  const tail = unit.spark.slice(-4);
  const perSample = tail.length > 1
    ? (tail[tail.length - 1] - tail[0]) / (tail.length - 1) : 0;
  unit.trend_f_per_hour = Math.round(perSample * 12 * 100) / 100;

  if (unit.breaching) {
    unit.risk_level = "critical";
    unit.hours_until_breach = 0;
  } else if (unit.danger_above != null && unit.trend_f_per_hour > 0.05) {
    const hours = (unit.danger_above - next) / unit.trend_f_per_hour;
    unit.hours_until_breach = Math.max(0, Math.round(hours * 100) / 100);
    unit.risk_level = hours <= 1 ? "critical" : hours <= 6 ? "high"
      : hours <= 24 ? "elevated" : "low";
  } else {
    unit.hours_until_breach = null;
    unit.risk_level = "stable";
  }
  return unit;
}

function tick() {
  simTick += 1;
  SIM.sensors.forEach(advance);

  const breaching = SIM.sensors.filter((u) => u.breaching);
  SIM.summary = {
    ...SIM.summary,
    sensors_breaching: breaching.length,
    readings_logged: (SIM.summary.readings_logged || 0) + SIM.sensors.length,
    open_incidents: (SIM.incidents || []).filter(
      (i) => i.state !== "resolved").length,
  };
  SIM.generated_at = new Date().toISOString().replace(/\.\d+Z$/, "Z");

  // A unit that crosses the line while somebody is watching should open
  // an incident in front of them. That is the moment worth seeing.
  breaching.forEach((unit) => {
    const already = (SIM.incidents || []).some(
      (i) => i.sensor_id === unit.sensor_id && i.state !== "resolved");
    if (already) return;
    SIM.incidents.unshift({
      incident_id: `INC-SIM-${simTick}-${unit.sensor_id}`,
      sensor_id: unit.sensor_id,
      tenant_id: unit.tenant_id,
      state: "open",
      temperature_fahrenheit: unit.last_temperature,
      temperature_display: unit.last_temperature_display,
      temperature_unit: unit.temperature_unit,
      industry_vertical: unit.industry_vertical,
      industry_name: unit.industry_name,
      catastrophe_type: unit.catastrophe,
      opened_at: SIM.generated_at,
      minutes_open: 0,
      notified_count: 0,
      sms_fanout: [], voice_fanout: [],
      sms_delivery: null, voice_delivery: null,
      acknowledged_at: null, acknowledged_by: null,
      resolved_at: null, voice_escalated_at: null,
      corrective_action: "", corrective_action_by: "",
      corrective_action_at: null, product_disposition: "",
      reviewed_by: "", reviewed_at: null,
      record_complete: false, record_missing: ["corrective action"],
      breach_details: `${unit.last_temperature_display}° against a limit of `
        + `${unit.danger_above_display}°`,
      dispatched_sms_text: "", voice_script: "",
      sms_dispatch_source: "simulated", voice_dispatch_source: "simulated",
    });
  });

  DEMO["/api/console/overview"] = { status: 200, body: SIM };

  // Two pages share this harness and they redraw by different names:
  // the console has refresh(), the plain view has load(). Calling only
  // one left the other frozen while its data moved underneath it, which
  // looks exactly like a page that has crashed.
  const redraw = (typeof window.refresh === "function" && window.refresh)
    || (typeof window.load === "function" && window.load);
  if (redraw) {
    try {
      const out = redraw();
      if (out && typeof out.catch === "function") out.catch(() => {});
    } catch (e) {}
  }
}

// Faster than the product's own twenty seconds, because a demo watched
// for ninety seconds has to show a minute of estate life, not ninety
// seconds of it.
setInterval(tick, 2600);
