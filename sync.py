import os
import re
import math
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# --- Config from environment variables ---
TOGGL_API_TOKEN = os.environ["TOGGL_API_TOKEN"]
TOGGL_WORKSPACE_ID = os.environ["TOGGL_WORKSPACE_ID"]
JIRA_DOMAIN = os.environ["JIRA_DOMAIN"]
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]
NOTIFY_EMAIL = os.environ["NOTIFY_EMAIL"]

JIRA_BASE_URL = f"https://{JIRA_DOMAIN}.atlassian.net"
ISSUE_KEY_PATTERN = re.compile(r"([A-Z]+-\d+)")


def round_up_to_5_minutes(seconds: int) -> int:
    five_minutes = 5 * 60
    return math.ceil(seconds / five_minutes) * five_minutes


def get_yesterday_range():
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    return yesterday.isoformat(), today.isoformat()


def fetch_toggl_entries(start: str, end: str) -> list:
    url = "https://api.track.toggl.com/api/v9/me/time_entries"
    params = {"start_date": start, "end_date": end}
    response = requests.get(url, params=params, auth=(TOGGL_API_TOKEN, "api_token"), timeout=30)
    response.raise_for_status()
    return response.json()


def extract_issue_key(description: str) -> str | None:
    if not description:
        return None
    match = ISSUE_KEY_PATTERN.search(description.upper())
    return match.group(1) if match else None


def aggregate_by_issue(entries: list) -> dict:
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
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/worklog"
    payload = {"timeSpentSeconds": duration_seconds, "started": started}
    response = requests.post(
        url, json=payload,
        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )
    return response


def format_duration(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def send_email(subject: str, html_body: str):
    response = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {SENDGRID_API_KEY}", "Content-Type": "application/json"},
        json={
            "personalizations": [{"to": [{"email": NOTIFY_EMAIL}]}],
            "from": {"email": NOTIFY_EMAIL, "name": "Toggl→Jira Sync"},
            "subject": subject,
            "content": [{"type": "text/html", "value": html_body}],
        },
        timeout=15,
    )
    if response.status_code == 202:
        print(f"📧 Email odeslán na {NOTIFY_EMAIL}")
    else:
        print(f"⚠️  Email se nepodařilo odeslat: {response.status_code} {response.text[:200]}")


def build_success_email(date_label: str, results: list) -> tuple[str, str]:
    total_seconds = sum(r["rounded"] for r in results)
    subject = f"✅ Toggl→Jira sync {date_label} — {format_duration(total_seconds)} zalogováno"
    rows = "".join(
        f"""<tr>
            <td style="padding:6px 12px;border-bottom:1px solid #eee;">
                <a href="https://{JIRA_DOMAIN}.atlassian.net/browse/{r['issue']}"
                   style="color:#0052cc;font-weight:bold;">{r['issue']}</a>
            </td>
            <td style="padding:6px 12px;border-bottom:1px solid #eee;color:#555;">{format_duration(r['raw'])}</td>
            <td style="padding:6px 12px;border-bottom:1px solid #eee;font-weight:bold;">{format_duration(r['rounded'])}</td>
        </tr>"""
        for r in results
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;">
        <h2 style="color:#2e7d32;">✅ Toggl → Jira sync proběhl úspěšně</h2>
        <p style="color:#555;">Datum: <strong>{date_label}</strong></p>
        <table style="border-collapse:collapse;width:100%;">
            <thead>
                <tr style="background:#f5f5f5;">
                    <th style="padding:8px 12px;text-align:left;">Issue</th>
                    <th style="padding:8px 12px;text-align:left;">Skutečný čas</th>
                    <th style="padding:8px 12px;text-align:left;">Zaokrouhleno</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
            <tfoot>
                <tr style="background:#e8f5e9;">
                    <td style="padding:8px 12px;font-weight:bold;">Celkem</td>
                    <td></td>
                    <td style="padding:8px 12px;font-weight:bold;">{format_duration(total_seconds)}</td>
                </tr>
            </tfoot>
        </table>
    </div>"""
    return subject, html


def build_error_email(date_label: str, error_msg: str, failed_issues: list) -> tuple[str, str]:
    subject = f"❌ Toggl→Jira sync {date_label} — chyba"
    issues_html = "".join(f"<li>{i}</li>" for i in failed_issues) if failed_issues else ""
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;">
        <h2 style="color:#c62828;">❌ Toggl → Jira sync selhal</h2>
        <p style="color:#555;">Datum: <strong>{date_label}</strong></p>
        <div style="background:#fff3f3;border-left:4px solid #c62828;padding:12px 16px;margin:16px 0;">
            <strong>Chyba:</strong><br>
            <code style="font-size:13px;">{error_msg}</code>
        </div>
        {"<p><strong>Neúspěšné issues:</strong></p><ul>" + issues_html + "</ul>" if issues_html else ""}
        <p style="color:#888;font-size:13px;">Zkontroluj GitHub Actions log pro detaily.</p>
    </div>"""
    return subject, html


def main():
    start, end = get_yesterday_range()
    date_label = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"\n🕐 Syncing Toggl → Jira for {date_label}")
    print(f"   Range: {start} → {end}\n")

    try:
        entries = fetch_toggl_entries(start, end)
    except Exception as e:
        subject, html = build_error_email(date_label, str(e), [])
        send_email(subject, html)
        raise

    print(f"📥 Fetched {len(entries)} Toggl entries")

    if not entries:
        print("✅ Nothing to sync.")
        return

    totals = aggregate_by_issue(entries)

    if not totals:
        print("✅ No entries with valid issue keys found.")
        return

    print(f"\n📊 Aggregated {len(totals)} issue(s):")
    for key, secs in totals.items():
        rounded = round_up_to_5_minutes(secs)
        print(f"   {key}: {format_duration(secs)} → rounded to {format_duration(rounded)}")

    started_dt = datetime.fromisoformat(start).strftime("%Y-%m-%dT09:00:00.000+0000")
    print(f"\n🚀 Posting worklogs to Jira...")

    success_results = []
    failed_issues = []

    for issue_key, raw_seconds in totals.items():
        rounded_seconds = round_up_to_5_minutes(raw_seconds)
        response = post_worklog(issue_key, rounded_seconds, started_dt)

        if response.status_code in (200, 201):
            print(f"   ✅ {issue_key}: {format_duration(rounded_seconds)} logged")
            success_results.append({"issue": issue_key, "raw": raw_seconds, "rounded": rounded_seconds})
        else:
            print(f"   ❌ {issue_key}: Failed ({response.status_code}) — {response.text[:200]}")
            failed_issues.append(f"{issue_key}: {response.status_code}")

    print(f"\n{'='*40}")
    print(f"Done: {len(success_results)} logged, {len(failed_issues)} failed")

    if failed_issues:
        error_msg = f"{len(failed_issues)} issue(s) se nepodařilo zalogovat"
        subject, html = build_error_email(date_label, error_msg, failed_issues)
        send_email(subject, html)
        raise SystemExit(1)
    else:
        subject, html = build_success_email(date_label, success_results)
        send_email(subject, html)


if __name__ == "__main__":
    main()
