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
minute. So the only number that matters is the run count:

| Schedule       | Runs/month | Billed minutes | % of free tier |
| -------------- | ---------- | -------------- | -------------- |
| Hourly         | ~730       | ~730           | 37%            |
| Every 30 min   | ~1,460     | ~1,460         | 73%            |
| Every 15 min   | ~2,920     | ~2,920         | 146% — over    |

Every 30 minutes fits with ~540 minutes to spare. Anything more frequent needs
a public repo, where minutes are unlimited.

Adding more entries to `TRACKERS` costs nothing extra: they run as additional
API calls inside the same job, and the job stays well under the one-minute
round-up either way.

## What's tracked

Five trackers, ~1,100 programmes, all on season 2027:

| Tracker    | Summer | Placements |
| ---------- | ------ | ---------- |
| UK Finance | 428    | 175        |
| UK Tech    | 285    | 160        |
| US Finance | 61\*   | —          |

Season 2027 covers both eligible routes: summer internships run in summer 2027
(graduating 2028), and industrial placements run 2027/28 with a final year in
2028/29 (graduating 2029).

**Spring weeks are deliberately excluded.** Spring Week 2027 targets first-year
students in 2026/27, who graduate 2029 on a three-year course or 2030 on a
four-year one — neither matches an eligible route.

\* Filtered to `sponsorsVisa == "Yes"` (61 of 317). US roles need work
authorisation. The other 231 are blank rather than `"No"`, so this is
deliberately strict — it will hide some employers who do sponsor. Drop the
`programme_filter` argument on that tracker to see all 317.

US Finance has no spring weeks or industrial placements — both are UK-specific
formats, confirmed as returning zero rather than erroring.

## Rate limiting

The API returns **429** under rapid sequential requests. `fetch()` retries with
exponential backoff, and `REQUEST_SPACING` puts 1.5s between trackers. If more
trackers are added and 429s start appearing in the logs, raise that value
before anything else.

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
