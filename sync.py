import os
import re
import math
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# --- Config from environment variables ---
TOGGL_API_TOKEN = os.environ["TOGGL_API_TOKEN"]
TOGGL_WORKSPACE_ID = os.environ.get("TOGGL_WORKSPACE_ID", "8060648")
JIRA_DOMAIN = os.environ.get("JIRA_DOMAIN", "inove-team")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]

JIRA_BASE_URL = f"https://{JIRA_DOMAIN}.atlassian.net"
ISSUE_KEY_PATTERN = re.compile(r"([A-Z]+-\d+)")


def round_up_to_5_minutes(seconds: int) -> int:
    """Round up total seconds to nearest 5 minutes."""
    five_minutes = 5 * 60
    return math.ceil(seconds / five_minutes) * five_minutes


def get_yesterday_range():
    """Return start and end of yesterday in UTC ISO format."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    return yesterday.isoformat(), today.isoformat()


def fetch_toggl_entries(start: str, end: str) -> list:
    """Fetch time entries from Toggl for given date range."""
    url = f"https://api.track.toggl.com/api/v9/workspace/{TOGGL_WORKSPACE_ID}/time_entries"
    params = {"start_date": start, "end_date": end}
    response = requests.get(
        url,
        params=params,
        auth=(TOGGL_API_TOKEN, "api_token"),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def extract_issue_key(description: str) -> str | None:
    """Extract Jira issue key from Toggl entry description."""
    if not description:
        return None
    match = ISSUE_KEY_PATTERN.search(description.upper())
    return match.group(1) if match else None


def aggregate_by_issue(entries: list) -> dict:
    """
    Group entries by issue key and sum durations.
    Entries with duration < 0 are still running — skip them.
    """
    totals = defaultdict(int)
    skipped = []

    for entry in entries:
        duration = entry.get("duration", 0)
        if duration < 0:
            skipped.append(entry.get("description", "(no description)"))
            continue

        description = entry.get("description", "")
        issue_key = extract_issue_key(description)

        if not issue_key:
            print(f"  ⚠️  Skipping (no issue key): '{description}' — {duration}s")
            continue

        totals[issue_key] += duration

    if skipped:
        print(f"  ⏭️  Skipped {len(skipped)} still-running entries")

    return dict(totals)


def post_worklog(issue_key: str, duration_seconds: int, started: str):
    """Post a worklog entry to Jira issue."""
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/worklog"
    payload = {
        "timeSpentSeconds": duration_seconds,
        "started": started,
    }
    response = requests.post(
        url,
        json=payload,
        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )
    return response


def format_duration(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def main():
    start, end = get_yesterday_range()
    date_label = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"\n🕐 Syncing Toggl → Jira for {date_label}")
    print(f"   Range: {start} → {end}\n")

    # 1. Fetch entries
    entries = fetch_toggl_entries(start, end)
    print(f"📥 Fetched {len(entries)} Toggl entries")

    if not entries:
        print("✅ Nothing to sync.")
        return

    # 2. Aggregate by issue
    totals = aggregate_by_issue(entries)

    if not totals:
        print("✅ No entries with valid issue keys found.")
        return

    print(f"\n📊 Aggregated {len(totals)} issue(s):")
    for key, secs in totals.items():
        rounded = round_up_to_5_minutes(secs)
        print(f"   {key}: {format_duration(secs)} → rounded to {format_duration(rounded)}")

    # 3. Post to Jira
    # Use start of yesterday as the "started" time for the worklog
    started_dt = datetime.fromisoformat(start).strftime("%Y-%m-%dT09:00:00.000+0000")
    print(f"\n🚀 Posting worklogs to Jira...")

    success = 0
    failed = 0
    for issue_key, raw_seconds in totals.items():
        rounded_seconds = round_up_to_5_minutes(raw_seconds)
        response = post_worklog(issue_key, rounded_seconds, started_dt)

        if response.status_code in (200, 201):
            print(f"   ✅ {issue_key}: {format_duration(rounded_seconds)} logged")
            success += 1
        else:
            print(f"   ❌ {issue_key}: Failed ({response.status_code}) — {response.text[:200]}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Done: {success} logged, {failed} failed")
    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
