"""One printable page per industry, to hand to somebody across a counter.

The industry page (/for/<trade>) is for a reader who found us. This is
for the one who did not: a sheet of paper a salesperson, a partner's
engineer or an owner leaves behind, which has to say on one side of A4
or Letter what goes wrong, what we do about it, where the alarm is set,
what the alert says, what it costs and how to try it.

Every figure on it is read from the code that does the thing: the rate
card and terms from pricing.py, the plans from domain.PLAN_TIERS, the
alarm limits from the industry profile, the timings from domain.py, and
the example alert from wording.py, which is the function that writes the
real one. Nothing here holds a number of its own except how far past the
limit the made-up example reading is, and the page says it is made up.
tests/test_sales_sheet.py holds each figure to its source.
"""

from __future__ import annotations

import html
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

# How far past its limit the example alert's reading is, in °F. The only
# figure on the sheet that is not the product's own, and it is labelled as
# an example wherever it shows.
EXAMPLE_EXCURSION_F = 8.0

# Whose the example unit is. The sheet is about the reader's own place.
EXAMPLE_PLACE = "your business"


def _money(amount: float) -> str:
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"


def _deg(value_f: float, unit: str) -> str:
    """32.0 in "F" -> "32°F"; in "C" -> "0°C". Rounded to one place."""
    from domain import display_temperature

    shown = display_temperature(value_f, unit)
    return f"{round(shown, 1):g}°{unit}" if shown is not None else ""


def _both(value_f: float) -> str:
    """A limit in both scales, so either reader finds the one they use."""
    return f"{_deg(value_f, 'F')} ({_deg(value_f, 'C')})"


def _minutes_list(values) -> str:
    words = [str(v) for v in values]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _term(days: int) -> str:
    if days % 365 == 0:
        years = days // 365
        return f"{years} year" + ("" if years == 1 else "s")
    return f"{days} days, free"


def limits(vertical: str) -> Dict[str, Any]:
    """Where the alarm is set for this trade, from its profile."""
    from domain import INDUSTRY_PROFILES

    profile = INDUSTRY_PROFILES[vertical]
    above, below = profile.get("danger_above"), profile.get("danger_below")
    if above is not None and below is not None:
        rule = f"below {_both(below)} or above {_both(above)}"
    elif above is not None:
        rule = f"above {_both(above)}"
    else:
        rule = f"below {_both(below)}"
    return {"above_f": above, "below_f": below, "rule": rule}


def example_alert(vertical: str) -> Dict[str, Any]:
    """The text, the email subject and the call, as the product writes them.

    Built by wording.py from a made-up unit a few degrees past this trade's
    own limit, so the example cannot drift from what a real alert says.
    """
    from domain import INDUSTRY_PROFILES, VOICE_ESCALATION_GRACE_MINUTES
    from records import Sensor
    from wording import alert_email, alert_sms, voice_script

    profile = INDUSTRY_PROFILES[vertical]
    above, below = profile.get("danger_above"), profile.get("danger_below")
    if above is not None:
        reading_f = above + EXAMPLE_EXCURSION_F
    else:
        reading_f = below - EXAMPLE_EXCURSION_F
    name = f"{profile['asset_noun'][:1].upper()}{profile['asset_noun'][1:]} 2"
    sensor = Sensor(sensor_id=name, tenant_id="", industry_vertical=vertical,
                    location_name=name, registered_at=None)  # type: ignore[arg-type]
    tenant = SimpleNamespace(company_name=EXAMPLE_PLACE, temperature_unit="F")
    incident = SimpleNamespace(
        temperature_fahrenheit=reading_f,
        incident_id="1",
        opened_at=None,
        minutes_open=lambda: float(VOICE_ESCALATION_GRACE_MINUTES),
    )
    subject, _ = alert_email(tenant, sensor, incident, "F")
    return {
        "unit_name": name,
        "reading_f": reading_f,
        "sms": alert_sms(tenant, sensor, reading_f, "F"),
        "email_subject": subject,
        "call": voice_script(tenant, sensor, incident, "F"),
    }


def facts(vertical: str) -> Dict[str, Any]:
    """Everything the sheet says, as values, for the page and the tests."""
    from domain import (
        DISPATCH_SLA_SECONDS,
        PLAN_TIERS,
        SENSOR_OFFLINE_AFTER_MINUTES,
        SMS_RETRY_AFTER_MINUTES,
        VOICE_ESCALATION_GRACE_MINUTES,
    )
    from industries import page
    from helpdesk import talk_to_us
    from pricing import (
        ADD_ONS,
        ANNUAL_ESCALATOR_PERCENT,
        CHAIN_DISCOUNT_TIERS,
        CUSTOM_PRICE_LABEL,
        EXTRA_UNIT_MONTHLY_USD,
        PRICE_BOOK,
        SETUP_FEE_PER_SITE_USD,
        STARTER_VERTICALS,
        headline,
        per_site,
        public_price,
        starter_terms,
        units_per_location,
        yearly_terms,
    )

    words = page(vertical)
    rate = PRICE_BOOK[vertical]
    trial = PLAN_TIERS["trial"]
    return {
        "vertical": vertical,
        "words": words,
        "limits": limits(vertical),
        "alert": example_alert(vertical),
        "timings": {
            "dispatch_seconds": DISPATCH_SLA_SECONDS,
            "call_after_minutes": VOICE_ESCALATION_GRACE_MINUTES,
            "sms_retry_minutes": tuple(SMS_RETRY_AFTER_MINUTES),
            "offline_after_minutes": SENSOR_OFFLINE_AFTER_MINUTES,
        },
        # A trade priced by quote carries no figure here: the sheet is
        # handed to strangers. `monthly_usd` is None and `custom` says why.
        "price": {
            "monthly_usd": public_price(vertical),
            "custom": public_price(vertical) is None,
            "custom_label": CUSTOM_PRICE_LABEL,
            "talk": talk_to_us(),
            "unit": rate["unit"],
            "per_site": per_site(vertical),
            "units_per_location": units_per_location(vertical),
            "extra_unit_usd": EXTRA_UNIT_MONTHLY_USD if per_site(vertical) else None,
            "setup_per_site_usd": SETUP_FEE_PER_SITE_USD,
            "yearly": yearly_terms(),
            # Chain discounts are for the per-location trades with a
            # published price; a trade priced by quote gets its own quote.
            "chain_tiers": ([{"from": start, "percent": off,
                              "each_usd": round(public_price(vertical) * (1 - off / 100.0), 2)}
                             for start, off in CHAIN_DISCOUNT_TIERS]
                            if per_site(vertical) and public_price(vertical) is not None
                            else []),
            "escalator_percent": ANNUAL_ESCALATOR_PERCENT,
        },
        "plans": [
            {"name": t["name"], "max_sensors": t["max_sensors"],
             "calls": bool(t["voice_escalation"]), "term_days": t["term_days"]}
            for t in PLAN_TIERS.values()
        ],
        "add_ons": [
            {"name": a["name"], "monthly_usd": a["monthly_usd"], "basis": a["basis"],
             "includes": a.get("includes")}
            for a in ADD_ONS.values()
        ],
        # Starter is offered on the sheets for the trades it is for.
        "starter": starter_terms() if vertical in STARTER_VERTICALS else None,
        "trial": {
            "days": trial["term_days"],
            "sensors": trial["max_sensors"],
            "calls": bool(trial["voice_escalation"]),
            "card_required": headline()["card_required"],
        },
    }


def render_html(vertical: str, base: str, version: str = "") -> str:
    """The sheet's body as HTML. Every interpolated value is escaped."""
    e = html.escape
    f = facts(vertical)
    w, lim, alert, t, p, trial = (f["words"], f["limits"], f["alert"],
                                  f["timings"], f["price"], f["trial"])
    noun, plural = w["asset_noun"], w["asset_plural"]
    paid_with_calls = [plan["name"] for plan in f["plans"] if plan["calls"]]
    calls_on = " and ".join(paid_with_calls) or "paid plans"

    def li(items: List[str]) -> str:
        return "".join(f"<li>{item}</li>" for item in items)

    per = f"a month per {e(p['unit'])}"
    if p["custom"]:
        talk = p["talk"]
        contact = (f'<a href="{e(talk["href"])}">Talk to us</a>'
                   + (f': <span class="sheet-mono talk-address">{e(talk["address"])}</span>'
                      if talk["address"] else f" ({e(base)}/help)"))
        price_big = f'<p class="sheet-big sheet-custom">{e(p["custom_label"])}</p>'
        price_note = ("Priced for what you run, by quote. " + contact + ".")
    else:
        price_big = (f'<p class="sheet-big">{_money(p["monthly_usd"])} '
                     f'<span>{per}</span></p>')
        price_note = (
            f"Priced per {e(p['unit'])}, for up to {p['units_per_location']} "
            f"fridges and freezers there; each one past {p['units_per_location']} "
            f"is {_money(p['extra_unit_usd'])} a month."
            if p["per_site"] else
            f"Each sensor is billed as one {e(p['unit'])}.")

    plan_rows = "".join(
        f"<tr><td>{e(plan['name'])}</td>"
        f"<td class=\"num\">{plan['max_sensors']:,}</td>"
        f"<td>{'Texts and calls' if plan['calls'] else 'Texts'}</td>"
        f"<td>{e(_term(int(plan['term_days'])))}</td></tr>"
        for plan in f["plans"])
    extras = "".join(
        f"<li>{e(a['name'])}: {_money(a['monthly_usd'])} a month"
        f"{' per location' if a['basis'] == 'per location' else ''}"
        f"{(', includes ' + e(a['includes'])) if a.get('includes') else ''}</li>"
        for a in f["add_ons"])
    setup_line = ("No setup fee." if not p["setup_per_site_usd"] else
                  f"Setup: {_money(p['setup_per_site_usd'])} per site, once, "
                  "on a contract's first bill.")
    chain_line = (
        "<li>Chains: " + ", ".join(f"{t['from']}+ {t['percent']:g}% off"
                                   for t in p["chain_tiers"])
        + " each location.</li>" if p["chain_tiers"] else "")
    starter = f["starter"]
    starter_line = (
        f'<p class="sheet-small sheet-starter">A small caf&eacute; or shop? '
        f"{e(starter['name'])}: {_money(starter['monthly_usd'])} a month for one "
        f"location and up to {starter['max_units']} fridges or freezers, "
        "texts and the site-wide alert included.</p>"
        if starter else "")

    fix_points = [
        f"A text to everyone on call within {t['dispatch_seconds']} seconds "
        f"of a reading out of range.",
        "They reply <b>1</b> to say they have it, and the calls stop.",
        f"Nobody answers in {t['call_after_minutes']} minutes? We phone them, "
        f"and keep phoning ({e(calls_on)}).",
        f"A text that fails is sent again {_minutes_list(t['sms_retry_minutes'])} "
        "minutes later.",
        f"A sensor silent for {t['offline_after_minutes']} minutes: we text "
        "about that too.",
        f"{e(w['paperwork'])}: {e(w['paperwork_detail'].rstrip('.'))}."
        if w.get("paperwork") else "Every reading kept, ready to print.",
    ]

    trial_line = (
        f"Free for {trial['days']} days on up to {trial['sensors']} sensors."
        + (" No card needed." if not trial["card_required"] else "")
        + ("" if trial["calls"] else " Texts during the trial; phone calls "
           "start on a paid plan.")
    )

    return f"""
<article class="sheet" data-vertical="{e(vertical)}">
  <header class="sheet-head">
    <div class="sheet-brand">
      <img src="/static/logo.svg" alt="" width="28" height="28">
      <span>CyberLogix AI</span>
    </div>
    <p class="sheet-for">Temperature alarms for {e(w['name'])}</p>
  </header>

  <h1 class="sheet-title">{e(w['slogan'])}</h1>

  <div class="sheet-grid">
   <div class="sheet-col">
    <section class="sheet-block sheet-problem">
      <h2>The problem</h2>
      <p>{e(w['loses'])}</p>
      <p class="sheet-small">A common cause: {e(w['catastrophe'].lower())}.
        Afterwards, {e(w['asks_after'])} will ask for the records.</p>
    </section>
    <section class="sheet-block sheet-limits">
      <h2>Where the alarm is set</h2>
      <p class="sheet-rule">Each {e(noun)} alarms when it reads
        <b>{e(lim['rule'])}</b>.</p>
      <p class="sheet-small">That is the setting for {e(plural)} out of the
        box. Each one can be set tighter in the console.</p>
    </section>
    <section class="sheet-block sheet-alert">
      <h2>What the alert looks like</h2>
      <p class="sheet-label">The text (example: {e(alert['unit_name'])} at
        {e(_deg(alert['reading_f'], 'F'))})</p>
      <p class="sheet-sms">{e(alert['sms'])}</p>
      <p class="sheet-label">The email subject</p>
      <p class="sheet-mono">{e(alert['email_subject'])}</p>
      <p class="sheet-label">The phone call, if nobody replies
        ({e(calls_on)})</p>
      <p class="sheet-call">&ldquo;{e(alert['call'])}&rdquo;</p>
    </section>
   </div>
   <div class="sheet-col">
    <section class="sheet-block sheet-fix">
      <h2>The fix</h2>
      <p>{e(w['fix'])}</p>
      <ul class="sheet-list">{li(fix_points)}</ul>
    </section>
    <section class="sheet-block sheet-price">
      <h2>What it costs</h2>
      {price_big}
      <p class="sheet-small">{price_note} In US dollars.</p>
      {starter_line}
      <table class="sheet-table">
        <thead><tr><th>Plan</th><th class="num">Sensors</th><th>Alerts</th>
          <th>Term</th></tr></thead>
        <tbody>{plan_rows}</tbody>
      </table>
      <ul class="sheet-list sheet-small">
        <li>{setup_line} Yearly: {e(p['yearly']['label'])}.</li>
        {chain_line}
        <li>Terms longer than a year rise {p['escalator_percent']:g}% a year.</li>
        {extras}
      </ul>
    </section>
   </div>
  </div>

  <section class="sheet-block sheet-trial">
    <div>
      <h2>Try it</h2>
      <p class="sheet-rule">{e(trial_line)}</p>
    </div>
    <div>
      <p class="sheet-url">{e(base)}/signup</p>
      <p class="sheet-small">Prices: {e(base)}/pricing<br>Questions: {e(base)}/help</p>
    </div>
  </section>

  <footer class="sheet-foot">
    <span>CyberLogix AI is a product of MTRX Horizyn.</span>
    <span>{e(base)}/for/{e(vertical)}</span>
    <span>{('Version ' + e(version)) if version else ''}</span>
  </footer>
</article>"""


def sheet_title(vertical: str) -> str:
    from domain import INDUSTRY_PROFILES

    return f"CyberLogix AI for {INDUSTRY_PROFILES[vertical]['name']}: one-page sheet"


def known_sheet(vertical: Optional[str]) -> bool:
    from industries import known

    return bool(vertical) and vertical in known()
