"""Exploration: log in to feedly.com and probe the web API.

Credentials are read from data/auth.yaml. Nothing secret is printed or stored:
cookie values, tokens and response contents are never written out, only
status codes and JSON shapes. Output goes to data/explore/ (git-ignored).

Use --headed when Cloudflare asks to "Verify you are human": the window opens
and you click the checkbox; the script waits for it.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
AUTH_FILE = ROOT / "data" / "auth.yaml"
OUT_DIR = ROOT / "data" / "explore"
LOGIN_URL = "https://feedly.com/i/login"
INTERESTING = ("feedly.com", "cloud.feedly.com")
# Read-only GET endpoints to check once logged in. Nothing is written.
PROBE_URLS = [
    "https://cloud.feedly.com/v3/profile",
    "https://cloud.feedly.com/v3/subscriptions",
    "https://cloud.feedly.com/v3/categories",
    "https://cloud.feedly.com/v3/markers/counts",
]


def load_credentials() -> tuple[str, str]:
    auth = yaml.safe_load(AUTH_FILE.read_text(encoding="utf-8"))["feedly"]
    return auth["username"], auth["password"]


def describe_shape(value, depth: int = 0):
    """Return the structure of a JSON value without its contents."""
    if depth > 2:
        return type(value).__name__
    if isinstance(value, dict):
        return {key: describe_shape(item, depth + 1) for key, item in list(value.items())[:25]}
    if isinstance(value, list):
        return {"list_len": len(value), "item": describe_shape(value[0], depth + 1) if value else None}
    return type(value).__name__


def probe_api(context, log: list[dict]) -> None:
    for url in PROBE_URLS:
        response = context.request.get(url)
        entry = {"method": "GET", "url": url, "status": response.status}
        if response.ok:
            try:
                entry["shape"] = describe_shape(response.json())
            except ValueError:
                entry["shape"] = "not JSON"
        log.append(entry)
        print(f"GET {url} -> {response.status}")
        if "shape" in entry:
            print("   shape:", json.dumps(entry["shape"], ensure_ascii=False)[:600])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    args = parser.parse_args()
    password_timeout = 180000 if args.headed else 15000

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    username, password = load_credentials()
    log: list[dict] = []

    with sync_playwright() as p:
        # Edge is used because the corporate proxy breaks Playwright's own
        # Chromium download. The Docker image will use bundled Chromium.
        # A persistent profile keeps cookies between runs. In headed mode you
        # complete any Cloudflare check once in the visible window; later runs
        # reuse that session instead of logging in again.
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(OUT_DIR / "profile"),
            channel="msedge",
            headless=not args.headed,
            locale="en-US",
        )
        page = context.new_page()

        def on_response(response):
            if any(host in response.url for host in INTERESTING):
                log.append(
                    {
                        "method": response.request.method,
                        "url": response.url.split("?")[0],
                        "status": response.status,
                        "content_type": response.headers.get("content-type", ""),
                    }
                )

        page.on("response", on_response)

        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        page.screenshot(path=str(OUT_DIR / "01_login_page.png"), full_page=True)

        email_box = page.locator("input[type=email], input[name=email], input[name=login], input[id*=email]").first
        if email_box.count() == 0:
            # Landing page: the real login form opens after clicking "Log in".
            print("No form yet, clicking 'Log in'. URL:", page.url.split("?")[0])
            page.get_by_role("button", name="Log in").or_(page.get_by_role("link", name="Log in")).first.click()
            page.wait_for_timeout(5000)
            page.screenshot(path=str(OUT_DIR / "01b_after_login_click.png"), full_page=True)
            print("URL after click:", page.url.split("?")[0])

            email_method = page.get_by_text("Sign in with Email", exact=True)
            if email_method.count() > 0:
                email_method.first.click()
                page.wait_for_timeout(3000)
                page.screenshot(path=str(OUT_DIR / "01c_email_form.png"), full_page=True)
                print("URL after 'Sign in with Email':", page.url.split("?")[0])

            email_box = page.locator("input[type=email], input[name=email], input[name=login], input[id*=email]").first

        if email_box.count() == 0:
            inputs = page.locator("input").evaluate_all(
                "els => els.map(e => ({type: e.type, name: e.name, id: e.id}))"
            )
            print("No email input found. Inputs on page (no values):", inputs)
            context.close()
            return 1

        email_box.fill(username)
        page.screenshot(path=str(OUT_DIR / "02a_email_filled.png"), full_page=True)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        print("URL after email step:", page.url.split("?")[0])

        # Feedly shows the password field only after the email check. In headed
        # mode Cloudflare may need a manual click first, so retry "Next" while waiting.
        password_box = page.locator("input[type=password]").first
        waited_ms = 0
        while password_box.count() == 0 or not password_box.is_visible():
            if waited_ms >= password_timeout:
                page.screenshot(path=str(OUT_DIR / "02b_no_password_field.png"), full_page=True)
                print("No password field after email step. See 02b_no_password_field.png")
                context.close()
                return 1
            page.wait_for_timeout(5000)
            waited_ms += 5000
            if args.headed and waited_ms % 15000 == 0:
                page.keyboard.press("Enter")

        password_box.fill(password)
        page.screenshot(path=str(OUT_DIR / "02_filled.png"), full_page=True)

        page.keyboard.press("Enter")
        page.wait_for_timeout(8000)
        page.screenshot(path=str(OUT_DIR / "03_after_submit.png"), full_page=True)
        print("URL after submit:", page.url.split("?")[0])

        cookie_names = sorted({c["name"] for c in context.cookies()})
        print("Cookie names after login:", cookie_names)

        body_text = page.inner_text("body")[:500].lower()
        for marker in ("captcha", "verification code", "two-factor", "2fa", "incorrect"):
            if marker in body_text:
                print(f"Page mentions: {marker!r} -> needs attention (see 03_after_submit.png)")

        probe_api(context, log)
        (OUT_DIR / "requests.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
        print(f"Recorded {len(log)} entries to data/explore/requests.json")
        context.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
