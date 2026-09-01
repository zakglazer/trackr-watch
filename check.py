#!/usr/bin/env python3
"""Poll the Trackr API and push a notification when a programme opens."""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.the-trackr.com/programmes"
SITE = "https://app.the-trackr.com"
STATE_FILE = Path(__file__).parent / "state" / "seen.json"
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
MAX_INDIVIDUAL_ALERTS = 10
PRIORITY_HIGH = 4  # ntfy scale: 1 min, 3 default, 5 max
SEASON = "2027"
REQUEST_SPACING = 1.5  # seconds between trackers; the API returns 429 if hammered
NOTIFY_ATTEMPTS = 4
# ntfy rejects a message over 4096 bytes outright; stay clear of the edge.
MAX_MESSAGE_BYTES = 3800


# Companies that always get through, whatever a tracker's filter says. Oaktree
# is a US firm with a blank sponsorsVisa on Trackr, so the visa filter below
# would silently drop it if its listing lands under US Finance.
WATCHLIST = {"oaktree-capital-management"}


def _visa_sponsors_only(programme):
    """US roles need work authorisation. Trackr flags 61 of 317 as sponsoring;
    the other 231 are blank rather than 'No', so this is deliberately strict
    and will hide some employers who do in fact sponsor."""
    return (programme.get("company") or {}).get("sponsorsVisa") == "Yes"


def _passes_filter(tracker, programme):
    if (programme.get("company") or {}).get("id") in WATCHLIST:
        return True
    return tracker["filter"] is None or tracker["filter"](programme)


def _tracker(industry, slug, type_, type_label, programme_filter=None):
    region, industry_label = slug.split("-", 1)
    return {
        "label": f"{region.upper()} {industry_label.title()} / {type_label}",
        "page": f"{SITE}/{slug}/{type_}",
        "filter": programme_filter,
        "params": {
            "region": region.upper(),
            "industry": industry,
            "season": SEASON,
            "type": type_,
        },
    }


# Season 2027 covers all three eligible routes:
#   summer-internships    -> intern summer 2027, graduate 2028
#   industrial-placements -> placement 2027/28, final year 2028/29, graduate 2029
#   spring-weeks          -> spring 2027 in year 2 of a 4-year course, feeding a
#                            summer 2028 internship, graduate 2029
TRACKERS = [
    _tracker("Finance", "uk-finance", "summer-internships", "Summer Internships"),
    _tracker("Finance", "uk-finance", "spring-weeks", "Spring Weeks"),
    _tracker("Finance", "uk-finance", "industrial-placements", "Industrial Placements"),
    # Off-cycle exists for UK Finance only - UK Tech and US Finance both return
    # zero. Roles run in term time, so they suit a placement year rather than
    # study alongside.
    _tracker("Finance", "uk-finance", "off-cycle-internships", "Off-Cycle"),
    _tracker("Tech", "uk-tech", "summer-internships", "Summer Internships"),
    _tracker("Tech", "uk-tech", "spring-weeks", "Spring Weeks"),
    _tracker("Tech", "uk-tech", "industrial-placements", "Industrial Placements"),
    # US has no spring weeks or placements - both are UK-specific formats.
    _tracker(
        "Finance",
        "us-finance",
        "summer-internships",
        "Summer Internships",
        programme_filter=_visa_sponsors_only,
    ),
]


def fetch(params, attempts=5):
    """GET with backoff.

    The API signals trouble two ways: HTTP 429 under rapid requests, and - less
    obviously - an empty array with HTTP 200 when it is rate-limiting or
    degraded. Both are retried.

    A persistently empty result raises rather than returning []. Every tracker
    here has programmes in normal operation, so empty means broken. Returning
    it would wipe those entries from the snapshot, and the empty list would
    then make is_new_tracker true on recovery - silently re-baselining and
    losing every opening that happened during the outage.
    """
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "trackr-watch/1.0"})
    for attempt in range(attempts):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == attempts - 1:
                raise
            continue
        # Until Aug 2026 the API returned a bare array. It now returns
        # {"groups": [...], "programmes": [...]}. Accept either, so a rollback
        # on their side doesn't break us again.
        data = payload.get("programmes") if isinstance(payload, dict) else payload
        if data:
            return data
    raise RuntimeError(
        f"{params['region']}/{params['industry']}/{params['type']}: API returned "
        f"no programmes after {attempts} attempts - refusing to overwrite the snapshot"
    )


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_open(programme, now):
    opening = parse_date(programme.get("openingDate"))
    if opening is None or opening > now:
        return False
    closing = parse_date(programme.get("closingDate"))
    return closing is None or closing >= now


def load_state():
    if not STATE_FILE.exists():
        return None
    with STATE_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


def notify(topic, title, message, click=None, priority=PRIORITY_HIGH, icon=None, actions=None):
    # Published as JSON rather than headers: action buttons in header form are
    # comma-delimited, so any URL containing a comma would corrupt the message.
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "tags": ["tada"],
        "priority": priority,
    }
    if click:
        payload["click"] = click
    if icon:
        payload["icon"] = icon
    if actions:
        payload["actions"] = actions

    req = urllib.request.Request(
        NTFY_SERVER,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    token = os.environ.get("NTFY_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    # ntfy.sh is a free public service and does throw the occasional 5xx. An
    # unretried blip aborts the run before the snapshot is written, so the next
    # run re-detects the same openings and alerts again - noisy, and it leaves a
    # red workflow behind. 4xx is our own fault (bad topic, oversized message)
    # and will not fix itself, so only server errors and throttling are retried.
    for attempt in range(NOTIFY_ATTEMPTS):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            return
        except urllib.error.HTTPError as exc:
            retriable = exc.code == 429 or exc.code >= 500
            if not retriable or attempt == NOTIFY_ATTEMPTS - 1:
                raise
        except urllib.error.URLError:
            # Timeout or DNS/connection failure - transient by nature.
            if attempt == NOTIFY_ATTEMPTS - 1:
                raise


def describe(programme):
    company = (programme.get("company") or {}).get("name") or programme.get("companyId")
    return f"{company} - {programme.get('name')}"


def icon_for(programme):
    """Company logo via favicon lookup - Trackr's API carries no logo field."""
    careers = (programme.get("company") or {}).get("careersSite")
    if not careers:
        return None
    host = urllib.parse.urlparse(careers).hostname
    if not host:
        return None
    return f"https://www.google.com/s2/favicons?domain={host}&sz=128"


def clamp(lines, limit=MAX_MESSAGE_BYTES):
    """Join lines into a message ntfy will accept.

    The digest grows with the batch: 44 openings on 1 Sep 2026 already came to
    ~3.5KB of the 4096-byte ceiling, and a busier morning would go over. ntfy
    rejects an oversized message outright rather than truncating it, which would
    lose the whole alert, so trim here and say how many were dropped.
    """
    message = "\n".join(lines)
    if len(message.encode("utf-8")) <= limit:
        return message

    kept = []
    used = 0
    for index, line in enumerate(lines):
        footer = f"...and {len(lines) - index} more"
        cost = len(line.encode("utf-8")) + (1 if kept else 0)
        if used + cost + 1 + len(footer.encode("utf-8")) > limit:
            break
        kept.append(line)
        used += cost
    return "\n".join(kept + [f"...and {len(lines) - len(kept)} more"])


def send_alerts(topic, newly_open):
    if len(newly_open) > MAX_INDIVIDUAL_ALERTS:
        page = newly_open[0][0]["page"]
        notify(
            topic,
            f"{len(newly_open)} programmes just opened",
            clamp([f"{t['label']}: {describe(p)}" for t, p in newly_open]),
            click=page,
            actions=[{"action": "view", "label": "View on Trackr", "url": page}],
        )
        return

    for tracker, programme in newly_open:
        closing = parse_date(programme.get("closingDate"))
        body = [tracker["label"]]
        if closing:
            body.append(f"Closes {closing:%d %b %Y}")
        if programme.get("rolling"):
            body.append("Rolling deadline - apply early")

        apply_url = programme.get("url")
        actions = []
        if apply_url:
            actions.append({"action": "view", "label": "Apply now", "url": apply_url})
        actions.append(
            {"action": "view", "label": "View on Trackr", "url": tracker["page"]}
        )

        notify(
            topic,
            f"Now open: {describe(programme)}",
            "\n".join(body),
            click=apply_url or tracker["page"],
            icon=icon_for(programme),
            actions=actions,
        )


def main():
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        sys.exit("NTFY_TOPIC is not set")

    now = datetime.now(timezone.utc)
    previous = load_state()
    first_run = previous is None
    seen = (previous or {}).get("programmes", {})

    current = {}
    newly_open = []

    for index, tracker in enumerate(TRACKERS):
        if index:
            time.sleep(REQUEST_SPACING)
        # A failure here aborts the run before any state is written, so the next
        # run retries from the same baseline rather than silently skipping a
        # tracker and losing its openings.
        # Filter before anything else, so excluded programmes never enter the
        # snapshot and can't resurface as "new" if the filter changes.
        programmes = [
            p for p in fetch(tracker["params"]) if _passes_filter(tracker, p)
        ]

        # A tracker with no ids in the snapshot is newly added: record it as a
        # baseline instead of alerting on everything already open in it.
        is_new_tracker = not any(p["id"] in seen for p in programmes)
        print(
            f"{tracker['label']}: {len(programmes)} programmes"
            + (" (new tracker - baselining)" if is_new_tracker else "")
        )

        for programme in programmes:
            pid = programme["id"]
            opened = is_open(programme, now)
            current[pid] = {
                "opened": opened,
                "openingDate": programme.get("openingDate"),
                "label": describe(programme),
            }
            was_open = seen.get(pid, {}).get("opened")
            if opened and was_open is not True and not is_new_tracker:
                newly_open.append((tracker, programme))

    if first_run:
        save_state({"programmes": current})
        print(f"Baseline saved: {len(current)} tracked, {len(newly_open)} already open")
        return

    if newly_open:
        print(f"{len(newly_open)} newly open")
        send_alerts(topic, newly_open)
    else:
        print("No new openings")

    # Saved only after alerts land, so a push failure repeats rather than drops.
    save_state({"programmes": current})


if __name__ == "__main__":
    main()
