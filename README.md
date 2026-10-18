# Montana Legislature Bill Tracker

The service is called **.bluejay**. The logo (the jay and the wordmark, lettering embedded as base64) lives in `docs/brand/`, with the
open-license notice for the font. It is the header banner and the browser-tab icon.

A static dashboard (GitHub Pages) to read, search and filter every Montana Legislature bill, with the bill list
refreshed from the Legislature's public API three times a day.

## How the data stays live

| Piece | Where it runs | When | What it does |
|---|---|---|---|
| `.github/workflows/refresh.yml` | GitHub Actions | 7:17am, 1:17pm, 7:17pm Mountain, on every push to `main`, and on demand | `scripts/build_data.py` pulls bills + legislators from `api.legmt.gov`, commits `docs/data/<year>/bills.json` only if it changed, then deploys `docs/` to Pages |
| `scripts/commandcenter_refresh.sh` | commandcenter (GPU) | your crontab, below | analyzes newly introduced bills with the local model, writes `docs/data/<year>/analysis.json`, pushes it (which triggers a deploy) |

Status, sponsors, outcomes, subjects and action history are live. The AI summaries and policy areas appear for a bill
once commandcenter has analyzed it; until then the bill shows without one. The page shows an "as of" time and a
Stale chip if a live session's data is more than 14 hours old, and re-checks for new data every 10 minutes while open.

GitHub cron is best-effort, so a run can start a few minutes late. Times are fixed in UTC, so in winter (MST) they fall
an hour earlier on the clock.

## Sessions

`docs/data/sessions.json` lists them. Only `"live": true` sessions are refreshed. To add one, look up its id with
`curl https://api.legmt.gov/legislators/v1/sessions` (2025 on) or `curl https://api.legmt.gov/archive/v1/sessions` (2023
and earlier, add `"api": "archive"`) and add a row, then run `python3 scripts/build_data.py --year <year>`.
The page opens on the newest session that has numbered bills (`build_data.py` keeps `"default"` current), so it never
lands on a session that only has draft requests; 2027 takes over by itself once its first bill is numbered. 2027 (70th Legislature, session id 3) convenes
2027-01-04; until then the API has only draft requests, and the page says so.

## Commandcenter schedule

```
17 7,13,19 * * *  cd ~/LLM/montana-dashboard && SESSION=2027 scripts/commandcenter_refresh.sh >> ~/LLM/montana-dashboard/refresh.log 2>&1
```
(cron on commandcenter uses its local timezone; the script takes a lock so runs never overlap.) It needs the pipeline repo at
`~/LLM/montana` (override with `MONTANA_DIR`), Ollama running, and push access to this repo.

## Run locally

```
python3 scripts/build_data.py                  # live sessions -> docs/data/
python3 -m http.server -d docs 8000            # open http://127.0.0.1:8000
python3 -m unittest discover -s tests
```

Bill data is public record from api.legmt.gov. The AI text is machine-generated and labeled as such; the Legislature's
own site, bills.legmt.gov, is the record.
