/* The price calculator on /pricing.
 *
 * It holds no price of its own. Every rate is rendered into the page from
 * pricing.py (the same rate card the invoices are built from) as a
 * data-rate attribute, and this file only adds them up. The sum lives in
 * the two functions below, which tests/test_pricing_page.py also runs
 * under Node against pricing.quote_monthly, so the page and the invoice
 * cannot quietly disagree.
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

/* The whole quote, as pricing.quote_monthly works it out.
   spec: {starter: rate or null, lines: [{rate, count}],
          addOns: [{rate, basis}], locations}
   On Starter the flat rate is the one line and every extra is for its one
   location; on the rate card an extra priced per location is counted
   once per location. */
function quote(spec) {
  const extras = spec.addOns || [];
  if (spec.starter !== null && spec.starter !== undefined) {
    return monthlyTotal([{ rate: spec.starter, count: 1 }],
      extras.map((a) => ({ rate: a.rate, count: 1 })));
  }
  return monthlyTotal(spec.lines || [], extras.map((a) => ({
    rate: a.rate,
    count: a.basis === "per location" ? whole(spec.locations) : 1,
  })));
}

/* A year, at the monthly price, with and without paying ahead. */
function yearly(monthly, discountPercent) {
  const full = Math.round(monthly * 12 * 100) / 100;
  const off = Math.round(full * Number(discountPercent || 0)) / 100;
  return { full, prepaid: Math.round((full - off) * 100) / 100 };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { monthlyTotal, quote, yearly };
}

if (typeof document !== "undefined") {
  const money = (n) => "$" + Number(n).toLocaleString("en-US", {
    minimumFractionDigits: 0, maximumFractionDigits: 2 });

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
  };

  const onStarter = () => {
    const picked = form.querySelector('input[name="calc-plan"]:checked');
    return Boolean(picked && picked.value === "starter");
  };

  const paint = () => {
    const starter = onStarter();
    // Starter is one location with its own short list of extras: the trade
    // rows and the estate extras step aside rather than quietly not count.
    rows.hidden = starter;
    document.getElementById("calc-add").hidden = starter;
    document.getElementById("calc-starter-note").hidden = !starter;
    document.getElementById("calc-locations-field").hidden = starter;
    form.querySelectorAll(".calc-addon").forEach((box) => {
      const allowed = !starter || box.dataset.starter === "yes";
      box.disabled = !allowed;
      if (!allowed) box.checked = false;
    });
    const lines = [...rows.querySelectorAll(".calc-row")].map((row) => {
      const option = optionOf(row);
      return {
        name: option ? option.textContent : "",
        unit: option ? option.dataset.unit : "",
        plural: option ? option.dataset.plural : "",
        rate: option ? option.dataset.rate : 0,
        count: row.querySelector(".calc-count").value,
      };
    });
    const extras = [...form.querySelectorAll(".calc-addon:checked")].map((box) => ({
      name: box.dataset.name, rate: box.dataset.rate, basis: box.dataset.basis,
    }));
    const locations = document.getElementById("calc-locations").value;
    const starterRate = starter ? form.dataset.starterRate : null;

    const monthly = quote({ starter: starterRate, lines, addOns: extras, locations });
    const year = yearly(monthly, form.dataset.prepayDiscount);
    document.getElementById("calc-total").textContent = money(monthly);

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
        add(`${line.name}: ${count} ${count === 1 ? line.unit : line.plural}`,
            money(monthlyTotal([line], [])));
      }
    }
    for (const extra of extras) {
      const places = starter ? 1 : whole(locations);
      const count = extra.basis === "per location" ? places : 1;
      add(extra.basis === "per location" && count !== 1
            ? `${extra.name} × ${count}` : extra.name,
          money(monthlyTotal([], [{ rate: extra.rate, count }])));
    }

    document.getElementById("calc-year").textContent =
      `${money(year.full)} a year, or ${money(year.prepaid)} paid a year ahead.`;

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
