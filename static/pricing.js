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

/* lines: [{rate, count}], addOns: [{rate}] -> the monthly total in dollars,
   rounded to the cent. Negative or non-numeric counts count as zero. */
function monthlyTotal(lines, addOns) {
  let cents = 0;
  for (const line of lines) {
    const count = Math.max(0, Math.floor(Number(line.count) || 0));
    cents += Math.round(Number(line.rate) * 100) * count;
  }
  for (const extra of addOns) {
    cents += Math.round(Number(extra.rate) * 100);
  }
  return cents / 100;
}

/* A year, at the monthly price, with and without paying ahead. */
function yearly(monthly, discountPercent) {
  const full = Math.round(monthly * 12 * 100) / 100;
  const off = Math.round(full * Number(discountPercent || 0)) / 100;
  return { full, prepaid: Math.round((full - off) * 100) / 100 };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { monthlyTotal, yearly };
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

  const paint = () => {
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
      name: box.dataset.name, rate: box.dataset.rate,
    }));

    const monthly = monthlyTotal(lines, extras);
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
    for (const line of lines) {
      const count = Math.max(0, Math.floor(Number(line.count) || 0));
      if (!count) continue;
      add(`${line.name}: ${count} ${count === 1 ? line.unit : line.plural}`,
          money(monthlyTotal([line], [])));
    }
    for (const extra of extras) add(extra.name, money(monthlyTotal([], [extra])));

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
