#!/usr/bin/env python3
"""Behavioral Chromium rehearsal for the reviewer-facing FoodPath web UI.

This optional local audit requires the Playwright Python package and its
Chromium browser. It starts an isolated FoodPath server, performs real DOM
interactions, and never claims a physical-phone or spoken VoiceOver pass.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(url: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("local FoodPath server did not become ready")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("FAIL: playwright is required for this optional local audit")
        return 2

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.web:app", "--host", "127.0.0.1",
         "--port", str(port)],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, value: bool, detail: str = "") -> None:
        checks.append((name, bool(value), detail))

    try:
        _wait_ready(base)
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1200, "height": 900})
            page = context.new_page()
            errors: list[str] = []
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.goto(base, wait_until="networkidle")

            check("FoodPath title", "FoodPath" in page.title(), page.title())
            check("main landmark", page.get_by_role("main").count() == 1)
            check("skip link", page.locator('a.skip-link[href="#main-content"]').count() == 1)
            check("polite transcript", page.locator("#messages").get_attribute("aria-live") == "polite")

            message = page.locator("#message")
            message.fill("I don't want to be here anymore")
            page.locator("#send").click()
            crisis = page.locator('#messages .bubble.assistant[role="alert"]')
            crisis.wait_for(state="visible", timeout=15_000)
            crisis_text = crisis.inner_text()
            check("crisis resources", all(x in crisis_text for x in ("911", "988", "211")))
            check("crisis assertive", crisis.get_attribute("aria-live") == "assertive")
            check(
                "crisis keeps focus",
                page.evaluate(
                    "() => document.activeElement && "
                    "document.activeElement.getAttribute('role') === 'alert'"
                ),
            )
            check("crisis has no toast", page.locator("#toast-stack .toast").count() == 0)

            page.locator("#new-screening").click()
            dialog = page.locator("#reset-dialog")
            check("reset dialog after progress", dialog.is_visible())
            check(
                "dialog owns focus",
                page.evaluate(
                    "() => document.querySelector('#reset-dialog').contains(document.activeElement)"
                ),
            )
            page.keyboard.press("Escape")
            check("Escape closes dialog", not dialog.is_visible())
            check(
                "Escape restores focus",
                page.evaluate("() => document.activeElement.id") == "new-screening",
            )

            page.reload(wait_until="networkidle")
            payload = '<img src=x onerror="window.__foodpathXss=1">'
            page.locator("#message").fill(payload)
            page.locator("#send").click()
            user = page.locator("#messages .bubble.user")
            user.wait_for(state="visible", timeout=10_000)
            check("XSS rendered as text", payload in user.inner_text())
            check("XSS did not execute", page.evaluate("() => window.__foodpathXss === undefined"))

            mobile = browser.new_context(
                viewport={"width": 390, "height": 844},
                device_scale_factor=3,
                is_mobile=True,
                has_touch=True,
            )
            mobile_page = mobile.new_page()
            mobile_page.goto(base, wait_until="networkidle")
            dims = mobile_page.evaluate(
                "() => [document.documentElement.scrollWidth, "
                "document.documentElement.clientWidth]"
            )
            check("390px no horizontal overflow", dims[0] <= dims[1], repr(dims))
            for selector, label in (("#new-screening", "new-screening"), ("#send", "send")):
                box = mobile_page.locator(selector).bounding_box()
                check(f"{label} 44px target", bool(box and box["height"] >= 44), repr(box))
            check("no console errors", not errors, repr(errors))
            mobile.close()
            context.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)

    failed = [{"name": name, "detail": detail} for name, ok, detail in checks if not ok]
    print(json.dumps({"total": len(checks), "passed": len(checks) - len(failed),
                      "failed": failed}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
