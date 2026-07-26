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

## Adding more trackers

Add entries to `TRACKERS` in `check.py`. The same API serves the other tabs —
only the `type` parameter changes:

- `spring-weeks`
- `off-cycle-internships`
- `industrial-placements`
- `graduate-programmes`

`region`, `industry` and `season` vary the same way.

## Notes

- GitHub disables scheduled workflows after 60 days of repo inactivity. The
  snapshot commit on each change counts as activity, so this only matters
  during long stretches with no openings.
- Set `NTFY_TOKEN` as a secret too if you move to a protected ntfy topic.
