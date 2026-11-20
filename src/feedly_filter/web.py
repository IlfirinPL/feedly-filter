"""Web GUI on port 8080: dashboard, feeds, folders, rules, aging, settings, history, login."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from .client import FeedlyError
from .config import Config, ConfigStore, FeedSettings, FolderSettings, Rule
from .engine import Engine, FeedInfo
from .history import History, file_size_mb
from .rules import effective
from .scheduler import Scheduler
from .session import SessionStore, normalize_authorization

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


@dataclass
class Services:
    store: ConfigStore
    session: SessionStore
    history: History
    engine: Engine
    scheduler: Scheduler


def back(url: str, message: str, is_error: bool = False) -> RedirectResponse:
    key = "error" if is_error else "msg"
    separator = "&" if "?" in url else "?"
    return RedirectResponse(f"{url}{separator}{key}={quote(message)}", status_code=303)


def scope_url(scope: str) -> str:
    kind, _, key = scope.partition("|")
    if kind == "global":
        return "/rules"
    if kind == "folder":
        return f"/folder?name={quote(key)}"
    return f"/feed?id={quote(key, safe='')}"


def inspect_url(source: str, page_no: int = 1) -> str:
    query = []
    if source:
        query.append(f"source={quote(source, safe='')}")
    if page_no > 1:
        query.append(f"page={page_no}")
    return "/inspect" + (f"?{'&'.join(query)}" if query else "")


def inspect_heading(source: str, catalog: dict[str, FeedInfo]) -> str:
    kind, _, key = source.partition("|")
    if kind == "folder":
        return f"Folder: {key}"
    if kind == "feed" and key in catalog:
        return catalog[key].title
    return ""


def rule_source_label(config: Config, feed_id: str, folder_name: str) -> str:
    feed = config.find_feed(feed_id)
    if feed is not None and feed.override:
        return "own"
    folder = config.find_folder(folder_name) if folder_name else None
    if folder is not None and (folder.rules or folder.whitelist):
        return f"folder: {folder_name}"
    return "global only"


def scope_view(config: Config, catalog: dict[str, FeedInfo], scope: str) -> dict:
    kind, _, key = scope.partition("|")
    if kind == "global":
        return {
            "kind": "global", "heading": "Global rules", "scope": scope,
            "own_rules": config.global_rules, "own_whitelist": config.global_whitelist,
            "eff_rules": [(rule, "global") for rule in config.global_rules],
            "eff_whitelist": [(rule, "global") for rule in config.global_whitelist],
            "eff_aging": config.aging.days if config.aging.enabled else 0,
            "own_aging": None, "override": None, "interval": None, "key": "", "folder": "",
        }
    if kind == "folder":
        folder = config.find_folder(key)
        eff = effective(config, None, key)
        return {
            "kind": "folder", "heading": f"Folder: {key}", "scope": scope, "key": key, "folder": key,
            "own_rules": folder.rules if folder else [], "own_whitelist": folder.whitelist if folder else [],
            "eff_rules": eff.rules, "eff_whitelist": eff.whitelist, "eff_aging": eff.aging_days,
            "own_aging": folder.aging_days if folder else None, "override": None, "interval": None,
        }
    info = catalog.get(key)
    feed = config.find_feed(key)
    folder_name = info.folder if info else (feed.folder if feed else "")
    eff = effective(config, feed, folder_name)
    return {
        "kind": "feed", "heading": info.title if info else key, "scope": scope, "key": key, "folder": folder_name,
        "own_rules": feed.rules if feed else [], "own_whitelist": feed.whitelist if feed else [],
        "eff_rules": eff.rules, "eff_whitelist": eff.whitelist, "eff_aging": eff.aging_days,
        "own_aging": feed.aging_days if feed else None,
        "override": feed.override if feed else False,
        "interval": config.interval_for(key),
    }


def same_rule(first: Rule, second: Rule) -> bool:
    """Same field, operator and value; case is ignored like in matching, the comment does not count."""
    return (first.field, first.op, first.value.casefold()) == (second.field, second.op, second.value.casefold())


def adopt_folder_rules(feed: FeedSettings, folder: FolderSettings | None) -> None:
    """Copy the folder's rules into a feed that gets its own rules, so they keep applying to it."""
    if folder is not None:
        feed.rules = [rule.model_copy() for rule in folder.rules]
        feed.whitelist = [rule.model_copy() for rule in folder.whitelist]
    feed.override = True


def apply_scope_action(config: Config, catalog: dict[str, FeedInfo], scope: str, form: dict[str, str]) -> str:
    kind, _, key = scope.partition("|")
    info = catalog.get(key) if kind == "feed" else None
    if kind == "global":
        owner = config
    elif kind == "folder":
        owner = config.get_folder(key)
    elif kind == "feed":
        owner = config.get_feed(key, info.title if info else "", info.folder if info else "")
    else:
        raise ValueError(f"unknown scope {scope!r}")

    rules_list = config.global_rules if kind == "global" else owner.rules
    whitelist = config.global_whitelist if kind == "global" else owner.whitelist
    action = form.get("action", "")

    if action in ("add_rule", "add_whitelist"):
        rule = Rule(
            field=form.get("field", "title"),
            op=form.get("op", "contains"),
            value=form.get("value", ""),
            comment=form.get("comment", ""),
        )
        is_rule = action == "add_rule"
        folder = config.find_folder(info.folder) if info else None
        inherits = kind == "feed" and not owner.override
        if kind == "global":
            current = config.global_rules if is_rule else config.global_whitelist
        elif inherits:
            current = (folder.rules if is_rule else folder.whitelist) if folder else []
        else:
            current = owner.rules if is_rule else owner.whitelist
        if any(same_rule(existing, rule) for existing in current):
            return "Rule already exists, not added"
        if inherits:
            adopt_folder_rules(owner, folder)  # the folder rules keep applying to this feed
        if kind == "global":
            target = config.global_rules if is_rule else config.global_whitelist
        else:
            target = owner.rules if is_rule else owner.whitelist
        target.append(rule)
        return "Rule added"
    if action in ("delete", "toggle"):
        target = rules_list if form.get("list") == "rules" else whitelist
        index = int(form.get("index", "-1"))
        if not 0 <= index < len(target):
            raise ValueError("rule not found")
        if action == "delete":
            del target[index]
            return "Rule deleted"
        target[index].enabled = not target[index].enabled
        return "Rule updated"
    if action == "set_aging":
        if kind == "global":
            raise ValueError("global aging is set in Settings")
        text = form.get("days", "").strip()
        days = int(text) if text else None
        if days is not None and days < 0:
            raise ValueError("days must be 0 or more")
        owner.aging_days = days
        return "Aging saved"
    if action == "set_interval":
        if kind != "feed":
            raise ValueError("interval is set per feed")
        text = form.get("minutes", "").strip()
        if not text:
            owner.interval_minutes = None  # empty means: use the default interval
            return "Interval reset to the default"
        minutes = int(text)
        if minutes < 5:
            raise ValueError("interval must be at least 5 minutes")
        owner.interval_minutes = minutes
        return "Interval saved"
    if action == "set_override":
        if kind != "feed":
            raise ValueError("override is set per feed")
        owner.override = form.get("override") == "on"
        return "Inheritance saved"
    raise ValueError(f"unknown action {action!r}")


KIND_LABELS = {
    "profile": "Profile (session check)",
    "subscriptions": "Subscriptions",
    "categories": "Categories",
    "unread_counts": "Unread counts",
    "stream_contents": "Feed entries",
    "mark_as_read": "Mark as read",
    "other": "Other",
}
OUTCOMES = ("2xx", "4xx", "5xx / 429", "network error", "other")
INSPECT_PAGE_SIZE = 20  # entries per page on the Inspect page
CHART_W, CHART_H = 720, 220
PAD_L, PAD_T, PAD_R, PAD_B = 44, 12, 8, 28


def outcome(status: int) -> str:
    if status == 0:
        return "network error"
    if 200 <= status < 300:
        return "2xx"
    if 400 <= status < 500 and status != 429:
        return "4xx"
    if status == 429 or status >= 500:
        return "5xx / 429"
    return "other"


def daily_bars(series: list[tuple[str, int]]) -> dict:
    """Geometry for the inline SVG bar chart: one bar per day, scaled to the busiest day."""
    top = max((count for _, count in series), default=0) or 1
    plot_w = CHART_W - PAD_L - PAD_R
    plot_h = CHART_H - PAD_T - PAD_B
    slot = plot_w / max(len(series), 1)
    step = max(1, -(-len(series) // 10))  # label about 10 days at most
    bars = []
    for index, (day, count) in enumerate(series):
        height = count / top * plot_h
        bars.append({
            "x": PAD_L + index * slot + 2,
            "y": PAD_T + plot_h - height,
            "w": max(slot - 4, 2),
            "h": height,
            "day": day,
            "count": count,
            "label": day[5:] if index % step == 0 else "",
            "label_x": PAD_L + index * slot + slot / 2,
        })
    grid = [{"y": PAD_T + plot_h * share, "value": round(top * (1 - share))} for share in (0, 0.5, 1)]
    return {"w": CHART_W, "h": CHART_H, "bars": bars, "grid": grid, "baseline": PAD_T + plot_h,
            "left": PAD_L, "right": CHART_W - PAD_R}


def create_app(services: Services) -> FastAPI:
    app = FastAPI(title="feedly-filter", docs_url=None, redoc_url=None)
    store, session, history, engine = services.store, services.session, services.history, services.engine

    def page(request: Request, template: str, error: str = "", **context) -> Response:
        context.setdefault("title", template)
        return TEMPLATES.TemplateResponse(
            request,
            template,
            {
                "msg": request.query_params.get("msg", ""),
                "error": error or request.query_params.get("error", ""),
                "config": store.config,
                **context,
            },
        )

    def refresh_catalog_message() -> str:
        try:
            engine.refresh_catalog()
            return ""
        except FeedlyError as error:
            return f"Feedly not reachable with the saved session: {error}. See Login."

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/")
    def index(request: Request):
        error = refresh_catalog_message()
        return page(request, "index.html", error=error, title="Dashboard",
                    runs=history.recent_runs(15), session_status=session.status(),
                    feed_count=len(engine.catalog), running=engine.is_running(),
                    scheduler_active=store.config.is_active_hour(engine.clock()))

    @app.post("/run")
    async def run_now(request: Request):
        form = await request.form()
        feed_id = str(form.get("feed_id", ""))
        folder = str(form.get("folder", ""))
        # "force" applies the marks now, even when dry-run is on; "normal" follows the dry-run setting.
        force = form.get("mode") == "force"
        feed_ids = [feed_id] if feed_id else None
        label = ""
        if folder:
            refresh_error = refresh_catalog_message()
            if refresh_error:
                return back("/", refresh_error, is_error=True)
            # An empty list would mean "all feeds", so a folder without feeds is refused.
            feed_ids = [info.id for info in engine.catalog.values() if info.folder == folder]
            if not feed_ids:
                return back("/", f"Folder {folder} has no feeds", is_error=True)
            label = f" for folder {folder}"
        started = engine.run_in_background(feed_ids, dry_run=False if force else None)
        if not started:
            return back("/", "A run is already in progress", is_error=True)
        if force:
            return back("/", f"Forced run started{label}: entries will be marked as read")
        return back("/", f"Run started{label}")

    @app.get("/run/status")
    def run_status() -> JSONResponse:
        return JSONResponse({"running": engine.is_running()})

    @app.post("/dry-run")
    async def toggle_dry_run(request: Request):
        form = await request.form()
        dry = str(form.get("state", "on")) == "on"
        store.update(lambda config: setattr(config, "dry_run", dry))
        if dry:
            return back("/", "Dry run ON: runs only report, nothing is marked")
        return back("/", "Dry run OFF: matching entries will be marked as read")

    @app.get("/feeds")
    def feeds(request: Request):
        error = refresh_catalog_message()
        rows = []
        for info in sorted(engine.catalog.values(), key=lambda item: item.title.casefold()):
            feed = store.config.find_feed(info.id)
            rows.append({
                "info": info,
                "interval": store.config.interval_for(info.id),
                "next_run": store.config.next_scheduled_run(info.id, engine.clock()).strftime("%Y-%m-%d %H:%M"),
                "rule_source": rule_source_label(store.config, info.id, info.folder),
                "own_rules": len(feed.rules) if feed else 0,
            })
        folders = {folder.name: {"name": folder.name, "feeds": 0, "unread": 0} for folder in store.config.folders}
        for info in engine.catalog.values():
            if info.folder:
                summary = folders.setdefault(info.folder, {"name": info.folder, "feeds": 0, "unread": 0})
                summary["feeds"] += 1
                summary["unread"] += info.unread
        return page(request, "feeds.html", error=error, title="Feeds", rows=rows,
                    folders=sorted(folders.values(), key=lambda item: item["name"].casefold()))

    @app.post("/feeds/interval")
    async def feeds_interval(request: Request):
        form = await request.form()
        ids = form.getlist("ids")
        try:
            minutes = int(str(form.get("minutes", "60")))
            if minutes < 5:
                raise ValueError("interval must be at least 5 minutes")
            if not ids:
                raise ValueError("select at least one feed")
        except ValueError as error:
            return back("/feeds", str(error), is_error=True)

        def change(config: Config) -> None:
            for feed_id in ids:
                info = engine.catalog.get(feed_id)
                config.get_feed(feed_id, info.title if info else "", info.folder if info else "").interval_minutes = minutes

        store.update(change)
        return back("/feeds", f"Interval set to {minutes} min for {len(ids)} feed(s)")

    @app.get("/feed")
    def feed_detail(request: Request, id: str):
        error = refresh_catalog_message()
        return page(request, "scope.html", error=error, title="Feed",
                    **scope_view(store.config, engine.catalog, f"feed|{id}"))

    @app.get("/folders")
    def folders():
        return RedirectResponse("/feeds")  # folders are listed on the Feeds page now

    @app.get("/folder")
    def folder_detail(request: Request, name: str):
        return page(request, "scope.html", title="Folder",
                    **scope_view(store.config, engine.catalog, f"folder|{name}"))

    @app.get("/rules")
    def global_rules(request: Request):
        return page(request, "scope.html", title="Global rules",
                    **scope_view(store.config, engine.catalog, "global"))

    @app.post("/scope")
    async def scope_action(request: Request):
        form = {key: str(value) for key, value in (await request.form()).items()}
        scope = form.get("scope", "global")
        refresh_catalog_message()  # the feed's folder is needed to keep inherited rules
        try:
            message = store.update(lambda config: apply_scope_action(config, engine.catalog, scope, form))
        except (ValueError, IndexError) as error:
            return back(scope_url(scope), str(error), is_error=True)
        return back(scope_url(scope), message)

    @app.post("/scope/preview")
    async def scope_preview(request: Request):
        """Show how many unread entries the rule would mark now. Nothing is saved."""
        form = {key: str(value) for key, value in (await request.form()).items()}
        scope = form.get("scope", "global")
        try:
            rule = Rule(field=form.get("field", "title"), op=form.get("op", "contains"),
                        value=form.get("value", ""), comment=form.get("comment", ""))
            preview = engine.preview_rule(scope, rule)
        except (ValueError, FeedlyError) as error:
            return back(scope_url(scope), f"Preview failed: {error}", is_error=True)
        return page(request, "scope.html", title="Preview", preview=preview, rule=rule,
                    **scope_view(store.config, engine.catalog, scope))

    @app.get("/inspect")
    def inspect_page(request: Request, source: str = "", page_no: int = Query(1, alias="page", ge=1)):
        error = refresh_catalog_message()
        found: list = []
        if source and not error:
            try:
                found = engine.inspect_entries(source)
            except (FeedlyError, ValueError) as exc:
                error = f"Could not read {source.partition('|')[0]}: {exc}"
        pages = max(-(-len(found) // INSPECT_PAGE_SIZE), 1)
        page_no = min(page_no, pages)
        start = (page_no - 1) * INSPECT_PAGE_SIZE
        entries = found[start:start + INSPECT_PAGE_SIZE]
        folders = sorted({info.folder for info in engine.catalog.values() if info.folder}, key=str.casefold)
        feeds = sorted(engine.catalog.values(), key=lambda item: item.title.casefold())
        sources = [(f"folder|{name}", f"Folder: {name}") for name in folders]
        sources += [(f"feed|{info.id}", info.title) for info in feeds]
        return page(request, "inspect.html", error=error, title="Inspect", sources=sources,
                    selected=source, heading=inspect_heading(source, engine.catalog),
                    entries=entries, total=len(found),
                    first=start + 1 if entries else 0, last=start + len(entries),
                    page_no=page_no, pages=pages,
                    prev_url=inspect_url(source, page_no - 1) if page_no > 1 else "",
                    next_url=inspect_url(source, page_no + 1) if page_no < pages else "")

    @app.post("/inspect/rule")
    async def inspect_rule(request: Request):
        form = {key: str(value) for key, value in (await request.form()).items()}
        source = form.get("source", "")
        page_no = int(form["page"]) if form.get("page", "").isdigit() else 1
        refresh_catalog_message()  # the feed's folder is needed to keep inherited rules
        try:
            message = store.update(lambda config: apply_scope_action(config, engine.catalog, form.get("scope", ""), form))
        except (ValueError, IndexError) as error:
            return back(inspect_url(source, page_no), str(error), is_error=True)
        return back(inspect_url(source, page_no), message)

    @app.get("/settings")
    def settings(request: Request):
        return page(request, "settings.html", title="Settings")

    @app.post("/settings")
    async def save_settings(request: Request):
        form = await request.form()
        try:
            days = int(str(form.get("days", "7")))
            limit = int(str(form.get("history_limit_mb", "100")))
            default_interval = int(str(form.get("default_interval_minutes", "60")))
            active_from = int(str(form.get("active_from", "7")))
            active_until = int(str(form.get("active_until", "22")))
            timezone = str(form.get("timezone", "Europe/Warsaw")).strip()
            if days < 0 or limit < 1:
                raise ValueError("days must be 0 or more, history limit at least 1 MB")
            if default_interval < 5:
                raise ValueError("default interval must be at least 5 minutes")
            if not 0 <= active_from < active_until <= 24:
                raise ValueError("working hours: start must be before end, between 0 and 24")
            ZoneInfo(timezone)
        except (ValueError, KeyError) as error:
            return back("/settings", f"Not saved: {error}", is_error=True)

        def change(config: Config) -> None:
            config.dry_run = form.get("dry_run") == "on"
            config.aging.enabled = form.get("aging_enabled") == "on"
            config.aging.days = days
            config.history_limit_mb = limit
            config.default_interval_minutes = default_interval
            config.active_from = active_from
            config.active_until = active_until
            config.timezone = timezone

        store.update(change)
        return back("/settings", "Settings saved")

    @app.get("/history")
    def history_page(request: Request, feed: str = "", run: int = 0):
        error = refresh_catalog_message()
        # A run's entries are listed in full; the default view is capped to the newest 200.
        marks = history.recent_marks(feed or None, run_id=run or None, limit=1000 if run else 200)
        return page(request, "history.html", error=error, title="History",
                    runs=history.recent_runs(30), marks=marks, run_id=run,
                    selected=feed, feeds=sorted(engine.catalog.values(), key=lambda item: item.title.casefold()),
                    size_mb=round(file_size_mb(history.path), 2))

    @app.get("/stats")
    def stats_page(request: Request, days: int = 14):
        days = min(max(days, 1), 90)
        today = datetime.now(UTC).date()
        since = (today - timedelta(days=days - 1)).isoformat()
        by_kind: dict[str, dict[str, int]] = {}
        for kind, status, count in history.request_stats(since):
            row = by_kind.setdefault(kind, dict.fromkeys(OUTCOMES, 0))
            row[outcome(status)] += count
        daily = dict(history.daily_requests(since))
        series = []
        for index in range(days):
            day = (today - timedelta(days=days - 1 - index)).isoformat()
            series.append((day, daily.get(day, 0)))
        rows = [
            {"label": KIND_LABELS.get(kind, kind), "outcomes": row, "total": sum(row.values())}
            for kind, row in sorted(by_kind.items(), key=lambda item: -sum(item[1].values()))
        ]
        totals = {name: sum(row["outcomes"][name] for row in rows) for name in OUTCOMES}
        return page(request, "stats.html", title="Stats", days=days, windows=(7, 14, 30, 90),
                    rows=rows, outcomes=OUTCOMES, totals=totals, grand_total=sum(totals.values()),
                    chart=daily_bars(series), series=series)

    @app.get("/login")
    def login_page(request: Request):
        return page(request, "login.html", title="Login", session_status=session.status())

    @app.post("/login")
    async def save_session(request: Request):
        form = await request.form()
        authorization = str(form.get("authorization", "")).strip()
        cookie = str(form.get("cookie", "")).strip()
        if not authorization:
            return back("/login", "Paste the authorization token", is_error=True)
        headers = {"Authorization": normalize_authorization(authorization)}
        if cookie:
            headers["Cookie"] = cookie
        # Check the token against Feedly first; it is saved only if the read-only call succeeds.
        try:
            with engine.client_factory(headers) as client:
                client.profile()
        except (FeedlyError, httpx.HTTPError) as error:
            return back("/login", f"Token rejected, not saved: {error}", is_error=True)
        session.save(authorization, cookie)
        engine.forget_catalog()
        return back("/login", "Token works, session saved")

    return app
