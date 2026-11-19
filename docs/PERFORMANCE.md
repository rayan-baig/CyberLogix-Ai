# Speed

How fast the console opens and how much it costs to keep open, measured
before and after 3.18.0. For the server under load (many sensors at
once), see [LOAD-TEST.md](LOAD-TEST.md).

## How it was measured

On 2026-11-19, on a shared machine, so treat small differences as noise.
The demo restaurant group (`python demo_account.py --db ...`: 3 sites,
20 units, 7 days of readings) served by one `uvicorn` worker, the same
as production, with the sweep off. Before is 3.17.0, after is 3.18.0,
side by side on the same database.

* **Page weight:** `/console` and every script and stylesheet it names,
  asked for the way a browser asks (`Accept-Encoding: gzip`).
* **Slow phone:** Chromium at phone size, with the network slowed to
  400 kbit/s and a 400 ms round trip (about a bad 3G signal in a
  basement kitchen). Time until the page's `load` event. Google Fonts
  were blocked in both runs, so only this server is measured.
* **Busiest requests:** the console's refresh, which every open console
  repeats every 20 seconds. Each request 15 times, median. Requests per
  poll were counted in Chromium, signed in, over six polls.

## Results

| | Before | After |
|---|---:|---:|
| Console first open, bytes sent | 388 KB | 109 KB (72% less) |
| Console first open, slow phone | 8.2 s | 2.9 s |
| Console opened again, bytes sent | 59 KB | 0.6 KB |
| Console opened again, slow phone | 1.6 s | 0.8 s |
| Requests per 20-second poll | 28 | 8 on four polls in five; all 28 on the fifth |
| Bytes per poll | 71 KB | 8.5 KB (21 KB on the fifth) |
| Requests per open console, per 100 s | 140 | 60 |

The server side was already quick and did not need changing: the
slowest request in the refresh is `/api/console/overview` at about 8 ms,
then `/api/forecast/fleet` at about 5 ms; every other one is under 4 ms,
and the whole refresh, one request after another, is under 110 ms.

## What changed

* **Compression.** Text answers (pages, scripts, stylesheets, JSON) are
  gzipped for any browser that asks. `console.js` goes from 250 KB to
  72 KB. Images are already compressed and are left alone.
* **Files kept by the browser.** A page now names each file with
  `?v=` and a short hash of the file (`/static/console.js?v=41f88666de4d`).
  Asked for that way, a file is kept for a year: a changed file has a new
  hash, so a browser can never run an old script against a new page.
  Asked for without `?v=`, it is served as before. Pages are still checked every time, but now carry an ETag, so
  opening the console again costs a 304 instead of the page.
* **Fewer requests while the console is open.** Cards that only change
  when somebody changes them (bills, the team, the roster, the contract,
  the forecast and so on) are fetched every fifth poll instead of every
  poll. Temperatures, problems, tasks and who would be called are still
  fetched every 20 seconds. Anything a person does in the console still
  reloads everything at once.

## Not changed, and why

* **Google Fonts.** `theme.css` loads the fonts from Google with
  `@import`, which the browser can only start after the stylesheet has
  arrived. Moving it to a `<link>` in every page's head would save one
  round trip on the first open; it touches every page and was left for
  a design pass.
* **The database.** Every console request is a scan of one business's
  records held in memory, not a database query, so there were no slow
  queries or missing indexes to fix.
* **`console.js` is one 250 KB file.** Splitting it by tab would cut the
  first open further, but it is a large change to the most used page,
  and gzip and caching already took most of the cost.

## Run it again

```bash
python demo_account.py --db /tmp/demo.db --password <12+ characters>
CYBERLOGIX_DB_PATH=/tmp/demo.db CYBERLOGIX_SWEEP_SECONDS=0 \
  python -m uvicorn main:app --port 8099
curl -s -H 'Accept-Encoding: gzip' -o /dev/null -w '%{size_download}\n' \
  "http://127.0.0.1:8099$(curl -s http://127.0.0.1:8099/console \
  | grep -o '/static/console.js?v=[0-9a-f]*')"
```
