"""Web GUI on port 8080: dashboard, feeds, folders, rules, aging, settings, history, login."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from .client import FeedlyError
from .config import Config, ConfigStore, Rule
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
        "interval": feed.interval_minutes if feed else 60,
    }


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
        (rules_list if action == "add_rule" else whitelist).append(rule)
        if kind == "feed":
            owner.override = True  # own rules replace the folder rules from now on
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
        minutes = int(form.get("minutes", "60"))
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
                    feed_count=len(engine.catalog))

    @app.post("/run")
    async def run_now(request: Request):
        form = await request.form()
        feed_id = str(form.get("feed_id", ""))
        started = engine.run_in_background([feed_id] if feed_id else None)
        if not started:
            return back("/", "A run is already in progress", is_error=True)
        return back("/", "Run started")

    @app.get("/feeds")
    def feeds(request: Request):
        error = refresh_catalog_message()
        rows = []
        for info in sorted(engine.catalog.values(), key=lambda item: item.title.casefold()):
            feed = store.config.find_feed(info.id)
            rows.append({
                "info": info,
                "interval": feed.interval_minutes if feed else 60,
                "rule_source": rule_source_label(store.config, info.id, info.folder),
                "own_rules": len(feed.rules) if feed else 0,
            })
        return page(request, "feeds.html", error=error, title="Feeds", rows=rows)

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
    def folders(request: Request):
        error = refresh_catalog_message()
        names = {info.folder for info in engine.catalog.values() if info.folder}
        names |= {folder.name for folder in store.config.folders}
        return page(request, "folders.html", error=error, title="Folders", names=sorted(names, key=str.casefold))

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
        try:
            message = store.update(lambda config: apply_scope_action(config, engine.catalog, scope, form))
        except (ValueError, IndexError) as error:
            return back(scope_url(scope), str(error), is_error=True)
        return back(scope_url(scope), message)

    @app.get("/settings")
    def settings(request: Request):
        return page(request, "settings.html", title="Settings")

    @app.post("/settings")
    async def save_settings(request: Request):
        form = await request.form()
        try:
            days = int(str(form.get("days", "7")))
            limit = int(str(form.get("history_limit_mb", "100")))
            if days < 0 or limit < 1:
                raise ValueError("days must be 0 or more, history limit at least 1 MB")
        except ValueError as error:
            return back("/settings", str(error), is_error=True)

        def change(config: Config) -> None:
            config.dry_run = form.get("dry_run") == "on"
            config.aging.enabled = form.get("aging_enabled") == "on"
            config.aging.days = days
            config.history_limit_mb = limit
            config.timezone = str(form.get("timezone", config.timezone)).strip() or config.timezone

        store.update(change)
        return back("/settings", "Settings saved")

    @app.get("/history")
    def history_page(request: Request, feed: str = ""):
        error = refresh_catalog_message()
        return page(request, "history.html", error=error, title="History",
                    runs=history.recent_runs(30), marks=history.recent_marks(feed or None),
                    selected=feed, feeds=sorted(engine.catalog.values(), key=lambda item: item.title.casefold()),
                    size_mb=round(file_size_mb(history.path), 2))

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
