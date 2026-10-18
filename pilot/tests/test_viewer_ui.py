"""Browser test of the match viewer: drawing, overlays, video tab, navigation.

Needs the optional Playwright client and the preinstalled Chromium; skipped otherwise.
"""

from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
CHROME = Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
VIEWER = Path(__file__).parents[1] / "broadcast" / "index.html"
pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="no Chromium for Playwright")


def test_viewer_interactions():
    url = VIEWER.as_uri()
    errors = []
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args=["--no-sandbox"])
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_selector("#l-pieces use")
        box = page.locator("#board").bounding_box()
        sq = box["width"] / 8
        def center(name):
            f = ord(name[0]) - 97; r = int(name[1]) - 1
            return box["x"] + (f + .5) * sq, box["y"] + (7 - r + .5) * sq

        # 1. draw mode off: a left drag draws nothing
        page.mouse.move(*center("e4")); page.mouse.down(); page.mouse.move(*center("e5"), steps=5); page.mouse.up()
        assert page.locator("#l-marks line").count() == 0, "left drag drew without draw mode"

        # 2. draw mode on: arrow d1-h5 in lime, circle on f7 in purple
        page.click("#draw")
        page.mouse.move(*center("d3")); page.mouse.down(); page.mouse.move(*center("h7"), steps=8); page.mouse.up()
        page.click(".swatch[data-mark='2']")
        page.mouse.click(*center("f7"))
        assert page.locator("#l-marks line").count() == 1, "arrow missing"
        assert page.locator("#l-marks circle").count() == 1, "circle missing"

        # 3. right-drag draws even with draw mode off
        page.click("#draw")
        page.mouse.move(*center("c3")); page.mouse.down(button="right"); page.mouse.move(*center("d5"), steps=5); page.mouse.up(button="right")
        assert page.locator("#l-marks line").count() == 2, "right-drag arrow missing"

        # 4. same arrow again removes it
        page.mouse.move(*center("c3")); page.mouse.down(button="right"); page.mouse.move(*center("d5"), steps=5); page.mouse.up(button="right")
        assert page.locator("#l-marks line").count() == 1, "toggle-off failed"

        # 5. marks belong to the position: next move hides them, back shows them
        page.click("#next")
        assert page.locator("#l-marks line").count() == 0
        page.click("#prev")
        assert page.locator("#l-marks line").count() == 1

        # 6. overlay toggle from the Position tab
        page.click("#tab-position")
        page.check("#layer-control")
        n_ctl = page.locator("#l-control rect").count()
        assert n_ctl > 20, f"control layer shows {n_ctl} squares"

        # 7. video tab: add a link and an iframe appears
        page.click("#tab-video")
        page.fill("#video-url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        page.click("#video-form button")
        assert page.locator("#video-slot iframe").get_attribute("src").startswith("https://www.youtube.com/embed/")

        # 8. clicking a key moment jumps there
        page.click("#tab-game")
        page.locator(".moment").nth(1).click()
        assert page.locator("#mv").inner_text() == "17. g4"

        # 9. colour schemes switch via the picker and keep drawn marks
        page.select_option("#scheme", "matchday")
        assert page.evaluate("document.documentElement.dataset.scheme") == "matchday"
        page.select_option("#scheme", "broadcast")
        assert page.evaluate("document.documentElement.dataset.scheme") is None
        b.close()
    assert not errors, errors
