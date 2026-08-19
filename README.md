# Trackr opening watcher

Polls the Trackr API every 30 minutes and pushes a phone notification when a
programme's opening date appears.

## How it works

The Trackr site is an Angular SPA backed by a public, unauthenticated JSON API:

```
https://api.the-trackr.com/programmes?region=UK&industry=Finance&season=2027&type=summer-internships
```

The `status` field is `null` on every record — the Open/Closed badge you see on
the site is computed in the browser. What actually signals an opening is the
`openingDate` field, which is `null` for most programmes until Trackr learns the
application window has opened.

So `check.py` treats a programme as open when `openingDate` is set and in the
past, and the closing date (if any) hasn't passed. It compares that against the
last snapshot in `state/seen.json` and notifies on any `false -> true` flip.

The first run has no snapshot to compare against, so it saves a baseline and
sends nothing. Every run after that can notify.

## Setup

1. Push this directory to a GitHub repo.
2. Add a repository secret named `NTFY_TOPIC` under
   Settings → Secrets and variables → Actions. Use an unguessable value —
   ntfy.sh topics are public to anyone who knows the name.
3. Install [ntfy](https://ntfy.sh/) on your phone and subscribe to that topic.
4. Run the workflow once manually (Actions → Check Trackr → Run workflow) to
   save the baseline snapshot.

From then on the schedule takes over. GitHub cron can drift by 5–15 minutes
under load, so treat the half-hourly check as "within about an hour".

The schedule is `7,37` rather than `0,30`. Scheduled jobs queue hardest on the
hour and half-hour, and free-tier runs get delayed — or occasionally dropped —
when the queue is busy. Offsetting keeps the 30-minute cadence while avoiding
the stampede.

## Cost

The repo is private, so Actions minutes are metered against the free tier's
2,000/month.

**Billing rounds every job up to a whole minute.** Actual duration is
irrelevant below that — a 15-second run and a 55-second run both cost one
minute. So the only number that matters is the run count.

The current split schedule:

| Window                          | Interval | Runs/month |
| ------------------------------- | -------- | ---------- |
| Weekdays 07:00–17:59 UTC        | 15 min   | ~955       |
| Weekday nights                  | hourly   | ~282       |
| Weekends                        | hourly   | ~209       |
| **Total**                       |          | **~1,446** |

That's ~72% of the 2,000-minute allowance — the same cost as a flat 30-minute
schedule, but with double the resolution during the hours openings actually
get published.

For reference: a flat 15-minute schedule would be ~2,920 runs (146% — over the
limit), and flat hourly ~730 (37%).

**GitHub does not guarantee scheduled runs.** They can be delayed under load
and, on free tier, dropped entirely. Intervals below ~15 minutes buy
resolution the scheduler may not actually deliver.

Adding more entries to `TRACKERS` costs nothing extra: they run as additional
API calls inside the same job, and the job stays well under the one-minute
round-up either way.

## What's tracked

Eight trackers, ~1,450 programmes, all on season 2027:

| Tracker    | Summer | Spring Weeks | Placements | Off-Cycle |
| ---------- | ------ | ------------ | ---------- | --------- |
| UK Finance | 428    | 110          | 175        | 199       |
| UK Tech    | 285    | 33           | 160        | —         |
| US Finance | 61\*   | —            | —          | —         |

Off-cycle exists for UK Finance only — UK Tech and US Finance both return zero.

Season 2027 covers all three eligible routes:

- **Summer internships** run in summer 2027 — penultimate year, graduating 2028.
- **Industrial placements** run 2027/28 with a final year in 2028/29 —
  graduating 2029.
- **Spring weeks** run in spring 2027, open to second-years on a four-year
  course. They sit two years before graduation, feeding a summer 2028
  internship and a 2029 graduation.

\* Filtered to `sponsorsVisa == "Yes"` (61 of 317). US roles need work
authorisation. The other 231 are blank rather than `"No"`, so this is
deliberately strict — it will hide some employers who do sponsor. Drop the
`programme_filter` argument on that tracker to see all 317.

US Finance has no spring weeks or industrial placements — both are UK-specific
formats, confirmed as returning zero rather than erroring.

## Watchlist

`WATCHLIST` in `check.py` holds company ids that bypass a tracker's filter
entirely. It exists because Oaktree Capital Management is a US firm whose
Trackr record has a **blank** `sponsorsVisa` — if its listing appears under US
Finance, the visa filter would drop it without trace. Watchlisting guarantees
it comes through.

Add ids as bare strings, e.g. `"oaktree-capital-management"`. They're the same
slug Trackr uses in its own `/company/<id>` URLs.

## Off-cycle start dates cannot be filtered

Off-cycle roles run in term time, so they suit a placement year. But **the API
carries no start-date field** — `eventDate` is empty on all 199 records, and
`openingDate` is when applications open, not when the role begins.

Only 19 of 199 names state a month (e.g. "Infrastructure Intern - January
2027", "Investment Banking Internship - September Start"). Filtering to a
specific start window would therefore drop ~90% of listings on missing data
rather than genuine mismatch, so no such filter is applied. The full programme
name is in every notification, which is where the month appears when it's
known at all.

Note also that all 199 off-cycle records already carry an `openingDate`,
unlike the other trackers where most are null. Alerts here fire when Trackr
*adds* a listing rather than when a date appears on an existing one.

## Rate limiting and the empty-array trap

The API signals trouble two ways:

1. **HTTP 429** under rapid sequential requests.
2. **An empty array with HTTP 200** when rate-limiting or degraded. This is the
   dangerous one — it is indistinguishable from "this tracker has no
   programmes" unless you know better.

`fetch()` retries both with exponential backoff, and `REQUEST_SPACING` puts
1.5s between trackers. If 429s start appearing in the logs, raise that value
before anything else.

**A persistently empty response raises rather than returning `[]`.** Every
tracker here has programmes in normal operation, so empty means broken. The
earlier behaviour would have wiped those entries from the snapshot, and the
empty list would then make `is_new_tracker` true on recovery — silently
re-baselining and losing every opening that occurred during the outage.

This is not hypothetical: on 19 Aug 2026 the API returned `[]` for every
region, industry and season for at least several hours. Trackr's own site
looked healthy because it was rendering from browser cache
(`transferSize: 0`).

A fetch failure aborts the whole run before any state is written, so the next
run retries from the same baseline. That's deliberate: a partial run that saved
state could silently skip a tracker's openings forever.

## Seasons and graduation year

`SEASON` is currently `"2027"` — summer 2027 programmes, which is the normal
cycle for someone graduating in **2028**.

If you graduate in **2029**, your main cycle is season 2028. That season is a
valid API parameter but currently returns zero programmes; Trackr populates it
roughly a year ahead, so expect it around mid-2027. When it fills, change
`SEASON` or make it a list.

Season 2029 doesn't exist yet at all — the API returns 422.

## Adding more trackers

Add entries to `TRACKERS` in `check.py`. Other available types:

- `off-cycle-internships`
- `graduate-programmes`

Other regions: `uk-law`, `france-finance`, `hong-kong-finance`. There is **no
US Tech tracker** on Trackr.

A newly added tracker is baselined on its first run — recorded without
alerting — so adding one never floods you with everything already open.

## Eligibility

Trackr's `eligibility` and `format` fields are **empty on every record**, so
degree requirements (e.g. "CS or Software Engineering only") cannot be filtered
automatically. That information lives on the employers' own sites. The
notification includes the full programme title, which is usually enough to
judge in a couple of seconds.

## Notes

- GitHub disables scheduled workflows after 60 days of repo inactivity. The
  snapshot commit on each change counts as activity, so this only matters
  during long stretches with no openings.
- Set `NTFY_TOKEN` as a secret too if you move to a protected ntfy topic.
