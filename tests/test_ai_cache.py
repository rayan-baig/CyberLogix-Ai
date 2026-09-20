"""The cache that makes a repeated question free, and its bound.

A cache hit costs nothing, which is the whole point of it. But it was
the only collection in this codebase without a limit -- readings cap at
500, BYOD samples at 25, faults at 200, login attempts at 8 -- and it is
the one that grows fastest, because every distinct prompt is a new key.

A cache is a saving, not a record. Losing the least recently wanted
entry costs one model call. Keeping every entry ever costs a database
that never stops growing, on a product meant to run unattended for
years.
"""

import store
from store import STORE


def test_a_repeated_question_is_free():
    STORE.cache_put("k1", "the answer")

    assert STORE.cache_get("k1") == "the answer"
    assert STORE.cache_get("nope") is None


def test_the_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(store, "MAX_CACHE_ENTRIES", 10)
    for n in range(40):
        STORE.cache_put(f"bounded-{n}", f"answer {n}")

    assert STORE.cache_size() <= 10


def test_eviction_drops_what_nobody_asks_for(monkeypatch):
    """Not whatever happened to be written first.

    An answer asked for every day and written once would otherwise be
    the first thing thrown away, which is the opposite of a cache.
    """
    monkeypatch.setattr(store, "MAX_CACHE_ENTRIES", 5)
    STORE.cache_put("popular", "asked constantly")
    for n in range(4):
        STORE.cache_put(f"filler-{n}", "meh")

    # Keep asking for the popular one while the cache churns past it.
    for n in range(20):
        assert STORE.cache_get("popular") == "asked constantly"
        STORE.cache_put(f"churn-{n}", "meh")

    assert STORE.cache_get("popular") == "asked constantly", (
        "the entry asked for on every pass was evicted")


def test_eviction_reaches_the_database_not_just_memory(monkeypatch):
    """Dropping it from memory alone leaves the row on disk to be loaded
    back at the next restart, which is not eviction -- it is a leak with
    extra steps."""
    monkeypatch.setattr(store, "MAX_CACHE_ENTRIES", 8)
    for n in range(50):
        STORE.cache_put(f"disk-{n}", f"answer {n}")

    on_disk = len(list(STORE._db.all("aicache")))

    assert on_disk <= 8, f"{on_disk} rows survived on disk"
    assert on_disk == STORE.cache_size()


def test_the_generator_asks_the_cache_before_it_asks_the_model(monkeypatch):
    """The saving itself. A second identical prompt must not reach the
    network."""
    import costs
    import gemini

    calls = []

    def never(*args, **kwargs):
        calls.append(1)
        raise AssertionError("the model was called for a cached prompt")

    key = costs.cache_key("what is the capital of France?", "test")
    STORE.cache_put(key, "Paris.")
    monkeypatch.setattr(gemini, "_client", never, raising=False)

    text, source = gemini.safe_generate(
        "what is the capital of France?", fallback="", purpose="test")

    assert text == "Paris."
    assert source == "cache"
    assert calls == []


def test_a_restart_does_not_reload_more_than_the_cap(monkeypatch, tmp_path):
    """A database written before the cap existed holds more than it
    allows. Trimming only on write means the first thing a restart does
    is load all of it back into memory and sit there until somebody
    happens to generate something."""
    from db import Database

    fat = Database(str(tmp_path / "fat.db"))
    for n in range(60):
        fat.put("aicache", f"old-{n}", {"key": f"old-{n}", "text": "x"})

    monkeypatch.setattr(store, "MAX_CACHE_ENTRIES", 12)
    fresh = store.HubStore(db=fat)
    fresh.load()

    assert fresh.cache_size() <= 12
    assert len(list(fat.all("aicache"))) <= 12, (
        "trimmed in memory but not on disk, so the next restart reloads them")
