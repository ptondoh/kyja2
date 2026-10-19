import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests
from playwright.sync_api import sync_playwright

from tests.conftest import make_admin  # noqa: F401  (réutilisé par les tests e2e)

ROOT = Path(__file__).resolve().parents[2]
SHOTS = Path(os.environ.get("E2E_SHOTS", ROOT / "tmp_uploads" / "shots"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url():
    port = _free_port()
    proc = subprocess.Popen(  # nosec B603
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if requests.get(f"{url}/health", timeout=1).status_code == 200:
                break
        except requests.RequestException:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("le serveur de test n'a pas démarré")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        # E2E_HEADED=1 : fenêtre visible ; E2E_SLOWMO=500 : ralentit chaque action (en ms).
        b = p.chromium.launch(
            channel="msedge",
            headless=os.environ.get("E2E_HEADED") != "1",
            slow_mo=int(os.environ.get("E2E_SLOWMO", "0")),
        )
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    ctx = browser.new_context(base_url=base_url, viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    pg.console_errors = []
    pg.on("console", lambda m: pg.console_errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.console_errors.append(str(e)))
    SHOTS.mkdir(parents=True, exist_ok=True)
    yield pg
    ctx.close()


@pytest.fixture
def shot(page):
    def _shot(name: str, full_page: bool = True):
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full_page)

    return _shot


def pick_day(page, iso_day: str) -> None:
    """Clique un jour du calendrier mensuel (avance de mois si besoin) ; les heures se chargent ensuite."""
    for _ in range(4):
        day = page.locator(f"label.day:has(input[value='{iso_day}'])")
        if day.count():
            day.click()
            return
        page.click("button[aria-label='Mois suivant']")
        page.wait_for_timeout(300)
    raise AssertionError(f"jour {iso_day} introuvable dans le calendrier")


def choose_hairdresser(page, choice_locator) -> None:
    """Choisit une coiffeuse puis attend le rechargement du calendrier (ses jours disponibles changent)."""
    with page.expect_response(lambda r: "/bookings/calendar" in r.url and r.status == 200):
        choice_locator.click()
