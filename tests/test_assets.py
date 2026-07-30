"""Every asset a page references must actually resolve when served.

The display lives at ``/`` but the controller lives at ``/play`` — a route with
no trailing slash — so a relative ``play.js`` resolves to ``/play.js`` and 404s,
leaving a blank white screen on every phone. That class of bug is invisible in
the source and fatal in the room, so it gets a test.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient

from server.app import app

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

ASSET_REF = re.compile(r'(?:href|src)="([^"]+)"')
JS_IMPORT = re.compile(r'^\s*(?:import|export)[^"\']*from\s*["\']([^"\']+)["\']', re.MULTILINE)


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("page", ["/", "/play"])
def test_page_assets_resolve(client: TestClient, page: str):
    html = client.get(page).text
    refs = [ref for ref in ASSET_REF.findall(html) if not ref.startswith(("data:", "#"))]
    assert refs, f"{page} references no assets at all"
    for ref in refs:
        resolved = urljoin(f"http://testserver{page}", ref).replace("http://testserver", "")
        response = client.get(resolved)
        assert response.status_code == 200, (
            f"{page} references {ref}, which resolves to {resolved} ({response.status_code})"
        )


@pytest.mark.parametrize("page", ["/", "/play"])
def test_pages_have_no_external_dependencies(client: TestClient, page: str):
    """Strict offline requirement (§18.1): no CDN, no remote fonts, no analytics."""

    html = client.get(page).text
    for ref in ASSET_REF.findall(html):
        assert not ref.startswith(("http://", "https://", "//")), f"{page} loads {ref} remotely"


def test_module_imports_point_at_real_files():
    """Every ES module import must exist on disk, with its extension."""

    for path in sorted(STATIC_DIR.rglob("*.js")):
        for specifier in JS_IMPORT.findall(path.read_text()):
            assert specifier.startswith("."), f"{path.name} uses a bare import: {specifier}"
            target = (path.parent / specifier).resolve()
            assert target.exists(), f"{path.name} imports missing module {specifier}"


def test_static_files_are_served_from_the_mount(client: TestClient):
    for asset in ["shared/theme.css", "shared/ws.js", "display/display.js", "play/play.js"]:
        assert client.get(f"/static/{asset}").status_code == 200, asset


ELEMENT_ID = re.compile(r'id="([^"]+)"')
ID_LOOKUP = re.compile(r'(?:\$|byId|getElementById)\(\s*["\']([^"\']+)["\']\s*\)')

SURFACES = {
    "display": (
        "display/index.html",
        ["display/display.js", "display/hud.js", "display/ceremony.js"],
    ),
    "play": ("play/index.html", ["play/play.js"]),
}


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_every_dom_lookup_has_matching_markup(surface: str):
    """A typo'd element id is a blank screen at boot, not a caught exception."""

    page, scripts = SURFACES[surface]
    available = ELEMENT_ID.findall((STATIC_DIR / page).read_text())
    for script in scripts:
        wanted = ID_LOOKUP.findall((STATIC_DIR / script).read_text())
        missing = sorted(set(wanted) - set(available))
        assert not missing, f"{script} looks up ids absent from {page}: {missing}"
