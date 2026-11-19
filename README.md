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
docker compose up -d              # or pull the published image (latest by default)
$env:FEEDLY_FILTER_TAG = "0.1"; docker compose up -d   # pull a specific version
```

The GUI is then on <http://localhost:8080>. The `./data` folder is mounted into the container, so the config,
session and history survive restarts. The image runs as a non-root user (uid 10001); on Linux make sure that user can
write to `./data`.

Images are built and published to GitHub Container Registry as `ghcr.io/<owner>/feedly-filter` by `.github/workflows/docker-publish.yml`:
pushes to `main` publish `latest`, `main` and `sha-…`, tags `vX.Y.Z` publish `X.Y.Z` and `X.Y`. Pull requests are built and smoke-tested but not pushed.
`FEEDLY_FILTER_TAG` selects the tag (default `latest`), e.g. `0.1`. `FEEDLY_FILTER_IMAGE` overrides the image name.

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
  Adding the first rule to a feed copies the folder's rules into it, so nothing that applied before stops applying.
  Global rules always apply.
- Folders are listed at the top of the **Feeds** page; click a folder to edit its rules, aging and whitelist.
- **Whitelist** entries are never marked, even when a rule matches.
- **Aging** marks entries older than N days by publication date. Set it globally, per folder or per feed; `0` turns it off.
- Entries saved for later are never marked.
- **Preview matches** shows how many unread entries a rule would mark right now, before you save it.

## Inspect

The **Inspect** page lists the unread entries of one feed or of a whole folder (choose either in the drop-down),
newest first, 20 per page (use *Previous* and *Next* to move between pages). For a folder, the entries of all its feeds
are merged; each feed is judged with its own rules. Columns: feed, title, author, URL, content, published date, decision
and reason. These are the four fields
rules can match (title, content, author, URL). Content is shown as plain text, cut to 200 characters; click it to see
up to 2000 characters. Rules match the original HTML of the summary. Each entry shows the
decision a run would make now and the reason: the rule that matched, aging, an exception, *saved for later*, or no match.
Nothing is changed by looking. Open **Rule** on an entry to block it (add a rule) or keep it (add an exception), at the
level of this feed, its folder or all feeds. The level is preselected from the feed or folder you chose at the top of the
page. The value is pre-filled with the entry title; shorten it if you want a
broader rule. The first rule added to a feed copies its folder's rules into the feed, so they keep applying; later
changes to the folder no longer reach that feed. Adding a rule that already exists (same field, operator and value,
ignoring case) is not repeated; you get a message instead.

## Dry-run and forced runs

- While **dry-run** is on, runs only record what would be marked. Open *History* to see the entries and the rule that matched each one.
- The dashboard has a **Dry run: ON/OFF** switch. Turning dry-run OFF asks for confirmation, because scheduled runs will then mark entries as read.
- **Run now** follows the dry-run setting. **Force run** applies the marks immediately, even with dry-run on.

## Schedule

Feeds are checked on their own interval (default 60 minutes, set in **Settings**; a feed can have its own, and an empty
field means the default). Each feed runs at its own minute inside every interval: with the 60-minute default and working
hours from 7:00, a feed runs once an hour between `HH:00` and `HH:59`, at a fixed minute that depends on the feed, so the
feeds do not all run at the same time. The **Feeds** page shows the next planned run of each feed.

Scheduled checks run only between **7:00 and 22:00** local time by default (the end hour is not included); change this
in Settings. Manual runs from the GUI are not limited by the hours.

## Stats

The **Stats** page counts every request sent to Feedly, split by type (profile check, subscriptions, unread counts,
feed entries, mark as read) and by outcome (2xx, 4xx, 5xx/429, network error). It shows a bar chart of requests per
day for the last 7, 14, 30 or 90 days, with a table view. Counts are stored in `history.db`.

## Sorting and filtering tables

Click a column header in any table (feeds, folders, rules, history, dashboard, stats) to sort by that column.
Click the same header again to reverse the order. Under the headers, type in a column's box to show only rows
that contain the text (case-insensitive). In number columns (such as *Processed* or *Interval*) the box takes a
comparison instead: `>0`, `<10`, `>=1`, `<=5`, `=3`; a plain number means exactly that value. A box with an
unfinished or invalid comparison turns red and filters nothing. Columns with a small set of values (the Inspect page's
*Decision* and *Reason*) get a drop-down instead, listing the values that appear in that column. Sorting and filtering
happen in the browser. Filters
stay set while the browser tab is open, also after opening a run's details and coming back; they are not saved on the server.

## Security

- The GUI has **no authentication**. Run it only on a trusted network, or put it behind a reverse proxy that does the auth.
- Do not commit `data/`. It is listed in `.gitignore`.

## Licence

MIT (see `LICENSE`, to be added).
