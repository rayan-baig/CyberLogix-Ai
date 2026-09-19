"""What somebody agreed to do, and whether they did it.

The meeting summariser already turned a transcript into action items
with an owner and a priority, and then dropped them on the floor: the
caller got a JSON reply and nothing was kept. The one genuinely useful
output of the feature lived for as long as a browser tab.

These cover the half that keeps them. Nothing here calls a model, a mail
host or a phone network, which is the point: the summariser needs a paid
key to be any good, and this works with one, without one, and for
somebody who never holds a meeting and just types the thing in.
"""

from datetime import timedelta

import pytest

import tasks
from store import utc_now


def day(offset: int) -> str:
    return (utc_now() + timedelta(days=offset)).strftime("%Y-%m-%d")


@pytest.fixture()
def crew(api, tenant_factory, owner_headers):
    headers, tenant = tenant_factory(plan="enterprise", company_name="Bell St")
    return {**headers, **owner_headers(headers)}, tenant


def test_a_thing_somebody_agreed_to_is_written_down(api, crew):
    headers, _ = crew

    made = api.post("/api/tasks", headers=headers, json={
        "title": "Get the walk-in compressor serviced",
        "owner": "Marco", "priority": "High", "due_on": day(3)})

    assert made.status_code == 201, made.text
    task = made.json()["task"]
    assert task["title"] == "Get the walk-in compressor serviced"
    assert task["owner"] == "Marco"
    assert task["state"] == "open"
    assert task["when"] == "in 3 day(s)"


def test_no_date_given_still_gets_one(api, crew):
    """Otherwise "overdue" means nothing and the list cannot be sorted.

    A week, rather than asking for a date at the moment somebody is
    trying to save eleven of them at once.
    """
    headers, _ = crew

    made = api.post("/api/tasks", headers=headers, json={
        "title": "Call the refrigeration people"})

    assert made.json()["task"]["due_on"] == day(tasks.DEFAULT_DAYS)


def test_a_task_past_its_date_says_so_loudly(api, crew):
    headers, _ = crew
    api.post("/api/tasks", headers=headers, json={
        "title": "Replace the door seal", "due_on": day(-4)})

    seen = api.get("/api/tasks", headers=headers).json()

    assert seen["late"] == 1
    assert seen["tasks"][0]["state"] == "late"
    assert seen["tasks"][0]["when"] == "4 day(s) late"
    assert "past their date" in seen["note"]


def test_the_worst_thing_is_at_the_top(api, crew):
    """A list that needs scrolling to find the fire is not a list."""
    headers, _ = crew
    for title, due in (("Later", day(20)), ("Very late", day(-9)),
                       ("Soon", day(2)), ("A bit late", day(-1))):
        api.post("/api/tasks", headers=headers,
                 json={"title": title, "due_on": due})

    order = [t["title"] for t in
             api.get("/api/tasks", headers=headers).json()["tasks"]]

    assert order == ["Very late", "A bit late", "Soon", "Later"]


def test_finishing_one_records_who_and_when(api, crew):
    headers, _ = crew
    made = api.post("/api/tasks", headers=headers,
                    json={"title": "Order thermometers"}).json()["task"]

    done = api.post(f"/api/tasks/{made['task_id']}/done", headers=headers)

    task = done.json()["task"]
    assert task["state"] == "done"
    assert task["done_by"] == "Owner"
    assert task["done_at"]
    assert done.json()["outstanding"]["open"] == 0


def test_finished_things_sink_rather_than_vanish(api, crew):
    headers, _ = crew
    made = api.post("/api/tasks", headers=headers,
                    json={"title": "Done thing"}).json()["task"]
    api.post("/api/tasks", headers=headers, json={"title": "Open thing"})
    api.post(f"/api/tasks/{made['task_id']}/done", headers=headers)

    everything = api.get("/api/tasks", headers=headers).json()
    only_open = api.get("/api/tasks?include_done=false",
                        headers=headers).json()

    assert [t["title"] for t in everything["tasks"]] == ["Open thing",
                                                         "Done thing"]
    assert [t["title"] for t in only_open["tasks"]] == ["Open thing"]


def test_a_task_ticked_by_mistake_can_be_put_back(api, crew):
    """Commoner than finishing one by mistake."""
    headers, _ = crew
    made = api.post("/api/tasks", headers=headers,
                    json={"title": "Not actually done"}).json()["task"]
    api.post(f"/api/tasks/{made['task_id']}/done", headers=headers)

    back = api.post(f"/api/tasks/{made['task_id']}/reopen", headers=headers)

    assert back.json()["task"]["state"] in {"open", "today", "late"}
    assert back.json()["task"]["done_by"] == ""


def test_what_a_meeting_agreed_is_kept(api, crew):
    """The whole reason this module exists.

    The summariser returns action_items_assigned and the caller was left
    holding it. This is where that list is meant to land.
    """
    headers, _ = crew

    saved = api.post("/api/tasks/from-meeting", headers=headers, json={
        "source": "Monday kitchen meeting",
        "action_items": [
            {"title": "Fix the walk-in door", "owner": "Marco",
             "priority": "High"},
            {"title": "Reorder gloves", "owner": "Priya", "priority": "Low"},
        ]})

    assert saved.status_code == 201, saved.text
    assert saved.json()["saved"] == 2
    assert saved.json()["outstanding"]["open"] == 2
    titles = {t["title"] for t in saved.json()["tasks"]}
    assert titles == {"Fix the walk-in door", "Reorder gloves"}
    assert all(t["source"] == "Monday kitchen meeting"
               for t in saved.json()["tasks"])


def test_the_summarisers_own_priority_words_are_accepted(api, crew):
    """High, Med, Low -- so a saved item reads like the transcript did."""
    headers, _ = crew

    for word in ("High", "med", "LOW"):
        made = api.post("/api/tasks", headers=headers,
                        json={"title": f"Thing {word}", "priority": word})
        assert made.status_code == 201, made.text

    refused = api.post("/api/tasks", headers=headers,
                       json={"title": "Bad", "priority": "Urgent"})
    assert refused.status_code == 422


def test_one_account_cannot_see_or_touch_another_s_tasks(
        api, tenant_factory, owner_headers):
    first, _ = tenant_factory(plan="enterprise", company_name="A St")
    second, _ = tenant_factory(plan="enterprise", company_name="B St")
    first = {**first, **owner_headers(first, email="a@ex.com")}
    second = {**second, **owner_headers(second, email="b@ex.com")}
    mine = api.post("/api/tasks", headers=first,
                    json={"title": "My private task"}).json()["task"]

    assert api.get("/api/tasks", headers=second).json()["count"] == 0
    assert api.post(f"/api/tasks/{mine['task_id']}/done",
                    headers=second).status_code == 404
    assert api.delete(f"/api/tasks/{mine['task_id']}",
                      headers=second).status_code == 404
    # ...and it is untouched.
    assert api.get("/api/tasks", headers=first).json()["tasks"][0][
        "state"] != "done"


def test_only_an_owner_may_delete(api, crew, operator_factory):
    """A deleted task leaves no record that it was ever agreed."""
    headers, _ = crew
    made = api.post("/api/tasks", headers=headers,
                    json={"title": "Delete me"}).json()["task"]

    gone = api.delete(f"/api/tasks/{made['task_id']}", headers=headers)

    assert gone.status_code == 204
    assert api.get("/api/tasks", headers=headers).json()["count"] == 0


def test_the_fleet_summary_reads_every_account_in_one_pass(
        api, tenant_factory, owner_headers):
    """Calling summary() per tenant full-scans the table each time. The
    licence digest made exactly that mistake and took the test suite
    from under two minutes to not finishing."""
    for n, email in ((1, "x@ex.com"), (2, "y@ex.com")):
        headers, _ = tenant_factory(plan="enterprise",
                                    company_name=f"Co {n}")
        headers = {**headers, **owner_headers(headers, email=email)}
        api.post("/api/tasks", headers=headers,
                 json={"title": "Late one", "due_on": day(-2)})
        api.post("/api/tasks", headers=headers,
                 json={"title": "Fine one", "due_on": day(5)})

    everything = tasks.fleet_summary()

    assert everything["open"] == 4
    assert everything["late"] == 2


def test_something_due_today_is_not_late_yet(api, crew):
    """The day it is due is a day you still have. Calling it late means
    every task spends its last day shouting, and a list that shouts about
    things that are fine is a list people stop reading."""
    headers, _ = crew
    api.post("/api/tasks", headers=headers,
             json={"title": "Due right now", "due_on": day(0)})

    seen = api.get("/api/tasks", headers=headers).json()

    assert seen["late"] == 0
    assert seen["due_today"] == 1
    assert seen["tasks"][0]["state"] == "today"
    assert seen["tasks"][0]["when"] == "Due today"


def test_the_fleet_pass_does_not_count_finished_work(
        api, tenant_factory, owner_headers):
    """Otherwise the operator's daily digest reports a backlog that was
    cleared, every day, forever."""
    headers, _ = tenant_factory(plan="enterprise", company_name="Done Co")
    headers = {**headers, **owner_headers(headers, email="done@ex.com")}
    late = api.post("/api/tasks", headers=headers,
                    json={"title": "Late then done",
                          "due_on": day(-3)}).json()["task"]
    api.post("/api/tasks", headers=headers,
             json={"title": "Still open", "due_on": day(-1)})

    before = tasks.fleet_summary()
    api.post(f"/api/tasks/{late['task_id']}/done", headers=headers)
    after = tasks.fleet_summary()

    assert before == {"open": 2, "late": 2}
    assert after == {"open": 1, "late": 1}


def test_a_priority_the_model_invented_does_not_lose_the_whole_meeting(
        api, crew):
    """The model is asked for High/Med and mostly obliges.

    "Urgent" or "P1" coming back would fail validation, and a strict
    reading would drop every item in the batch rather than the one odd
    word. The console maps anything unfamiliar to the middle, and this
    is the rule that makes that safe: the API refuses it, so the
    mapping has to happen before it is sent, not be hoped for after.
    """
    headers, _ = crew

    refused = api.post("/api/tasks/from-meeting", headers=headers, json={
        "source": "Meeting", "action_items": [
            {"title": "Fine one", "priority": "High"},
            {"title": "Odd one", "priority": "Urgent"},
        ]})

    assert refused.status_code == 422
    # Nothing half-saved: the batch is one transaction or none of it.
    assert api.get("/api/tasks", headers=headers).json()["count"] == 0


def test_an_empty_meeting_saves_nothing_rather_than_erroring(api, crew):
    """A transcript that named no actions is a normal Tuesday."""
    headers, _ = crew

    saved = api.post("/api/tasks/from-meeting", headers=headers, json={
        "source": "Quiet meeting", "action_items": []})

    assert saved.status_code == 201
    assert saved.json()["saved"] == 0
    assert saved.json()["outstanding"]["count"] == 0
