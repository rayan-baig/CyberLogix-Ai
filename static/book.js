"use strict";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));
const $ = (id) => document.getElementById(id);
const money = (n) => "$" + Math.round(Number(n || 0)).toLocaleString();
const pct = (n) => (n === null || n === undefined || n === "")
  ? "—" : (Math.round(Number(n) * 100) / 100) + "%";
/* Unit economics, to the cent. Rounding to whole dollars turned a real
   47c of metered usage into "-$0", which reads as free on the one page
   whose entire subject is what each line costs. */
const cents = (n) => "$" + Math.abs(Number(n || 0)).toFixed(2);
const less = (n) => "−" + cents(n);
const when = (iso) => {
  if (!iso) return "—";
  const at = new Date(iso);
  return isNaN(at) ? String(iso) : at.toLocaleString();
};
const yesno = (v) => (v ? "yes" : "no");

/* The key lives in sessionStorage, not localStorage: this is the
   credential that reads every customer's contact details and what they
   owe, and it should not outlive the tab it was typed into. */
const KEY = "clx-admin-key";
function storedKey() {
  try { return sessionStorage.getItem(KEY) || ""; } catch (err) { return ""; }
}
function rememberKey(value) {
  try { sessionStorage.setItem(KEY, value); } catch (err) { /* private mode */ }
}
function forgetKey() {
  try { sessionStorage.removeItem(KEY); } catch (err) { /* private mode */ }
}

function fail(message) {
  $("err").hidden = false;
  $("err").textContent = message;
}

/* A refused key is the same event whichever panel noticed it, so it is
   handled once here rather than five times badly. */
function locked(resp) {
  return resp.status === 401 || resp.status === 403 || resp.status === 503;
}
function relock(detail) {
  forgetKey();
  $("gate").hidden = false;
  $("board").hidden = true;
  $("refresh").hidden = true;
  $("forget").hidden = true;
  fail(detail || "That key was not accepted.");
}

/* Every call carries the key as a header. Never as a query string: the
   platform credential must not end up in an access log or a referrer. */
async function api(path, options) {
  const opts = options || {};
  const key = storedKey();
  const resp = await fetch(path, {
    method: opts.method || "GET",
    headers: Object.assign(
      { "X-CyberLogix-Admin": key },
      opts.body ? { "Content-Type": "application/json" } : {}
    ),
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  return resp;
}

/* Reads a JSON endpoint, or throws something a person can read. The
   watchdog answers 503 by design when the sweep has stopped, so a panel
   that wants to *show* that has to be allowed to read the body rather
   than be bounced to the gate. */
async function read(path, tolerate) {
  const resp = await api(path);
  if (locked(resp) && !(tolerate || []).includes(resp.status)) {
    const body = await resp.json().catch(() => ({}));
    relock(typeof body.detail === "string" ? body.detail : "");
    throw new Error("locked");
  }
  if (!resp.ok && !(tolerate || []).includes(resp.status)) {
    throw new Error("HTTP " + resp.status);
  }
  return resp.json();
}

/* ---- renderers ------------------------------------------------------
   Everything below takes plain arrays and escapes at the point of use.
   The tiles and the lines share one shape, [label, value, sub], so the
   whole console renders through two functions rather than fifteen. */

/* Two primitives. An optional element is built by a function rather
   than by a conditional inside a template, so every interpolation on
   this page is one escaped expression that can be read at a glance --
   which is the only way the allow-list in test_frontend_safety stays
   worth anything. */
function span(cls, text) {
  return text ? `<span class="${esc(cls)}">${esc(text)}</span>` : "";
}
function div(cls, text) {
  return text ? `<div class="${esc(cls)}">${esc(text)}</div>` : "";
}
function actionButton(it) {
  if (!it.act) return "";
  return `<button type="button" class="act" data-act="${esc(it.act)}"
           data-id="${esc(it.id)}">${esc(it.label)}</button>`;
}

function tilesInto(el, list) {
  $(el).innerHTML = list.map(([k, v, s]) => `
    <div class="total">
      <span class="k">${esc(k)}</span>
      <span class="v">${esc(v)}</span>
      <span class="s">${esc(s)}</span>
    </div>`).join("");
}

function linesInto(el, list) {
  $(el).innerHTML = list.map(([k, v, s]) => `
    <div class="line">
      <span class="k">${esc(k)}</span>
      <span class="v">${esc(v)}</span>
      ${span("s", s)}
    </div>`).join("");
}

/* A list of things that may need an action. `tone` colours the edge,
   `act` and `id` become a button the delegate below picks up. */
function itemsInto(el, items, empty) {
  if (!items.length) {
    $(el).innerHTML = `<p class="empty">${esc(empty)}</p>`;
    return;
  }
  const blank = { tone: "", title: "", detail: "", meta: "", amount: "" };
  $(el).innerHTML = items.map((raw) => {
    const it = Object.assign({}, blank, raw);
    return `
    <article class="item ${esc(it.tone)}">
      <span class="bar" aria-hidden="true"></span>
      <div>
        ${div("title", it.title)}
        ${div("detail", it.detail)}
        ${div("meta", it.meta)}
      </div>
      <div class="right">
        ${span("amount", it.amount)}
        ${actionButton(it)}
      </div>
    </article>`;
  }).join("");
}

function say(el, message, bad) {
  $(el).textContent = message;
  $(el).classList.toggle("bad", !!bad);
}

/* ---- the worklist (the page this file started as) ------------------- */

function totals(book) {
  tilesInto("totals", [
    ["Annual run rate", money(book.arr_usd),
     `${esc(book.paying)} of ${esc(book.accounts)} accounts paying`],
    ["Monthly", money(book.mrr_usd), "at today's registered units"],
    ["Owed", money(book.cash_outstanding_usd), "invoiced and unpaid"],
    ["At risk", money(book.annual_revenue_at_risk_usd),
     "a year, if nobody calls"],
    ["Expansion", money(book.identified_expansion_usd),
     "a year, already priced"],
    // The one that used to be invisible: an account being served with
    // no contract is never invoiced at all, and was counted as paying.
    ["Not being billed", money(book.unbilled_annual_usd),
     book.unbilled_accounts + " account(s) served with no contract"],
  ]);
}

function worklist(rows) {
  $("worklist-title").textContent = rows.length
    ? `Needs a person — ${rows.length}`
    : "Needs a person";
  if (!rows.length) {
    $("rows").innerHTML =
      `<p class="empty">Nothing needs a person today. Every account is
       paying, in term, and carrying what it was sold.</p>`;
    return;
  }
  $("rows").innerHTML = rows.map((r) => `
    <article class="row ${esc(r.urgency)}">
      <span class="bar" aria-hidden="true"></span>
      <div>
        <div class="who">${esc(r.company_name)}</div>
        <div class="why">${esc(r.headline)}</div>
        <div class="do">${esc(r.action)}</div>
        <div class="contact">${esc(r.contact_name)} · ${
          esc(r.contact_email)} · ${esc(r.contact_phone)}</div>
      </div>
      <div class="amount">${money(r.at_stake_usd)}<span>at stake</span></div>
    </article>`).join("");
}

/* What is wrong with the machine rather than with a customer. Mail that
   cannot be sent looks exactly like mail nobody needed, so it is worth a
   band at the top rather than a line in a log nobody opens. */
async function warnings() {
  try {
    const rows = (await read("/api/digest/operator")).digest.warnings || [];
    $("broken").hidden = rows.length === 0;
    $("broken-list").innerHTML = rows.map((w) => `<li>${esc(w)}</li>`).join("");
  } catch (err) {
    /* The book still stands without it. */
  }
}

async function loadWork() {
  const body = await read("/api/contracts/attention");
  totals(body.book);
  worklist(body.rows || []);
  $("note").textContent = body.note || "";
  warnings();
}

/* ---- money ----------------------------------------------------------- */

async function loadMoney() {
  const b = await read("/api/margin");
  const t = b.target || {};
  const st = b.statement || {};
  const rates = st.rates || {};

  tilesInto("margin-totals", [
    ["Target", pct(t.target_percent), "before tax"],
    ["Achieved", pct(t.achieved_percent),
     t.met ? "target met" : "short by " + pct(t.gap_points).replace("%", "") + " points"],
    ["Kept per location", cents(st.before_tax_usd), "a month, before tax"],
    ["Bad debt ceiling", pct(t.bad_debt_ceiling_percent),
     "the most that can go unpaid and still hit target"],
  ]);

  linesInto("margin-statement", [
    ["Invoiced", cents(st.invoiced_usd), ""],
    ["Bad debt", less(st.bad_debt_usd),
     rates.measuring_bad_debt
       ? "measured from " + rates.invoices_old_enough_to_judge + " ripe invoices"
       : rates.why || ""],
    ["Card fees", less(st.card_fees_usd), ""],
    ["Metered usage", less(st.metered_usage_usd),
     "AI, SMS and voice at cost"],
    ["Fixed infrastructure", less(st.fixed_costs_usd),
     b.fixed_costs_are_shared || ""],
    ["Kept before tax", cents(st.before_tax_usd), pct(st.before_tax_percent)],
    ["One more location costs", cents(st.marginal_cost_of_one_more_unit_usd),
     "the only cost that grows with the business"],
  ]);

  const lever = t.biggest_lever || {};
  linesInto("margin-lever", [
    [lever.line || "—", cents(lever.usd), lever.what_to_do || ""],
  ]);

  const pp = b.annual_prepay || {};
  const buys = pp.buys || {};
  linesInto("margin-prepay", [
    ["Discount offered", pct(pp.current_percent), ""],
    ["What it is worth", pct(pp.break_even_percent),
     "bad debt avoided " + pct(buys.bad_debt_avoided_percent)
     + ", card fees avoided " + pct(buys.card_fees_avoided_percent)
     + ", financing " + pct(buys.financing_value_percent)],
    ["Verdict", pp.worth_it ? "worth it" : "overpaying", pp.note || ""],
  ]);

  $("margin-note").textContent = (t.note || "") + " " + (st.note || "");
}

/* ---- the books ------------------------------------------------------- */

function booksQuery() {
  const year = ($("books-year").value || "").trim();
  return /^\d{4}$/.test(year) ? "?year=" + year : "";
}

async function loadBooks() {
  const q = booksQuery();
  const b = await read("/api/books" + q);
  const accrual = b.accrual_basis || {};
  const cash = b.cash_basis || {};
  const costs = b.known_costs || {};

  tilesInto("books-totals", [
    ["Revenue, accrual", money(accrual.revenue_usd),
     accrual.invoices + " invoice(s) issued"],
    ["Revenue, cash", money(cash.revenue_usd),
     cash.payments + " payment(s) received"],
    ["Written off", money(b.written_off_usd), "in the period"],
    ["Still owed", money(b.outstanding_at_period_end_usd), "at period end"],
    ["Known costs", money(costs.known_total_usd), "evidenced, not guessed"],
  ]);

  const charged = costs.infrastructure_charged_from
    ? "charged from " + when(costs.infrastructure_charged_from)
      + " to " + when(costs.infrastructure_charged_to)
    : "nothing charged: the business was not running in this period";
  const byCategory = Object.entries(costs.by_category || {}).map(
    ([name, amount]) => [name.replace(/_/g, " "), money(amount), ""]
  );

  linesInto("books-costs", [
    ["Metered usage", money(costs.metered_usage_usd), "AI, SMS and voice"],
    ["Fixed infrastructure", money(costs.fixed_infrastructure_usd),
     costs.months_covered + " month(s); " + charged],
    ["Recorded by hand", money(costs.recorded_expenses_usd),
     costs.recorded_expense_count + " entry(s), "
     + costs.without_a_receipt + " without a receipt"],
  ].concat(byCategory).concat([
    ["Known total", money(costs.known_total_usd), costs.warning || ""],
  ]));

  $("books-note").textContent = (b.period || "") + " — " + (b.note || "");
  await loadExpenses(q);
}

async function loadExpenses(q) {
  const b = await read("/api/books/expenses" + (q || booksQuery()));
  itemsInto("expense-list", (b.expenses || []).map((e) => ({
    tone: e.receipt ? "" : "warn",
    title: e.description,
    detail: (e.category || "").replace(/_/g, " ")
      + (e.supplier ? " · " + e.supplier : "")
      + (e.receipt ? " · " + e.receipt
                   : " · no receipt, so it may not be claimable"),
    meta: e.spent_at || "",
    amount: money(e.amount_usd),
    act: "drop-expense",
    id: e.expense_id,
    label: "Remove",
  })), "Nothing recorded for this period. Every card payment this "
     + "application never sees is a deduction nobody will claim.");
}

async function loadCategories() {
  const b = await read("/api/books/expense-categories");
  $("x-category").innerHTML = (b.categories || []).map(
    (opt) => `<option value="${esc(opt)}">${esc(opt)}</option>`
  ).join("");
}

/* A CSV wants the same header as everything else, so it cannot be a
   plain link. Fetched and handed to the browser as a file instead. */
async function download(path, filename) {
  const resp = await api(path);
  if (!resp.ok) { say("expense-said", "That export failed.", true); return; }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/* ---- money we took and cannot account for ---------------------------- */

async function loadCash() {
  const b = await read("/api/payments/unmatched");
  tilesInto("cash-totals", [
    ["Unaccounted for", money(b.total_usd), b.count + " payment(s)"],
  ]);
  itemsInto("cash-list", (b.payments || []).map((r) => ({
    tone: "bad",
    title: money(r.amount_usd) + " " + String(r.currency || "").toUpperCase(),
    detail: r.why || "",
    meta: (r.invoice_reference ? "quoted " + r.invoice_reference + " · " : "")
      + (r.event_id || "") + " · seen " + when(r.seen_at),
    act: "clear-cash",
    id: r.event_id,
    label: "Mark settled",
  })), "Every payment that has arrived is against an invoice.");
  $("cash-note").textContent = b.note || "";
}

/* ---- is the machine running ------------------------------------------ */

async function loadHealth() {
  /* 503 is this endpoint's answer, not its failure: it means the sweep
     has stopped, which is exactly what this panel exists to show. */
  const w = await read("/api/watchdog", [503]);
  tilesInto("health-totals", [
    ["Alert sweep", w.healthy ? "running" : "stopped",
     w.seconds_since_sweep === null ? "has never run"
       : "last ran " + Math.round(w.seconds_since_sweep) + "s ago"],
    ["Money pass", w.seconds_since_money_pass === null ? "never"
       : Math.round(w.seconds_since_money_pass) + "s ago",
     "invoices, dunning and receipts"],
    ["Called stale after", Math.round(w.stale_after_seconds) + "s",
     "sweep interval is " + w.sweep_interval_seconds + "s"],
    ["Outside heartbeat", yesno(w.heartbeat_url_configured),
     w.heartbeat_url_configured ? "absence of a ping is the alarm"
       : "nothing off this machine would notice it stop"],
  ]);
  itemsInto("health-list", (w.reasons || []).map((why) => ({
    tone: "bad", title: why,
  })), "Nothing is stopped.");

  const [backups, mail, log, sup, due, terms, fit, broke] =
    await Promise.all([
    read("/api/admin/backups"),
    read("/api/mail/status"),
    read("/api/mail/log?limit=25"),
    read("/api/mail/suppressions"),
    read("/api/conversion/due"),
    read("/api/legal/acceptance/outstanding"),
    read("/api/admin/readiness"),
    read("/api/admin/faults?limit=25"),
  ]);

  linesInto("backup-lines", [
    ["Snapshots kept", backups.count + " of " + backups.keep, backups.directory || ""],
    ["Most recent", backups.latest || "none yet",
     backups.latest ? Math.round(backups.latest_bytes / 1024) + " KB" : ""],
    ["Survives a lost disk",
     yesno(!backups.on_the_same_disk_as_the_database), backups.note || ""],
  ]);

  // Deduplicated by where it was raised, so a thousand repeats of one
  // bug is one row saying a thousand -- which is what stops the noisy
  // bug burying the rare one.
  linesInto("fault-lines", [
    ["Distinct faults", String(broke.distinct),
     broke.occurrences + " occurrence(s) in total"],
    ["Most recent", broke.newest ? when(broke.newest) : "none",
     broke.worst ? "worst offender: " + broke.worst : broke.note],
  ]);
  itemsInto("fault-list", (broke.faults || []).map((f) => ({
    tone: f.seen > 10 ? "bad" : "warn",
    title: f.exception + " in " + f.source,
    detail: f.message,
    meta: f.seen + "x \u00b7 first " + when(f.first_seen)
      + " \u00b7 last " + when(f.last_seen) + " \u00b7 " + f.fault_id,
    act: "dismiss-fault",
    id: f.fault_id,
    label: "Dismiss",
  })), "Nothing has thrown an error.");

  itemsInto("snapshot-list", (backups.snapshots || []).map((snap) => ({
    title: snap.name,
    detail: Math.round(snap.size_bytes / 1024) + " KB",
    meta: when(snap.taken_at),
    act: "restore",
    id: snap.name,
    label: "Restore",
  })), "No snapshot has been taken yet.");

  linesInto("mail-lines", [
    ["Can send at all", yesno(mail.configured),
     mail.configured ? String(mail.host)
       : "missing " + (mail.missing || []).join(", ")],
    ["Can say how to pay", yesno(mail.payment_details_configured),
     "an invoice that cannot be paid is not an invoice"],
    ["Queued", String(mail.queued), "written, not yet delivered"],
    ["Sent", String(mail.sent), ""],
    ["Failed", String(mail.failed),
     "given up after " + mail.max_attempts + " attempts or "
     + mail.max_age_hours + " hours"],
  ]);

  itemsInto("mail-log", (log.messages || []).map((m) => ({
    tone: m.status === "failed" ? "bad" : (m.status === "sent" ? "good" : "warn"),
    title: m.subject,
    detail: m.detail || "",
    meta: m.to + " · " + m.klass + " · " + m.status
      + " · " + when(m.sent_at || m.created_at),
  })), "Nothing has been sent yet.");

  itemsInto("suppression-list", (sup.suppressions || []).map((b2) => ({
    tone: b2.reason === "bounced" ? "bad" : "warn",
    title: b2.address,
    detail: b2.reason === "bounced"
      ? "bounced — nothing at all is sent here, invoices included"
      : "unsubscribed — commercial mail only; invoices still go",
    meta: (b2.detail ? b2.detail + " · " : "") + when(b2.created_at),
    act: "unsuppress",
    id: b2.address,
    label: "Allow again",
  })), "No address has been stopped.");

  // Two questions, not one. A deployment that can monitor but cannot
  // invoice is a perfectly good way to launch, and saying so is more
  // use than a single ready/not-ready light that is always red.
  linesInto("launch-lines", [
    ["Launch shape", fit.shape, fit.note],
    ["Can a freezer depend on it", fit.can_monitor ? "yes" : "no",
     "alerts reach a person"],
    ["Runs unattended", fit.runs_unattended ? "yes" : "no",
     "something outside notices if this dies"],
    ["Can it ask to be paid", fit.can_take_money ? "yes" : "no",
     // Not "trials only" when the provisioning key is unset: any owner can
     // move off the trial from the console, so that line was untrue.
     fit.can_take_money
       ? "invoices carry everything a finance team needs"
       : "an owner can move off the trial at any time \u2014 the first "
         + "invoice they trigger will be missing something"],
  ]);
  itemsInto("launch-list", fit.checks
    .filter((c) => c.state !== "ok")
    .map((c) => ({
      tone: c.state === "blocks" ? "bad" : "warn",
      title: c.title,
      detail: c.detail,
      meta: c.fix,
    })), "Nothing outstanding.");

  // An acceptance pins the hash of the exact text. Editing a document
  // does not void the old agreement -- it means the customer is still
  // on the old one, which is the thing worth knowing before anybody
  // relies on a clause added afterwards.
  linesInto("terms-lines", [
    ["Live version", String(terms.version), ""],
    ["Accounts on it", (terms.accounts - terms.count) + " of " + terms.accounts,
     terms.count ? terms.note : "Every account has accepted the current text."],
  ]);
  itemsInto("terms-list", (terms.outstanding || []).map((a) => ({
    tone: a.never_accepted.length ? "bad" : "warn",
    title: a.company_name,
    detail: a.never_accepted.length
      ? "has never accepted: " + a.never_accepted.join(", ")
      : "agreed to an earlier version of: " + a.out_of_date.join(", "),
    meta: a.contact_name + " \u00b7 " + a.contact_email,
  })), "Every account has accepted the text that is live today.");

  itemsInto("conversion-list", (due.due || []).map((d) => ({
    tone: d.already_sent ? "" : "warn",
    title: d.company_name,
    detail: "stage: " + d.stage
      + (d.already_sent ? " (already sent, would be skipped)" : ""),
    meta: d.tenant_id + " · " + d.units + " unit(s)",
  })), "No trial is due anything.");
}

/* ---- tabs ------------------------------------------------------------ */

const PANELS = {
  work: loadWork, money: loadMoney, books: loadBooks,
  cash: loadCash, health: loadHealth,
};
let current = "work";

async function show(name) {
  current = name;
  document.querySelectorAll("#tabs button").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.panel === name));
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.hidden = panel.id !== "panel-" + name;
  });
  await refresh();
}

async function refresh() {
  $("err").hidden = true;
  try {
    await PANELS[current]();
  } catch (err) {
    if (err.message !== "locked") {
      fail("That view could not be loaded (" + err.message + ").");
    }
  }
}

/* ---- things that change something ------------------------------------ */

async function act(button) {
  const what = button.dataset.act;
  const id = button.dataset.id || "";
  button.disabled = true;
  try {
    if (what === "books-year") { await loadBooks(); return; }
    if (what === "ledger-csv") {
      await download("/api/books/ledger.csv" + booksQuery(), "sales-ledger.csv");
      return;
    }
    if (what === "cash-csv") {
      await download("/api/books/cash.csv" + booksQuery(), "cash-book.csv");
      return;
    }
    if (what === "expenses-csv") {
      await download("/api/books/expenses.csv" + booksQuery(), "expenses.csv");
      return;
    }
    if (what === "drop-expense") {
      await api("/api/books/expenses/" + encodeURIComponent(id),
                { method: "DELETE" });
      await loadBooks();
      return;
    }
    if (what === "clear-cash") {
      await api("/api/payments/unmatched/" + encodeURIComponent(id),
                { method: "DELETE" });
      await loadCash();
      return;
    }
    if (what === "unsuppress") {
      await api("/api/mail/suppressions?address=" + encodeURIComponent(id),
                { method: "DELETE" });
      await loadHealth();
      return;
    }
    if (what === "dismiss-fault") {
      await api("/api/admin/faults/" + encodeURIComponent(id),
                { method: "DELETE" });
      await loadHealth();
      return;
    }
    if (what === "restore") {
      // Typed out in full, because this discards every write made since
      // the snapshot and a misclick should not be able to do that.
      if (!window.confirm(
            "Restore " + id + " over the live database?\n\n"
            + "Every change made since it was taken is discarded. The "
            + "database being replaced is snapshotted first.")) {
        return;
      }
      const resp = await api("/api/admin/backups/restore", {
        method: "POST", body: { snapshot: id, confirm: true },
      });
      const body = await resp.json().catch(() => ({}));
      say("health-said", resp.ok
          ? ("Restored " + id + ". " + body.detail)
          : ("Could not restore it: " + (body.detail || resp.status)),
          !resp.ok);
      await loadHealth();
      return;
    }
    if (what === "backup") {
      const resp = await api("/api/admin/backups", { method: "POST" });
      const body = await resp.json().catch(() => ({}));
      say("health-said", resp.ok ? "Snapshot taken and verified."
          : ("Could not take one: " + (body.detail || resp.status)), !resp.ok);
      await loadHealth();
      return;
    }
    if (what === "flush") {
      const resp = await api("/api/mail/flush", { method: "POST" });
      const body = await resp.json().catch(() => ({}));
      // The response reports what it tried and what landed; what is
      // still queued is read back from the refreshed panel below rather
      // than invented here from a key this endpoint does not return.
      say("health-said", resp.ok
          ? ("Tried " + body.attempted + ", sent " + body.sent + ".")
          : "The queue could not be flushed.", !resp.ok);
      await loadHealth();
      return;
    }
    if (what === "money-pass") {
      const resp = await api("/api/contracts/run", { method: "POST" });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) { say("work-said", "The pass could not be run.", true); return; }
      const bill = body.billing, chase = body.collections;
      const broke = bill.failed_subscriptions.length + chase.failed_invoices.length;
      say("work-said",
          "Issued " + bill.invoices_issued + " invoice(s) for "
          + cents(bill.billed_usd) + ", sent " + chase.notices_count
          + " collection notice(s), added " + chase.late_fees_issued.length
          + " late fee(s)."
          + (broke ? " " + broke + " account(s) failed and are in the log." : ""),
          broke > 0);
      await loadWork();
      return;
    }
    if (what === "reports") {
      const resp = await api("/api/digest/reports", { method: "POST" });
      const body = await resp.json().catch(() => ({}));
      // sent_count again: `sent` here is also the list, not the number.
      say("health-said", resp.ok
          ? ("Sent " + body.sent_count + " customer report(s).")
          : "The reports could not be sent.", !resp.ok);
      await loadHealth();
      return;
    }
    if (what === "convert") {
      const resp = await api("/api/conversion/run", { method: "POST" });
      const body = await resp.json().catch(() => ({}));
      // sent_count, not sent: `sent` is the list of what went out, and
      // concatenating an array gave "Sent  to trials" with the number
      // silently missing.
      const failed = (body.failed_tenants || []).length;
      say("health-said", resp.ok
          ? ("Sent " + body.sent_count + " to trials."
             + (failed ? " " + failed + " account(s) could not be staged." : ""))
          : "The pass could not be run.", !resp.ok);
      await loadHealth();
      return;
    }
  } catch (err) {
    if (err.message !== "locked") fail("That did not work (" + err.message + ").");
  } finally {
    button.disabled = false;
  }
}

/* ---- wiring ---------------------------------------------------------- */

$("gate").addEventListener("submit", (event) => {
  event.preventDefault();
  rememberKey($("key").value.trim());
  $("key").value = "";
  open_();
});
$("refresh").addEventListener("click", refresh);
$("forget").addEventListener("click", () => {
  forgetKey();
  location.reload();
});
$("tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("button[data-panel]");
  if (tab) show(tab.dataset.panel);
});
$("board").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-act]");
  if (button) act(button);
});
$("expense-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  say("expense-said", "");
  const payload = {
    amount_usd: Number($("x-amount").value),
    category: $("x-category").value,
    description: $("x-description").value.trim(),
    supplier: $("x-supplier").value.trim(),
    receipt: $("x-receipt").value.trim(),
  };
  const date = $("x-date").value;
  if (date) payload.spent_at = date;
  const resp = await api("/api/books/expenses", { method: "POST", body: payload });
  if (resp.status === 201) {
    $("expense-form").reset();
    say("expense-said", "Recorded. It comes off the profit the return is based on.");
    await loadBooks();
  } else {
    const body = await resp.json().catch(() => ({}));
    say("expense-said", typeof body.detail === "string"
        ? body.detail : "That was not accepted.", true);
  }
});

async function open_() {
  if (!storedKey()) return;
  $("err").hidden = true;
  try {
    await PANELS[current]();
  } catch (err) {
    if (err.message === "locked") return;
    fail("The book could not be loaded (" + err.message + ").");
    return;
  }
  $("gate").hidden = true;
  $("board").hidden = false;
  $("refresh").hidden = false;
  $("forget").hidden = false;
  loadCategories().catch(() => { /* the rest of the page still works */ });
}

open_();
