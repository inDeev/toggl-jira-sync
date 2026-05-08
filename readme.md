# Toggl → Jira Daily Sync

Automaticky každý den ráno stáhne time entries z Toggl za předchozí den, agreguje je podle Jira issue key, zaokrouhlí nahoru na 5 minut a zaloguje jako worklogy do Jiry. Po úspěšném syncu odešle souhrnný email přes SendGrid.

## Funkce

- **Denní sync** — každý den v 6:00 UTC (7:00 CET / 8:00 CEST)
- **Zaokrouhlení nahoru na 5 minut** — vždy po agregaci všech záznamů pro dané issue
- **Ochrana proti duplikátům** — synchronizované dny jsou uloženy v privátním GitHub Gist, opakované spuštění nic nepřepíše
- **Týdenní přehled** — každé pondělí přijde v emailu souhrn předchozího týdne podle projektů
- **Měsíční přehled** — každý 1. v měsíci přijde souhrn předchozího měsíce podle projektů
- **Email notifikace** — denní souhrn při úspěchu i chybě přes SendGrid
- **Manuální spuštění** — workflow lze spustit ručně z GitHub Actions

## Formát Toggl entries

Popis záznamu musí obsahovat Jira issue key — vždy na začátku:

```
PROJ-123 implementace přihlášení
SPBL-45 code review
```

Záznamy bez issue key jsou přeskočeny. Záznamy s neznámým prefixem jsou v přehledech vedeny jako `OSTATNÍ`.

## Nastavení

### 1. Vytvoř GitHub repozitář

```bash
gh repo create toggl-jira-sync --public
```

### 2. Přidej GitHub Secrets

Settings → Secrets and variables → Actions → New repository secret

| Secret | Popis | Kde získat |
|--------|-------|------------|
| `TOGGL_API_TOKEN` | Toggl API token | Toggl → Profile Settings → API Token |
| `TOGGL_WORKSPACE_ID` | ID workspace v Toggleu | Toggl → Settings → URL nebo pod názvem workspace |
| `JIRA_DOMAIN` | Subdoména Jiry (bez .atlassian.net) | Např. `moje-firma` |
| `JIRA_EMAIL` | Přihlašovací email do Jiry | Tvůj email |
| `JIRA_API_TOKEN` | Jira API token | id.atlassian.com → Security → API tokens |
| `SENDGRID_API_KEY` | SendGrid API klíč | sendgrid.com → Settings → API Keys |
| `NOTIFY_EMAIL` | Email pro souhrny | Tvůj email (musí být ověřen v SendGrid) |
| `GIST_TOKEN` | GitHub classic token se scope `gist` | github.com → Settings → Developer settings → Tokens (classic) |

### 3. Ověř odesílací email v SendGrid

SendGrid → Settings → Sender Authentication → Verify a Single Sender

Bez tohoto kroku emaily nepůjdou.

### 4. Nahraj soubory do repozitáře

```
toggl-jira-sync/
├── sync.py
├── README.md
└── .github/
    └── workflows/
        └── sync.yml
```

### 5. Otestuj manuálně

GitHub → Actions → **Toggl → Jira Daily Sync** → **Run workflow**

Po prvním úspěšném manuálním spuštění se cron začne spouštět automaticky.

## Logika zaokrouhlování

```
PROJ-123: 43 min + 18 min = 61 min → zaokrouhleno na 65 min (1h 5m)
PROJ-456: 28 min → zaokrouhleno na 30 min
```

Záznamy pro stejné issue se nejdříve sečtou, teprve pak se výsledek zaokrouhlí nahoru na nejbližších 5 minut.

## Ochrana proti duplikátům

Při prvním spuštění skript automaticky vytvoří privátní GitHub Gist (`toggl_jira_synced_dates.json`), kam ukládá seznam již synchronizovaných dat. Datum se zapíše pouze při úplném úspěchu — pokud sync selže, příští spuštění to zkusí znovu.

## Emailové notifikace

| Situace | Předmět emailu |
|---------|---------------|
| Úspěšný sync | ✅ Toggl→Jira sync 2026-05-08 — 3h 30m zalogováno |
| Již synchronizováno | ⏭️ Toggl→Jira sync 2026-05-08 — již synchronizováno |
| Žádná práce | 📋 Toggl→Jira sync 2026-05-08 — žádná práce |
| Chyba | ❌ Toggl→Jira sync 2026-05-08 — chyba |

V pondělí a 1. v měsíci je součástí emailu také projektový přehled — i v případě, že předchozí den nebyla žádná práce.
