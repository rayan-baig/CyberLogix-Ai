"""The operator console, and the contract it has with its own API.

Thirty-eight platform endpoints had no UI at all. Reading your own P&L,
your books, money that arrived without an invoice id, the mail queue or
the backups meant curl and an admin key, which in practice means nobody
ever looked.

The interesting test here is not that the page renders. It is the key
contract. The console reads `body.sent_count`, `body.attempted`,
`known_costs.months_covered` and forty other names out of JSON, and
nothing in JavaScript objects when one of them is missing: it renders
`undefined`, or concatenates an array into an empty string and prints a
sentence with the number silently gone. Both of those shipped and were
caught by driving the page in a browser rather than by any test.

So the names the console depends on are written down here and checked
against what the endpoints actually return. A rename on either side
fails this file instead of quietly blanking a figure an operator is
making decisions on.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "static" / "book.html"


def console_js() -> str:
    src = PAGE.read_text()
    return src[src.index("<script>"): src.rindex("</script>")]


# Every panel, the endpoint behind it, and the names the console reads
# out of the response. Nested keys are written parent.child.
CONTRACT = {
    "/api/contracts/attention": [
        "book.arr_usd", "book.mrr_usd", "book.cash_outstanding_usd",
        "book.annual_revenue_at_risk_usd", "book.identified_expansion_usd",
        "book.paying", "book.accounts", "rows", "note",
        "book.unbilled_annual_usd", "book.unbilled_accounts",
    ],
    "/api/margin": [
        "target.target_percent", "target.achieved_percent", "target.met",
        "target.gap_points", "target.bad_debt_ceiling_percent",
        "target.biggest_lever.line", "target.biggest_lever.usd",
        "target.biggest_lever.what_to_do", "target.note",
        "statement.invoiced_usd", "statement.bad_debt_usd",
        "statement.card_fees_usd", "statement.metered_usage_usd",
        "statement.fixed_costs_usd", "statement.before_tax_usd",
        "statement.before_tax_percent",
        "statement.marginal_cost_of_one_more_unit_usd",
        "statement.rates.measuring_bad_debt",
        "statement.rates.invoices_old_enough_to_judge",
        "statement.rates.why", "statement.note",
        "annual_prepay.current_percent", "annual_prepay.break_even_percent",
        "annual_prepay.worth_it", "annual_prepay.note",
        "annual_prepay.buys.bad_debt_avoided_percent",
        "annual_prepay.buys.card_fees_avoided_percent",
        "annual_prepay.buys.financing_value_percent",
        "fixed_costs_are_shared",
    ],
    "/api/books": [
        "period", "note", "written_off_usd", "outstanding_at_period_end_usd",
        "accrual_basis.revenue_usd", "accrual_basis.invoices",
        "cash_basis.revenue_usd", "cash_basis.payments",
        "known_costs.metered_usage_usd", "known_costs.fixed_infrastructure_usd",
        "known_costs.recorded_expenses_usd",
        "known_costs.recorded_expense_count", "known_costs.by_category",
        "known_costs.known_total_usd", "known_costs.months_covered",
        "known_costs.without_a_receipt", "known_costs.warning",
        "known_costs.infrastructure_charged_from",
        "known_costs.infrastructure_charged_to",
    ],
    "/api/books/expenses": ["expenses"],
    "/api/books/expense-categories": ["categories"],
    "/api/payments/unmatched": ["count", "total_usd", "payments", "note"],
    "/api/watchdog": [
        "healthy", "reasons", "seconds_since_sweep",
        "seconds_since_money_pass", "stale_after_seconds",
        "sweep_interval_seconds", "heartbeat_url_configured",
    ],
    "/api/admin/backups": [
        "count", "keep", "latest", "latest_bytes", "directory",
        "on_the_same_disk_as_the_database", "note",
    ],
    "/api/mail/status": [
        "configured", "missing", "host", "payment_details_configured",
        "queued", "sent", "failed", "max_attempts", "max_age_hours",
    ],
    "/api/mail/log": ["messages"],
    "/api/mail/suppressions": ["suppressions"],
    "/api/conversion/due": ["due"],
    "/api/legal/acceptance/outstanding": [
        "version", "count", "accounts", "outstanding", "note",
    ],
}


def dig(body, path):
    """Walk a dotted path, returning a sentinel if any step is missing."""
    node = body
    for step in path.split("."):
        if not isinstance(node, dict) or step not in node:
            return ...
        node = node[step]
    return node


@pytest.mark.parametrize("path", sorted(CONTRACT))
def test_the_console_reads_names_the_api_actually_returns(
    path, api, admin_headers
):
    resp = api.get(path, headers=admin_headers)
    # The watchdog answers 503 by design when the sweep has stopped, and
    # the console renders that body rather than being bounced by it.
    assert resp.status_code in (200, 503), resp.text
    body = resp.json()
    missing = [key for key in CONTRACT[path] if dig(body, key) is ...]
    assert not missing, f"{path} no longer returns {missing}"


def test_running_the_trial_pass_reports_a_count_not_a_list(api, admin_headers):
    """This one shipped.

    The response carries `sent` as the list of what went out and
    `sent_count` as the number. The console read `sent`, JavaScript
    turned the empty array into an empty string, and the operator was
    told "Sent  to trials" with the figure missing and no error
    anywhere.
    """
    body = api.post("/api/conversion/run", headers=admin_headers).json()

    assert isinstance(body["sent_count"], int)
    assert isinstance(body["sent"], list), (
        "the console must not print this one directly"
    )
    assert "sent_count" in console_js()


def test_flushing_the_queue_reports_what_it_tried(api, admin_headers):
    """Also shipped: the console printed 'still queued 0' from a key
    this endpoint has never returned, so the 0 was invented rather than
    measured. A fabricated zero is worse than no number."""
    body = api.post("/api/mail/flush", headers=admin_headers).json()

    assert set(body) >= {"attempted", "sent"}
    assert "queued" not in body
    js = console_js()
    flush = js[js.index('if (what === "flush")'):]
    flush = flush[:flush.index("return;")]
    assert "body.queued" not in flush, "reading a key this endpoint lacks"


def test_the_money_pass_reports_what_it_billed(api, admin_headers):
    """The button that bills and chases the whole fleet. An operator who
    presses it and is told nothing cannot tell it from a no-op."""
    body = api.post("/api/contracts/run", headers=admin_headers).json()

    for key in ("invoices_issued", "billed_usd", "failed_subscriptions"):
        assert key in body["billing"], key
    for key in ("notices_count", "late_fees_issued", "failed_invoices"):
        assert key in body["collections"], key


def test_customer_reports_report_a_count_not_a_list(api, admin_headers):
    """The same shape that caught the trial pass: `sent` is the list."""
    body = api.post("/api/digest/reports", headers=admin_headers).json()

    assert isinstance(body["sent_count"], int)
    assert isinstance(body["sent"], list)


def test_every_platform_endpoint_a_person_needs_has_a_button():
    """Thirty-eight endpoints and no UI is how an operator ends up using
    curl to read their own P&L, which in practice means never reading it.

    The console does not have to expose everything — but a platform
    endpoint that no page mentions should be a decision, not an
    oversight, so the exceptions are named here.
    """
    import re as _re
    from collections import defaultdict

    routes = defaultdict(list)
    for path in ROOT.glob("*.py"):
        src = path.read_text()
        prefix = _re.search(r'APIRouter\(prefix="([^"]+)"', src)
        if not prefix:
            continue
        for match in _re.finditer(
            r'@router\.(get|post|delete|put)\("([^"]*)"[^)]*\)\s*\n'
            r"(?:async )?def (\w+)\(([^)]*)\)", src, _re.S
        ):
            verb, route, _name, args = match.groups()
            if "require_platform_admin" in args:
                routes[verb.upper()].append(prefix.group(1) + route)

    pages = "".join(p.read_text() for p in (ROOT / "static").glob("*.html"))
    uncovered = sorted(
        f"{verb} {route}"
        for verb, found in routes.items()
        for route in found
        if route.split("{")[0].rstrip("/") not in pages
    )

    assert not uncovered, f"no page offers: {uncovered}"


def test_every_tab_has_a_loader_and_a_panel():
    """A tab with no panel is a blank page, and a panel with no loader
    is a page that never fills in."""
    page = PAGE.read_text()
    tabs = set(re.findall(r'data-panel="([a-z]+)"', page))
    panels = set(re.findall(r'id="panel-([a-z]+)"', page))
    loaders = set(re.findall(r"(\w+): load(\w+),", console_js()))

    assert tabs == panels, f"tabs and panels disagree: {tabs ^ panels}"
    assert {name for name, _ in loaders} == tabs


def test_the_console_never_invents_a_missing_number():
    """`body.x || 0` renders a plausible figure when the key is absent.

    On a page whose whole purpose is telling an operator how much money
    there is, a number that was never measured is the one thing that
    must not appear. Text and lists may default; figures may not.
    """
    js = console_js()
    offenders = re.findall(r"\(body\.\w+ \|\| 0\)", js)

    assert not offenders, f"a fabricated figure: {offenders}"


def test_every_write_button_names_an_action_the_console_handles():
    """A button whose action nothing handles is a button that does
    nothing at all, silently, which is how it would ship."""
    page = PAGE.read_text()
    js = console_js()
    declared = set(re.findall(r'data-act="([a-z-]+)"', page))
    # the ones rendered into list items, which the markup cannot show
    declared |= {"drop-expense", "clear-cash", "unsuppress"}
    handled = set(re.findall(r'what === "([a-z-]+)"', js))

    assert declared <= handled, f"unhandled: {sorted(declared - handled)}"
