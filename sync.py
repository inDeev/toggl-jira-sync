import os
import re
import math
import json
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# --- Config from environment variables ---
TOGGL_API_TOKEN = os.environ["TOGGL_API_TOKEN"]
TOGGL_WORKSPACE_ID = os.environ["TOGGL_WORKSPACE_ID"]
JIRA_DOMAIN = os.environ["JIRA_DOMAIN"]
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
NOTIFY_EMAIL = os.environ["NOTIFY_EMAIL"]
GIST_TOKEN = os.environ["GIST_TOKEN"]

JIRA_BASE_URL = f"https://{JIRA_DOMAIN}.atlassian.net"
ISSUE_KEY_PATTERN = re.compile(r"([A-Z]+-\d+)")
GIST_FILENAME = "toggl_jira_synced_dates.json"


def round_up_to_5_minutes(seconds: int) -> int:
    """Round up total seconds to nearest 5 minutes."""
    five_minutes = 5 * 60
    return math.ceil(seconds / five_minutes) * five_minutes


# ---------------------------------------------------------------------------
# Gist helpers
# ---------------------------------------------------------------------------

def gist_headers() -> dict:
    return {"Authorization": f"Bearer {GIST_TOKEN}", "Accept": "application/vnd.github+json"}


def find_gist() -> str | None:
    """Return gist ID if our tracking gist already exists, otherwise None."""
    response = requests.get("https://api.github.com/gists", headers=gist_headers(), timeout=15)
    response.raise_for_status()
    for gist in response.json():
        if GIST_FILENAME in gist.get("files", {}):
            return gist["id"]
    return None


def create_gist() -> str:
    """Create a new private gist and return its ID."""
    payload = {
        "description": "Toggl→Jira sync — already synced dates",
        "public": False,
        "files": {GIST_FILENAME: {"content": json.dumps([])}},
    }
    response = requests.post("https://api.github.com/gists", headers=gist_headers(), json=payload, timeout=15)
    response.raise_for_status()
    gist_id = response.json()["id"]
    print(f"📝 Created new tracking gist: {gist_id}")
    return gist_id


def load_synced_dates(gist_id: str) -> list:
    """Load list of already synced dates from gist."""
    response = requests.get(f"https://api.github.com/gists/{gist_id}", headers=gist_headers(), timeout=15)
    response.raise_for_status()
    raw = response.json()["files"][GIST_FILENAME]["content"]
    return json.loads(raw)


def save_synced_dates(gist_id: str, dates: list):
    """Save updated list of synced dates back to gist."""
    payload = {"files": {GIST_FILENAME: {"content": json.dumps(sorted(dates))}}}
    response = requests.patch(
        f"https://api.github.com/gists/{gist_id}", headers=gist_headers(), json=payload, timeout=15
    )
    response.raise_for_status()
    print(f"💾 Saved synced dates to gist")


def is_already_synced(date_label: str) -> tuple[str, list]:
    """Return (gist_id, synced_dates). Raises if date already synced."""
    gist_id = find_gist() or create_gist()
    synced_dates = load_synced_dates(gist_id)
    return gist_id, synced_dates


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def get_sync_date() -> datetime | None:
    """
    Vrátí explicitně zadané SYNC_DATE jako datetime objekt, nebo None při
    automatickém spuštění (zpracováváme včerejšek).
    """
    raw = os.environ.get("SYNC_DATE", "").strip()
    if raw:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return None


def get_target_date() -> datetime:
    """
    Vrátí datum, za které se bude synchronizovat:
    - Při automatickém spuštění: včerejšek
    - Při manuálním spuštění s SYNC_DATE: zadané datum
    """
    sync_date = get_sync_date()
    if sync_date:
        return sync_date
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)


def get_reference_date() -> datetime:
    """
    Vrátí datum, pro které se rozhoduje o týdenním/měsíčním přehledu.
    Logika: sync běží ráno a zpracovává předchozí den.
    - Při automatickém spuštění: dnes (= den po zpracovávaném včerejšku)
    - Při manuálním spuštění s SYNC_DATE: den po SYNC_DATE
    """
    sync_date = get_sync_date()
    if sync_date:
        return sync_date + timedelta(days=1)
    return datetime.now(timezone.utc)


def get_yesterday_range() -> tuple[str, str]:
    target = get_target_date().replace(hour=0, minute=0, second=0, microsecond=0)
    next_day = target + timedelta(days=1)
    return target.isoformat(), next_day.isoformat()


def get_last_week_range() -> tuple[str, str]:
    ref = get_reference_date().replace(hour=0, minute=0, second=0, microsecond=0)
    last_monday = ref - timedelta(days=ref.weekday() + 7)
    last_sunday = last_monday + timedelta(days=7)
    return last_monday.isoformat(), last_sunday.isoformat()


def get_last_month_range() -> tuple[str, str]:
    ref = get_reference_date()
    first_this_month = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = first_this_month
    last_month_start = (first_this_month - timedelta(days=1)).replace(day=1)
    return last_month_start.isoformat(), last_month_end.isoformat()


def is_monday() -> bool:
    return get_reference_date().weekday() == 0


def is_first_of_month() -> bool:
    return get_reference_date().day == 1


# ---------------------------------------------------------------------------
# Toggl helpers
# ---------------------------------------------------------------------------

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


def extract_project(issue_key: str) -> str:
    parts = issue_key.split("-")
    return parts[0] if len(parts) == 2 and parts[0].isalpha() else "OSTATNÍ"


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
            print(f"  ⚠️ Skipping (no issue key): '{description}' — {duration}s")
            continue
        totals[issue_key] += duration
    if skipped:
        print(f"  ⏭️ Skipped {len(skipped)} still-running entries")
    return dict(totals)


def aggregate_by_project(entries: list) -> dict:
    totals = defaultdict(int)
    for entry in entries:
        duration = entry.get("duration", 0)
        if duration < 0:
            continue
        description = entry.get("description", "")
        issue_key = extract_issue_key(description)
        project = extract_project(issue_key) if issue_key else "OSTATNÍ"
        totals[project] += duration
    return dict(totals)


# ---------------------------------------------------------------------------
# Jira helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_duration(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def build_project_summary_html(title: str, period_label: str, entries: list) -> str:
    by_project = aggregate_by_project(entries)
    if not by_project:
        return f"""
<div style="margin-top:28px;">
  <h3 style="color:#1565c0;border-bottom:2px solid #1565c0;padding-bottom:4px;">{title}</h3>
  <p style="color:#888;">Žádná data za {period_label}.</p>
</div>"""

    total = sum(by_project.values())
    rows = ""
    for project, secs in sorted(by_project.items(), key=lambda x: -x[1]):
        pct = (secs / total * 100) if total > 0 else 0
        bar_width = max(1, int(pct))
        rows += f"""<tr>
  <td style="padding:6px 12px;border-bottom:1px solid #eee;font-weight:bold;">{project}</td>
  <td style="padding:6px 12px;border-bottom:1px solid #eee;">{format_duration(secs)}</td>
  <td style="padding:6px 12px;border-bottom:1px solid #eee;">
    <div style="display:flex;align-items:center;gap:8px;">
      <div style="background:#1565c0;height:10px;width:{bar_width}px;border-radius:3px;min-width:2px;"></div>
      <span>{pct:.1f}%</span>
    </div>
  </td>
</tr>"""

    return f"""
<div style="margin-top:28px;">
  <h3 style="color:#1565c0;border-bottom:2px solid #1565c0;padding-bottom:4px;">{title}</h3>
  <p style="color:#555;margin:4px 0 8px;">Období: <strong>{period_label}</strong> — celkem <strong>{format_duration(total)}</strong></p>
  <table style="border-collapse:collapse;width:100%;">
    <thead>
      <tr style="background:#e3f2fd;">
        <th style="padding:8px 12px;text-align:left;">Projekt</th>
        <th style="padding:8px 12px;text-align:left;">Čas</th>
        <th style="padding:8px 12px;text-align:left;">Podíl</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
    <tfoot>
      <tr style="background:#e3f2fd;">
        <td style="padding:8px 12px;font-weight:bold;">Celkem</td>
        <td style="padding:8px 12px;font-weight:bold;">{format_duration(total)}</td>
        <td style="padding:8px 12px;">100%</td>
      </tr>
    </tfoot>
  </table>
</div>"""


# ---------------------------------------------------------------------------
# Email builders
# ---------------------------------------------------------------------------

def send_email(subject: str, html_body: str):
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={
            "from": "Toggl→Jira Sync <onboarding@resend.dev>",
            "to": [NOTIFY_EMAIL],
            "subject": subject,
            "html": html_body,
        },
        timeout=15,
    )
    if response.status_code == 200:
        print(f"📧 Email odeslán na {NOTIFY_EMAIL}")
    else:
        print(f"⚠️ Email se nepodařilo odeslat: {response.status_code} {response.text[:200]}")


def build_success_email(date_label: str, results: list, extra_html: str = "") -> tuple[str, str]:
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
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;">
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
  {extra_html}
</div>"""
    return subject, html


def build_skipped_email(date_label: str, extra_html: str = "") -> tuple[str, str]:
    subject = f"⏭️ Toggl→Jira sync {date_label} — již synchronizováno"
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;">
  <h2 style="color:#f57c00;">⏭️ Toggl → Jira sync {date_label}</h2>
  <p style="color:#888;">Tento den byl již dříve synchronizován — do Jiry nebylo nic přidáno.</p>
  {extra_html}
</div>"""
    return subject, html


def build_no_work_email(date_label: str, extra_html: str = "") -> tuple[str, str]:
    subject = f"📋 Toggl→Jira sync {date_label} — žádná práce"
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;">
  <h2 style="color:#555;">📋 Toggl → Jira sync {date_label}</h2>
  <p style="color:#888;">Za předchozí den nebyla zaznamenána žádná práce — do Jiry nebylo nic zalogováno.</p>
  {extra_html}
</div>"""
    return subject, html


def build_error_email(date_label: str, error_msg: str, failed_issues: list) -> tuple[str, str]:
    subject = f"❌ Toggl→Jira sync {date_label} — chyba"
    issues_html = "".join(f"<li>{i}</li>" for i in failed_issues) if failed_issues else ""
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;">
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Determine target date (yesterday or explicit SYNC_DATE)
    target = get_target_date()
    start = target.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end = (target + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    date_label = target.strftime("%Y-%m-%d")

    ref = get_reference_date()
    print(f"\n🕐 Syncing Toggl → Jira for {date_label}")
    print(f"   Range: {start} → {end}")
    print(f"   Reference date (for weekly/monthly check): {ref.strftime('%Y-%m-%d')} ({ref.strftime('%A')})\n")

    # --- Check gist for already synced dates ---
    gist_id, synced_dates = is_already_synced(date_label)
    already_synced = date_label in synced_dates

    if already_synced:
        print(f"⏭️ {date_label} already synced — skipping Jira upload")

    # --- Build optional summary sections (always, regardless of sync status) ---
    extra_html = ""
    if is_monday():
        print("📅 Monday detected — fetching last week summary...")
        w_start, w_end = get_last_week_range()
        w_label = f"{w_start[:10]} – {(datetime.fromisoformat(w_end) - timedelta(days=1)).strftime('%Y-%m-%d')}"
        week_entries = fetch_toggl_entries(w_start, w_end)
        extra_html += build_project_summary_html("📊 Přehled minulého týdne", w_label, week_entries)

    if is_first_of_month():
        print("📅 1st of month detected — fetching last month summary...")
        m_start, m_end = get_last_month_range()
        m_label = f"{m_start[:10]} – {(datetime.fromisoformat(m_end) - timedelta(days=1)).strftime('%Y-%m-%d')}"
        month_entries = fetch_toggl_entries(m_start, m_end)
        extra_html += build_project_summary_html("📊 Přehled minulého měsíce", m_label, month_entries)

    # --- If already synced, send skip email and exit ---
    if already_synced:
        subject, html = build_skipped_email(date_label, extra_html)
        send_email(subject, html)
        return

    # --- Fetch target day's entries ---
    try:
        entries = fetch_toggl_entries(start, end)
    except Exception as e:
        subject, html = build_error_email(date_label, str(e), [])
        send_email(subject, html)
        raise

    print(f"📥 Fetched {len(entries)} Toggl entries")

    if not entries:
        print("✅ No work that day.")
        subject, html = build_no_work_email(date_label, extra_html)
        send_email(subject, html)
        return

    totals = aggregate_by_issue(entries)
    if not totals:
        print("✅ No entries with valid issue keys.")
        subject, html = build_no_work_email(date_label, extra_html)
        send_email(subject, html)
        return

    print(f"\n📊 Aggregated {len(totals)} issue(s):")
    for key, secs in totals.items():
        rounded = round_up_to_5_minutes(secs)
        print(f"  {key}: {format_duration(secs)} → rounded to {format_duration(rounded)}")

    # --- Post to Jira ---
    started_dt = target.strftime("%Y-%m-%dT09:00:00.000+0000")
    print(f"\n🚀 Posting worklogs to Jira...")

    success_results = []
    failed_issues = []

    for issue_key, raw_seconds in totals.items():
        rounded_seconds = round_up_to_5_minutes(raw_seconds)
        response = post_worklog(issue_key, rounded_seconds, started_dt)
        if response.status_code in (200, 201):
            print(f"  ✅ {issue_key}: {format_duration(rounded_seconds)} logged")
            success_results.append({"issue": issue_key, "raw": raw_seconds, "rounded": rounded_seconds})
        else:
            print(f"  ❌ {issue_key}: Failed ({response.status_code}) — {response.text[:200]}")
            failed_issues.append(f"{issue_key}: {response.status_code}")

    print(f"\n{'='*40}")
    print(f"Done: {len(success_results)} logged, {len(failed_issues)} failed")

    if failed_issues:
        error_msg = f"{len(failed_issues)} issue(s) se nepodařilo zalogovat"
        subject, html = build_error_email(date_label, error_msg, failed_issues)
        send_email(subject, html)
        raise SystemExit(1)
    else:
        # Mark as synced only on full success
        synced_dates.append(date_label)
        save_synced_dates(gist_id, synced_dates)
        subject, html = build_success_email(date_label, success_results, extra_html)
        send_email(subject, html)


if __name__ == "__main__":
    main()
