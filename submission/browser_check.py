"""Check a real rendered page and report uncaught frontend errors."""

import json
import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    edge = any(
        (Path(os.environ.get(variable, "")) / "Microsoft/Edge/Application/msedge.exe").is_file()
        for variable in ("ProgramFiles", "ProgramFiles(x86)")
    )
    with sync_playwright() as playwright:
        channel = "msedge" if os.name == "nt" and edge else "chromium"
        if channel == "chromium" and not Path(playwright.chromium.executable_path).is_file():
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium", "--no-shell"],
                check=True,
                timeout=120,
            )
        browser = playwright.chromium.launch(channel=channel, headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            response = page.goto(os.environ["BASE_URL"], wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            text = page.locator("body").inner_text(timeout=10000).strip()
            report = {"status": response.status if response else 0, "visible_text": text[:1000], "errors": errors}
            print(json.dumps(report, ensure_ascii=False), flush=True)
            assert response and 200 <= response.status < 300, "Browser did not receive a successful page"
            assert not errors, "Uncaught frontend errors: " + "; ".join(errors)
            assert text, "The application renders no visible text"
        finally:
            browser.close()


if __name__ == "__main__":
    main()
