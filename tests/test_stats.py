"""User counts for the owner's MTRX Horizon console: counts only, behind a key."""


def test_off_unless_a_key_is_set(api, monkeypatch):
    monkeypatch.delenv("STATS_KEY", raising=False)
    assert api.get("/api/stats").status_code == 404


def test_counts_behind_the_key(api, monkeypatch, operator_factory):
    operator_factory()
    monkeypatch.setenv("STATS_KEY", "s3cret-stats-key")
    assert api.get("/api/stats").status_code == 401
    assert api.get("/api/stats", headers={"Authorization": "Bearer nope"}).status_code == 401
    body = api.get("/api/stats", headers={"Authorization": "Bearer s3cret-stats-key"}).json()
    assert body == {"users": 1, "active_users": 1}
