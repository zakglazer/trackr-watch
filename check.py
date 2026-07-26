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


def _visa_sponsors_only(programme):
    """US roles need work authorisation. Trackr flags 61 of 317 as sponsoring;
    the other 231 are blank rather than 'No', so this is deliberately strict
    and will hide some employers who do in fact sponsor."""
    return (programme.get("company") or {}).get("sponsorsVisa") == "Yes"


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


# Season 2027 covers both eligible routes:
#   summer-internships    -> intern summer 2027, graduate 2028
#   industrial-placements -> placement 2027/28, final year 2028/29, graduate 2029
# Spring weeks are deliberately absent: Spring Week 2027 targets first-years in
# 2026/27, who graduate 2029 (3yr) or 2030 (4yr) - neither eligible route.
TRACKERS = [
    _tracker("Finance", "uk-finance", "summer-internships", "Summer Internships"),
    _tracker("Finance", "uk-finance", "industrial-placements", "Industrial Placements"),
    _tracker("Tech", "uk-tech", "summer-internships", "Summer Internships"),
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
    """GET with backoff - the API rate-limits with 429 under rapid requests."""
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "trackr-watch/1.0"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


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
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


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


def send_alerts(topic, newly_open):
    if len(newly_open) > MAX_INDIVIDUAL_ALERTS:
        page = newly_open[0][0]["page"]
        notify(
            topic,
            f"{len(newly_open)} programmes just opened",
            "\n".join(f"{t['label']}: {describe(p)}" for t, p in newly_open),
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
        programmes = fetch(tracker["params"])
        if tracker["filter"]:
            # Filter before anything else, so excluded programmes never enter
            # the snapshot and can't resurface as "new" if the filter changes.
            programmes = [p for p in programmes if tracker["filter"](p)]

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
