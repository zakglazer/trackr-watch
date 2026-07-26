# Trackr opening watcher

Polls the Trackr API every hour and pushes a phone notification when a
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
under load, so treat the hourly check as "within about 90 minutes".

## Cost

The repo is private, so Actions minutes are metered against the free tier's
2,000/month. Hourly is ~730 runs, and GitHub rounds each run up to a whole
minute — so this lands around 730–800 minutes, comfortably inside the
allowance. The job deliberately skips `setup-python` (the runner already has
Python 3, and `check.py` is stdlib-only) to stay under that round-up.

Going back to a 30-minute schedule would roughly double this to ~1,460
minutes. Still under the limit, but with little headroom if a run ever runs
long. Anything more frequent than that needs a public repo, where minutes are
unlimited.

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
