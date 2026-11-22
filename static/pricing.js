/* The price calculator on /pricing.
 *
 * It holds no price of its own. Every rate is rendered into the page from
 * pricing.py (the same rate card the invoices are built from) as a
 * data-rate attribute, and this file only adds them up. The sum lives in
 * the two functions below, which tests/test_pricing_page.py also runs
 * under Node against pricing.quote_monthly, so the page and the invoice
 * cannot quietly disagree. The chain discount tiers and the yearly months
 * are read from the page the same way.
 */
"use strict";

const whole = (n) => Math.max(0, Math.floor(Number(n) || 0));

/* lines: [{rate, count}], addOns: [{rate, count?}] -> the monthly total in
   dollars, rounded to the cent. Negative or non-numeric counts count as
   zero; an extra with no count is counted once. */
function monthlyTotal(lines, addOns) {
  let cents = 0;
  for (const line of lines) {
    cents += Math.round(Number(line.rate) * 100) * whole(line.count);
  }
  for (const extra of addOns) {
    const count = extra.count === undefined ? 1 : whole(extra.count);
    cents += Math.round(Number(extra.rate) * 100) * count;
  }
  return cents / 100;
}

/* "5:10,20:20" (from this many locations, percent off) -> [[5, 10], [20, 20]].
   The tiers are rendered into the page from pricing.CHAIN_DISCOUNT_TIERS. */
function parseTiers(text) {
  return String(text || "").split(",").filter(Boolean).map((pair) => {
    const [from, off] = pair.split(":");
    return [Number(from), Number(off)];
  });
}

/* The chain discount, as pricing.chain_discount works it out, in cents:
   percent off the location prices for a chain of `locations`, and no
   chain paying more than a bigger one would. Returns {percent, cents,
   cappedAt}: cents is the discount, cappedAt the bigger chain whose price
   this one was held to (or null). */
function chainDiscount(totalCents, locations, tiers) {
  const places = whole(locations);
  let percent = 0;
  for (const [from, off] of tiers || []) {
    if (places >= from) percent = off;
  }
  let after = totalCents - Math.round(totalCents * percent / 100);
  let cappedAt = null;
  if (places) {
    for (const [from, off] of tiers || []) {
      if (from <= places) continue;
      const ceiling = Math.round(totalCents * from * (100 - off) / (places * 100));
      if (ceiling < after) { after = ceiling; cappedAt = from; }
    }
  }
  return { percent, cents: totalCents - after, cappedAt };
}

/* The per-location trades on the rate card: the lines the chain discount
   comes off, their locations counted together. */
function chainOf(spec) {
  const lines = (spec.lines || []).filter((line) => !line.custom
    && line.included !== undefined && line.included !== null);
  const locations = lines.reduce((n, line) => n + whole(line.count), 0);
  const cents = lines.reduce(
    (sum, line) => sum + Math.round(Number(line.rate) * 100) * whole(line.count), 0);
  return chainDiscount(cents, locations, spec.chain);
}

/* The whole quote, as pricing.quote_monthly works it out.
   spec: {starter: rate or null,
          lines: [{rate, count, custom?, included?, each?}],
          addOns: [{rate, basis}], locations, extraRate, chain}
   On Starter the flat rate is the one line and every extra is for its one
   location; on the rate card an extra priced per location is counted
   once per location. A per-location trade (a line with `included`) adds
   extraRate for each unit past `included` at each of its locations, and
   its location prices take the chain discount (`chain`: the tiers).
   A trade priced by quote (`custom`) has no figure on the page, so a quote
   with any of it in is null: the page says "custom" instead of a total. */
function quote(spec) {
  const extras = spec.addOns || [];
  if (spec.starter !== null && spec.starter !== undefined) {
    return monthlyTotal([{ rate: spec.starter, count: 1 }],
      extras.map((a) => ({ rate: a.rate, count: 1 })));
  }
  const lines = spec.lines || [];
  if (lines.some((line) => line.custom && whole(line.count))) return null;
  const priced = lines.filter((line) => !line.custom);
  const pastAllowance = priced
    .filter((line) => line.included !== undefined && line.included !== null)
    .map((line) => ({
      rate: spec.extraRate,
      count: whole(line.count) * Math.max(0, whole(line.each) - whole(line.included)),
    }));
  const gross = monthlyTotal(priced.concat(pastAllowance), extras.map((a) => ({
    rate: a.rate,
    count: a.basis === "per location" ? whole(spec.locations) : 1,
  })));
  return Math.round(gross * 100 - chainOf(spec).cents) / 100;
}

/* Units past the allowance on one line, for the breakdown. */
function extraUnits(line) {
  if (line.included === undefined || line.included === null) return 0;
  return whole(line.count) * Math.max(0, whole(line.each) - whole(line.included));
}

/* A year at the monthly price: twelve monthly bills (`full`), or paid
   yearly (`yearly`), which is `charged` months for `covered`. Both counts
   come from the page (pricing.YEARLY_MONTHS_CHARGED and _COVERED). */
function yearly(monthly, charged, covered) {
  const cents = Math.round(Number(monthly) * 100);
  const full = cents * whole(covered) / 100;
  const paid = cents * whole(charged) / 100;
  return { full, yearly: paid, saving: Math.round((full - paid) * 100) / 100 };
}

/* "Growth:Growth:50,enterprise:Enterprise:1000" -> [{key, name, max}],
   rendered into the page from PLAN_TIERS. */
function parsePlans(text) {
  return String(text || "").split(",").filter(Boolean).map((item) => {
    const [key, name, max] = item.split(":");
    return { key, name, max: Number(max) };
  });
}

/* Units on the rate card: per-location lines count each location's
   fridges and freezers; anything else counts its units. */
function cardUnits(lines) {
  return (lines || []).reduce((n, line) => n + (line.included !== undefined
    && line.included !== null ? whole(line.count) * whole(line.each) : whole(line.count)), 0);
}

/* The cheapest plan that fits, and what it costs a month. Starter fits
   one location of one of its trades (terms.verticals) with no more than
   terms.units units, and only the extras it carries; it is chosen when it
   costs no more than the rate card would. Otherwise the smallest rate-card
   plan (terms.plans) whose unit limit holds them. Prices come from quote(),
   so nothing is priced here. Returns {plan, name, monthly} (monthly null
   for a quote). */
function cheapestPlan(spec, terms) {
  const lines = (spec.lines || []).filter((line) => whole(line.count) > 0);
  const extras = spec.addOns || [];
  const card = quote(Object.assign({}, spec, { starter: null }));
  const one = lines.length === 1 ? lines[0] : null;
  const starterFits = Boolean(one) && !one.custom
    && (terms.verticals || []).includes(one.sector)
    && whole(one.count) === 1
    && whole(one.each) <= whole(terms.units)
    && extras.every((extra) => extra.starter === "yes");
  if (starterFits) {
    const flat = quote(Object.assign({}, spec, { starter: terms.starterRate }));
    if (card === null || flat <= card) {
      return { plan: "starter", name: terms.starterName || "Starter", monthly: flat };
    }
  }
  const units = cardUnits(lines);
  const plans = terms.plans || [];
  const fit = plans.find((plan) => units <= plan.max) || plans[plans.length - 1]
    || { key: "card", name: "The rate card" };
  return { plan: fit.key, name: fit.name, monthly: card };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { monthlyTotal, quote, yearly, extraUnits, chainDiscount,
                     parseTiers, chainOf, cheapestPlan, parsePlans, cardUnits };
}

if (typeof document !== "undefined") {
  // Whole dollars as "$446", anything else to the cent: "$4,816.80".
  const money = (n) => "$" + Number(n).toLocaleString("en-US", {
    minimumFractionDigits: Number.isInteger(Number(n)) ? 0 : 2,
    maximumFractionDigits: 2 });

  const form = document.getElementById("calc");
  const rows = document.getElementById("calc-rows");

  const optionOf = (row) => {
    const select = row.querySelector(".calc-sector");
    return select.options[select.selectedIndex];
  };

  const label = (row) => {
    const option = optionOf(row);
    row.querySelector(".calc-unit").textContent =
      "How many " + (option ? option.dataset.plural : "units");
    // Units at each location matter only where a location has an allowance.
    row.querySelector(".calc-each-field").hidden =
      !(option && option.dataset.included);
  };

  const planMode = () => {
    const picked = form.querySelector('input[name="calc-plan"]:checked');
    return picked ? picked.value : "auto";
  };
  const terms = {
    starterRate: form.dataset.starterRate,
    units: form.dataset.starterUnits,
    verticals: String(form.dataset.starterVerticals || "").split(",").filter(Boolean),
    plans: parsePlans(form.dataset.cardPlans),
  };

  const paint = () => {
    const mode = planMode();
    const chosenStarter = mode === "starter";
    // Starter chosen by hand is one location with its own short list of
    // extras: the trade rows and the estate extras step aside rather than
    // quietly not count. "The cheapest that fits" keeps every row.
    rows.hidden = chosenStarter;
    document.getElementById("calc-add").hidden = chosenStarter;
    document.getElementById("calc-starter-note").hidden = !chosenStarter;
    document.getElementById("calc-locations-field").hidden = chosenStarter;
    form.querySelectorAll(".calc-addon").forEach((box) => {
      const allowed = !chosenStarter || box.dataset.starter === "yes";
      box.disabled = !allowed;
      if (!allowed) box.checked = false;
    });
    const lines = [...rows.querySelectorAll(".calc-row")].map((row) => {
      const option = optionOf(row);
      const data = option ? option.dataset : {};
      return {
        sector: option ? option.value : "",
        name: option ? option.textContent.replace(/ \(custom pricing\)$/, "") : "",
        unit: data.unit || "",
        plural: data.plural || "",
        rate: data.rate || 0,
        custom: data.custom === "yes",
        included: data.included === undefined ? null : data.included,
        each: row.querySelector(".calc-each").value,
        count: row.querySelector(".calc-count").value,
      };
    });
    const extras = [...form.querySelectorAll(".calc-addon:checked")].map((box) => ({
      name: box.dataset.name, rate: box.dataset.rate, basis: box.dataset.basis,
      starter: box.dataset.starter,
    }));
    const locations = document.getElementById("calc-locations").value;
    const extraRate = form.dataset.extraRate;
    const chain = parseTiers(form.dataset.chain);
    const base = { starter: null, lines, addOns: extras, locations, extraRate, chain };
    // The plan this quote is for: picked by hand, or the cheapest that
    // fits what was typed in, named above the figure ("Starter fits you").
    const best = cheapestPlan(base, terms);
    const starter = chosenStarter || (mode === "auto" && best.plan === "starter");
    const starterRate = starter ? form.dataset.starterRate : null;
    const spec = Object.assign({}, base, { starter: starterRate });
    const monthly = quote(spec);
    const fitLine = document.getElementById("calc-fit");
    fitLine.hidden = mode !== "auto" || best.monthly === null;
    fitLine.textContent = fitLine.hidden ? ""
      : `${best.name} fits you: ${money(best.monthly)} a month`;
    const custom = monthly === null;
    const charged = form.dataset.monthsCharged;
    const covered = form.dataset.monthsCovered;
    const picked = form.querySelector('input[name="calc-pay"]:checked');
    const payYearly = Boolean(picked && picked.value === "yearly");
    const year = custom ? null : yearly(monthly, charged, covered);
    document.getElementById("calc-period").textContent =
      payYearly ? "Each year, paid up front" : "Each month";
    document.getElementById("calc-total").textContent =
      custom ? "Ask for a quote" : money(payYearly ? year.yearly : monthly);
    document.getElementById("calc-custom").hidden = !custom;

    const list = document.getElementById("calc-lines");
    list.replaceChildren();
    const add = (left, right) => {
      const li = document.createElement("li");
      const a = document.createElement("span");
      const b = document.createElement("span");
      a.textContent = left; b.textContent = right;
      li.append(a, b);
      list.append(li);
    };
    if (starter) {
      add("Starter: one location", money(monthlyTotal([{ rate: starterRate, count: 1 }], [])));
    } else {
      for (const line of lines) {
        const count = whole(line.count);
        if (!count) continue;
        const what = `${line.name}: ${count} ${count === 1 ? line.unit : line.plural}`;
        if (line.custom) {
          add(what, "Quote");
          continue;
        }
        add(what, money(monthlyTotal([line], [])));
        const past = extraUnits(line);
        if (past) {
          add(`${past} extra unit${past === 1 ? "" : "s"} past ${whole(line.included)} at a ${line.unit}`,
              money(monthlyTotal([{ rate: extraRate, count: past }], [])));
        }
      }
    }
    if (!starter) {
      const off = chainOf(spec);
      if (off.cents > 0) {
        const places = lines.filter((line) => !line.custom && line.included !== null)
          .reduce((n, line) => n + whole(line.count), 0);
        add(`Chain discount (${places} locations): \u2212${off.percent}%`
              + (off.cappedAt ? `, held to the ${off.cappedAt}-location price` : ""),
            "\u2212" + money(off.cents / 100));
      }
    }
    for (const extra of extras) {
      const places = starter ? 1 : whole(locations);
      const count = extra.basis === "per location" ? places : 1;
      add(extra.basis === "per location" && count !== 1
            ? `${extra.name} × ${count}` : extra.name,
          money(monthlyTotal([], [{ rate: extra.rate, count }])));
    }

    document.getElementById("calc-year").textContent = custom ? "" : (payYearly
      ? `${money(monthly)} a month × ${whole(charged)} months, covering ${whole(covered)}. `
        + `${money(year.saving)} less than paying monthly.`
      : `${money(year.full)} a year paid monthly, or ${money(year.yearly)} paid yearly `
        + `(${whole(covered) - whole(charged)} months free).`);

    rows.querySelectorAll(".calc-remove").forEach((button) => {
      button.hidden = rows.children.length < 2;
    });
  };

  rows.querySelectorAll(".calc-row").forEach(label);

  form.addEventListener("input", (event) => {
    const row = event.target.closest(".calc-row");
    if (row) label(row);
    paint();
  });
  form.addEventListener("change", paint);
  form.addEventListener("submit", (event) => event.preventDefault());

  document.getElementById("calc-add").addEventListener("click", () => {
    const first = rows.querySelector(".calc-row");
    const copy = first.cloneNode(true);
    const select = copy.querySelector(".calc-sector");
    // The next trade down the list, so a second row is not a duplicate.
    select.selectedIndex = Math.min(
      select.options.length - 1,
      rows.children.length % select.options.length);
    copy.querySelector(".calc-count").value = "1";
    rows.append(copy);
    label(copy);
    paint();
  });

  rows.addEventListener("click", (event) => {
    const button = event.target.closest(".calc-remove");
    if (!button || rows.children.length < 2) return;
    button.closest(".calc-row").remove();
    paint();
  });

  paint();
}

/* The folds: open on a wide screen, folded on a phone (the page was ten
   thousand pixels long there). A link to something inside a fold
   (/pricing#chains) opens that fold. */
if (typeof document !== "undefined") {
  const wide = () => {
    try { return window.matchMedia("(min-width: 721px)").matches; } catch (_) { return true; }
  };
  if (wide()) {
    document.querySelectorAll("details[data-open-wide]").forEach((d) => { d.open = true; });
  }
  const openFor = () => {
    const id = decodeURIComponent((location.hash || "").slice(1));
    const target = id && document.getElementById(id);
    const fold = target && target.closest("details");
    if (fold) { fold.open = true; target.scrollIntoView(); }
  };
  openFor();
  window.addEventListener("hashchange", openFor);
}
