# Toggl → Jira Daily Sync

Automaticky každý pracovní den ráno stáhne time entries z Toggl za předchozí den,
agreguje je podle Jira issue key a zaloguje jako worklogy do Jiry.

## Logika zaokrouhlování
- Všechny záznamy pro jedno issue se **nejdříve sečtou**
- Teprve pak se výsledek **zaokrouhlí nahoru na 5 minut**

## Formát Toggl entries
Popis musí obsahovat Jira issue key: `PROJ-123 cokoliv dalšího`

## Setup

### 1. Vytvoř GitHub repozitář
```
gh repo create toggl-jira-sync --private
```

### 2. Přidej GitHub Secrets
Settings → Secrets and variables → Actions → New repository secret

| Secret | Hodnota |
|--------|---------|
| `TOGGL_API_TOKEN` | Toggl API token (Profile Settings → API Token) |
| `JIRA_EMAIL` | Tvůj email v Jiře |
| `JIRA_API_TOKEN` | Jira API token (id.atlassian.com → Security → API tokens) |

### 3. Nahraj soubory
```
git add .
git commit -m "Initial setup"
git push
```

### 4. Otestuj manuálně
GitHub → Actions → "Toggl → Jira Daily Sync" → Run workflow
