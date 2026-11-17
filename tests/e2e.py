"""End-to-end smoke test: installs packs from the Subjects page (served from data/packs) and walks every screen.
Screenshots go to tests/out/ (or the dir given as first arg). Run: nix run .#test"""
import subprocess, time, sys
from playwright.sync_api import sync_playwright
S = sys.argv[1] if len(sys.argv) > 1 else "tests/out"
import os; os.makedirs(S, exist_ok=True)
srv = subprocess.Popen([sys.executable, "pipeline/serve.py", "8765"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
errors = []
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        pg.on("console", lambda m: m.type in ("error", "warning") and errors.append(m.text))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto("http://localhost:8765/")
        pg.wait_for_selector("text=Getting started")
        pg.screenshot(path=f"{S}/0-start.png", full_page=True)
        # hosted without packs (GitHub Pages): no catalog, so the page leads with zip import
        pg.route("**/packs/index.json", lambda r: r.fulfill(status=404, body="not found"))
        pg.click("text=Add subjects")
        pg.wait_for_selector("text=Import your subject files")
        pg.screenshot(path=f"{S}/0-hosted.png", full_page=True)
        pg.set_input_files("input[type=file]", "data/packs/9709.zip")
        pg.wait_for_selector("#toast:has-text('Mathematics: 2045 questions ready')", timeout=120000)
        pg.unroute("**/packs/index.json")
        errors.clear()  # the faked 404s above are expected
        print("hosted import: ok")
        # served by `nix run .#serve`: install from the catalog
        pg.reload()
        pg.wait_for_selector("button:has-text('Install')")
        print("catalog:", pg.locator(".grid h2").all_inner_texts())
        pg.wait_for_selector("text=Convert from PapaCambridge")  # laptop server offers link conversion
        for name in ("Physics",):
            pg.click(f"section.card:has(h2:has-text('{name}')) button:has-text('Install')")
            pg.wait_for_selector(f"section.card:has(h2:has-text('{name}')) .tag:has-text('Installed')", timeout=120000)
        pg.screenshot(path=f"{S}/0-subjects.png", full_page=True)
        # pretend a newer Maths build is in the catalog: it should offer an update
        def newer(route):
            cat = route.fetch().json()
            for e in cat["subjects"]:
                if e["code"] == "9709": e["built"] = "9999-01-01"
            route.fulfill(json=cat)
        pg.route("**/packs/index.json", newer)
        pg.reload()
        maths = "section.card:has(h2:has-text('Mathematics'))"
        pg.click(f"{maths} button:has-text('Update')")
        pg.wait_for_selector("#toast:has-text('Mathematics: 2045 questions ready')", timeout=120000)
        pg.unroute("**/packs/index.json")
        print("update path: ok")
        pg.goto("http://localhost:8765/#/")
        pg.wait_for_selector("text=Mathematics")
        pg.wait_for_selector("text=Physics")
        pg.screenshot(path=f"{S}/1-home.png")
        # practice physics, MCQ only
        pg.click("text=Practice >> nth=1")
        pg.wait_for_selector("text=Physics practice")
        pg.click("text=Paper 2 · AS Structured")
        pg.click("text=Paper 4 · A Level Structured")
        print("counter:", pg.inner_text(".actions .muted"))
        pg.screenshot(path=f"{S}/2-setup.png", full_page=True)
        pg.click("button:has-text('Start') >> nth=0")
        pg.wait_for_selector(".opts")
        pg.wait_for_function("document.querySelector('.qimg').complete && document.querySelector('.qimg').naturalWidth > 0")
        pg.click(".opt >> nth=1")
        pg.screenshot(path=f"{S}/3-mcq.png", full_page=True)
        pg.click("text=Next")
        pg.wait_for_selector(".opts")
        pg.click("text=End")
        pg.wait_for_selector("text=Session complete")
        pg.screenshot(path=f"{S}/4-summary.png", full_page=True)
        # full maths paper, structured self-mark
        pg.goto("http://localhost:8765/#/practice/9709")
        pg.click("text=Start paper")
        pg.wait_for_selector("textarea")
        pg.fill("textarea", "x = 2")
        pg.click("text=Show mark scheme")
        pg.wait_for_selector(".scores")
        pg.wait_for_function("[...document.querySelectorAll('.qimg')].every(i => i.complete && i.naturalWidth > 0)")
        pg.click(".score >> nth=2")
        pg.screenshot(path=f"{S}/5-selfmark.png", full_page=True)
        pg.click("text=Save & next")
        pg.wait_for_selector("text=Q2")
        pg.goto("http://localhost:8765/#/progress")
        pg.wait_for_selector("h3:has-text(\"By paper\")")
        pg.screenshot(path=f"{S}/6-progress.png", full_page=True)
        pg.goto("http://localhost:8765/#/browse/9702")
        pg.click("details summary >> nth=0")
        pg.wait_for_selector(".qcell")
        pg.screenshot(path=f"{S}/7-browse.png", full_page=True)
        pg.goto("http://localhost:8765/#/settings")
        pg.wait_for_selector("text=AI marking")
        pg.wait_for_function("document.querySelectorAll('#models option').length > 0", timeout=20000)
        print("models in list:", pg.eval_on_selector_all("#models option", "o => o.length"))
        pg.screenshot(path=f"{S}/8-settings.png", full_page=True)
        print("sw:", pg.evaluate("navigator.serviceWorker.getRegistrations().then(r => r.length)"))
        b.close()
except Exception:
    try: pg.screenshot(path=f"{S}/failure.png", full_page=True)
    except Exception: pass
    raise
finally:
    print("ERRORS:", errors)
    srv.terminate()
