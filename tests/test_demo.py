"""See it live: the demo a stranger can open without signing up.

It is the one part of the product anybody on the internet can drive, so
it is tested on what it must never do -- send a real text or call, leave
anything behind on the server, answer a script without limit, or show
the name of the AI model -- and on what it has to show: a walk-in that
warms a minute in, the real alert's words, the calls, and "reply 1".
"""

import json
import re
from pathlib import Path

import pytest

import demo
import gemini
import ratelimit
import wording
from pages import page_source
from store import STORE

ROOT = Path(__file__).resolve().parent.parent
DEMO_JS = (ROOT / "static" / "demo.js").read_text()
DEMO_PAGE = page_source(ROOT / "static" / "demo.html")
LANDING = (ROOT / "static" / "index.html").read_text()

# The model family this codebase calls (gemini.py). The configured id
# itself is checked with a sentinel in each test.
MODEL_ID = re.compile(r"gemini", re.I)


def _everything_kept():
    """Every row in the database, by kind, and the in-memory counts."""
    import usage

    db = STORE._db
    return ({kind: db.count(kind) for kind in db.kinds()},
            len(STORE.list_tenants()), json.dumps(usage.last_days(2), sort_keys=True))


# --- what a visitor sees -------------------------------------------------------


def test_the_landing_page_has_see_it_live(api):
    page = api.get("/").text
    assert '<a class="btn" href="/demo">See it live</a>' in page


def test_the_demo_page_is_served_and_labelled_pretend(api):
    got = api.get("/demo")
    assert got.status_code == 200
    page = got.text
    assert "Demo &mdash; pretend data" in page
    assert "No texts or calls are sent, nothing is saved" in page
    # It ends with the way in.
    assert page.count("Start your free trial") >= 2
    assert 'id="end-cta" href="/signup?sector=restaurant"' in page
    # The trial length comes from the plan tiers, not the page.
    from pricing import headline
    assert f"Free for {headline()['trial_days']} days" in page
    assert "%%" not in page


def test_the_story_is_a_restaurant_with_four_units(api):
    body = api.get("/api/demo/story").json()

    assert body["label"] == "Demo — pretend data"
    assert [u["name"] for u in body["units"]] == [
        "Walk-in cooler", "Freezer", "Prep fridge", "Bar fridge"]
    assert body["drifting"] == "WALK-IN-1"


def test_one_unit_drifts_warm_about_a_minute_in():
    story = demo.story()
    walk_in = next(u for u in story["units"] if u["id"] == story["drifting"])
    alert_at = story["drift_starts_seconds"] + story["alert_index"] * story["tick_seconds"]

    assert story["drift_starts_seconds"] == 60
    assert 60 < alert_at <= 90
    # Under its limit until it goes over; then it STAYS over for the
    # demo's stand-in for the real wait before the alert.
    drift = story["drift_f"]
    over = story["over_index"]
    assert all(f <= walk_in["limit_f"] for f in drift[:over])
    assert all(f > walk_in["limit_f"] for f in drift[over:story["alert_index"] + 1])
    assert story["alert_index"] == over + story["stay_ticks"]
    assert story["real_stay_minutes"] == 20
    # And it comes back down once somebody has dealt with it.
    assert story["recover_f"][-1] < walk_in["limit_f"]
    # Every other unit starts comfortably inside its own limit.
    for unit in story["units"]:
        assert unit["start_f"] < unit["limit_f"] - 3


def test_the_alert_says_exactly_what_a_real_one_would():
    """Worded by the code that words real alerts, so the demo cannot
    promise a text the product does not send."""
    story = demo.story()
    text = story["messages"]["alert_text"]

    walk_in = demo._unit(demo.UNITS[0])
    business = type("B", (), {"company_name": demo.BUSINESS})()
    assert text == wording.alert_sms(
        business, walk_in, story["drift_f"][story["alert_index"]], "F")
    assert text.startswith("ALERT Walk-in cooler")
    assert "Reply 1" in text and len(text) <= wording.SMS_LIMIT
    assert "Press 1" in story["messages"]["call_script"]
    assert f"{story['real_wait_minutes']} minutes" in story["messages"]["call_script"]
    assert story["messages"]["ack_reply"].startswith("CyberLogix: thanks, Sam.")
    assert "No more calls" in story["messages"]["ack_reply"]


def test_the_page_shows_texts_calls_and_reply_1():
    for needed in ('id="reply-1"', 'id="press-1"', 'id="call"', 'id="timeline"',
                   'id="fix"', 'id="skip"'):
        assert needed in DEMO_PAGE, needed
    # The calls go one person at a time, and replying 1 stops them.
    assert "S.messages.alert_text" in DEMO_JS
    assert "S.messages.call_script" in DEMO_JS
    assert "S.messages.ack_reply" in DEMO_JS
    assert "if (run.alertOpen && !run.acked)" in DEMO_JS


# --- what it must never do ----------------------------------------------------


def test_the_demo_sends_nothing(api, monkeypatch):
    """No text, no call, no email, whatever is pressed: the server's part
    is words, and the page's part never leaves the browser."""
    import mail
    import notifications

    def refuse(*args, **kwargs):
        raise AssertionError("the demo tried to send something")

    monkeypatch.setattr(notifications, "send_sms", refuse)
    monkeypatch.setattr(notifications, "place_voice_call", refuse)
    monkeypatch.setattr(mail, "send", refuse)

    assert api.get("/demo").status_code == 200
    assert api.get("/api/demo/story").status_code == 200


def test_the_page_calls_nothing_but_the_story():
    """Every fetch in the page's own script is the story, read-only."""
    assert re.findall(r"fetch\(\s*\"([^\"]+)\"", DEMO_JS) == ["/api/demo/story"]
    assert "method:" not in DEMO_JS and "POST" not in DEMO_JS
    assert "localStorage" not in DEMO_JS and "sessionStorage" not in DEMO_JS


def test_the_demo_keeps_nothing(api):
    """No business, person, sensor, reading, audit line or usage count:
    nothing on the server to clean up afterwards."""
    before = _everything_kept()
    for _ in range(3):
        api.get("/demo")
        api.get("/api/demo/story")
    assert _everything_kept() == before


def test_the_story_is_rate_limited(api):
    for _ in range(demo.LIMIT.per_caller):
        assert api.get("/api/demo/story").status_code == 200
    refused = api.get("/api/demo/story")
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0
    assert "Too many demo requests" in refused.json()["detail"]


def test_the_story_has_a_ceiling_for_everybody_together(api, monkeypatch):
    """Callers that cannot be told apart still meet a limit."""
    monkeypatch.setattr(demo, "LIMIT", ratelimit.Limit(
        "demo-test", per_caller=1000, total=5, window_seconds=600, words="demo requests"))
    for _ in range(5):
        assert api.get("/api/demo/story").status_code == 200
    assert api.get("/api/demo/story").status_code == 429


def test_the_page_says_so_when_it_is_refused():
    assert "if (!got.ok)" in DEMO_JS and "body.detail" in DEMO_JS
    assert "The demo could not start" in DEMO_JS


def test_no_model_name_or_id_anywhere_in_the_demo(api, monkeypatch):
    monkeypatch.setattr(gemini, "GEMINI_MODEL", "sentinel-model-7")
    story = api.get("/api/demo/story").text
    page = api.get("/demo").text

    for text in (story, page, DEMO_PAGE):
        assert "sentinel-model-7" not in text
        assert not MODEL_ID.search(text), MODEL_ID.search(text)


def test_health_names_no_model_either(api, monkeypatch):
    """The public health answer was where a model id leaked before."""
    monkeypatch.setattr(gemini, "GEMINI_MODEL", "sentinel-model-7")
    assert "sentinel-model-7" not in api.get("/api/health").text


def test_the_demo_page_is_kept_out_of_search(api):
    assert api.get("/demo").headers.get("X-Robots-Tag", "").startswith("noindex")


# --- the copy on GitHub Pages ---------------------------------------------------


def test_the_static_copy_needs_no_server_and_names_no_model(monkeypatch):
    import build_public_demo

    monkeypatch.setattr(gemini, "GEMINI_MODEL", "sentinel-model-7")
    page = build_public_demo.build_kitchen(demo.story())

    assert '<script type="application/json" id="demo-story">' in page
    assert 'getElementById("demo-story")' in page
    assert "sentinel-model-7" not in page
    assert not MODEL_ID.search(page)
    # Its sign-up button goes to the live product, from inside the story.
    assert "https://cyberlogix.mtrxhorizyn.com/signup?sector=restaurant" in page
    assert 'href="/' not in re.sub(r"<script.*?</script>", "", page, flags=re.S)


def test_the_builder_refuses_a_capture_that_names_the_model(monkeypatch):
    import build_public_demo

    monkeypatch.setattr(gemini, "GEMINI_MODEL", "sentinel-model-7")
    with pytest.raises(SystemExit):
        build_public_demo.refuse_model_names({"/api/health": {"body": {
            "ai": {"model": "sentinel-model-7"}}}})
    build_public_demo.refuse_model_names({"/api/health": {"body": {"ai": {}}}})


def test_the_committed_public_pages_name_no_model():
    for name in ("index.html", "console.html", "help.html", "sensors.html", "demo.html"):
        text = (ROOT / "docs" / name).read_text()
        assert not MODEL_ID.search(text), name
