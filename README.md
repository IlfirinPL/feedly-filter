# feedly-filter

Marks Feedly entries as read using per-feed blacklist rules and aging (older than N days, by publication date).
Configuration lives in a `data/` folder; a small web GUI runs on port 8080.

> **Status:** early version. The automatic login is not finished yet (use the Login page with a pasted token).
> The Feedly web API is unofficial and can change without notice. Use at your own risk.

## Requirements

- Python 3.12 or newer
- A Feedly account (username and password; the web login is protected by Cloudflare, see *Session* below)

## Setup and run

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\feedly-filter
```

Open <http://localhost:8080>. Environment variables:

| Variable   | Default | Meaning                    |
|------------|---------|----------------------------|
| `DATA_DIR` | `data`  | folder with config and state |
| `PORT`     | `8080`  | port of the web GUI        |

Tests: `.\.venv\Scripts\python -m pytest`

## Docker

```powershell
docker compose up -d --build      # build locally and start
docker compose up -d              # or pull the published image (set FEEDLY_FILTER_IMAGE if needed)
```

The GUI is then on <http://localhost:8080>. The `./data` folder is mounted into the container, so the config,
session and history survive restarts. The image runs as a non-root user (uid 10001); on Linux make sure that user can
write to `./data`.

Images are built and published to GitHub Container Registry by `.github/workflows/docker-publish.yml`:
pushes to `main` publish `latest` and `sha-…`, tags `vX.Y.Z` publish `X.Y.Z` and `X.Y`. Pull requests are built and smoke-tested but not pushed.

## The data folder

```
data/
  config.yaml   # rules, folders, feed settings, aging, dry-run (created on first start)
  session.json  # Feedly token and cookies — secret, git-ignored
  history.db    # runs and marked entries (SQLite, capped at 100 MB by default)
  auth.yaml     # username and password — not read by the app yet, git-ignored
```

`config.yaml` can be edited by hand; changes made in the GUI are written back to it.
A new install starts in **dry-run** mode.

## Session

Automatic login with a headless browser is blocked by Cloudflare. Sign in to feedly.com in your normal browser, then
on the **Login** page paste the `authorization` header value (and optionally the `cookie` header) from the browser's
developer tools (Network tab, any request to `cloud.feedly.com`). The token is checked against Feedly before it is saved.
Treat the token like a password: it gives full access to the account.

## Rules

- A rule has a **field** (`title`, `content`, `author`, `url`), an **operator** (`contains`, `starts with`, `equals`,
  `regex`) and a **value**. Matching ignores letter case.
- Rules can be global (all feeds), per folder or per feed. A feed that overrides its folder uses only its own rules.
  Global rules always apply.
- **Whitelist** entries are never marked, even when a rule matches.
- **Aging** marks entries older than N days by publication date. Set it globally, per folder or per feed; `0` turns it off.
- Entries saved for later are never marked.
- **Preview matches** shows how many unread entries a rule would mark right now, before you save it.

## Dry-run and forced runs

- While **dry-run** is on, runs only record what would be marked. Open *History* to see the entries and the rule that matched each one.
- The dashboard has a **Dry run: ON/OFF** switch. Turning dry-run OFF asks for confirmation, because scheduled runs will then mark entries as read.
- **Run now** follows the dry-run setting. **Force run** applies the marks immediately, even with dry-run on.

## Schedule

Feeds are checked on their own interval (default 60 minutes, set in **Settings**; a feed can have its own, and an empty
field means the default). Scheduled checks run only between **7:00 and 22:00** local time by default (the end hour is
not included); change this in Settings. Manual runs from the GUI are not limited by the hours.

## Stats

The **Stats** page counts every request sent to Feedly, split by type (profile check, subscriptions, unread counts,
feed entries, mark as read) and by outcome (2xx, 4xx, 5xx/429, network error). It shows a bar chart of requests per
day for the last 7, 14, 30 or 90 days, with a table view. Counts are stored in `history.db`.

## Security

- The GUI has **no authentication**. Run it only on a trusted network, or put it behind a reverse proxy that does the auth.
- Do not commit `data/`. It is listed in `.gitignore`.

## Licence

MIT (see `LICENSE`, to be added).
