"""Browser end-to-end run of the slide workspace against a live server and the real claude CLI.

    S=$(mktemp -d); printf 'model = "haiku"\nlibrary = "%s/lib"\n' $S > $S/config.toml
    uv run python tests/make_pdfs.py $S/attention.pdf
    GLOSS_CONFIG=$S/config.toml GLOSS_HOME=$S/home uv run gloss --port 8798 &
    uv run --no-project --with playwright python -I scripts/e2e_browser.py $S

Uses the installed Google Chrome (headless). Screenshots go to $S/shots/.
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

S = Path(sys.argv[1]); URL = "http://localhost:8798/"
shots = S / "shots"
shots.mkdir(exist_ok=True)
log = []
def step(msg): print(f"[{time.strftime('%X')}] {msg}", flush=True)

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    pg = ctx.new_page()
    pg.on("console", lambda m: m.type in ("error", "warning") and log.append(f"console.{m.type}: {m.text}"))
    pg.on("pageerror", lambda e: log.append(f"pageerror: {e}"))

    pg.goto(URL); pg.wait_for_selector("text=导入课件")
    pg.screenshot(path=shots / "01-home.png")
    step("home ok; importing")
    with pg.expect_file_chooser() as fc:
        pg.click(".drop")
    fc.value.set_files(str(S / "attention.pdf"))
    pg.wait_for_selector(".grid .thumb", timeout=20000)  # shown while the mind map is generated
    step("tree generating; waiting for mind map")
    pg.wait_for_selector(".react-flow .snode", timeout=180000)
    pg.wait_for_timeout(500)
    pg.screenshot(path=shots / "02-map.png")
    step(f"map: {pg.locator('.snode').count()} topics, {pg.locator('.react-flow .thumb').count()} thumbs")
    pg.locator(".snode").first.click(); pg.wait_for_selector(".react-flow .more")
    pg.locator(".react-flow .more").click(); pg.wait_for_selector(".react-flow .more", state="detached")
    step("collapse / expand ok")

    pg.locator(".react-flow .thumb[aria-label^='第 2 页']").click()
    pg.wait_for_selector(".stage img")
    pg.screenshot(path=shots / "03-slide-loading.png")
    step("slide 2 open; waiting for explanation")
    pg.wait_for_selector(".explain-body .md:not(.streaming)", timeout=120000)
    pg.screenshot(path=shots / "04-explained.png")
    step("explanation done: " + pg.locator(".explain-body").inner_text()[:80].replace("\n", " "))

    # drag-select the formula line
    box = pg.locator(".overlay").bounding_box()
    x0, y0 = box["x"] + box["width"] * 0.04, box["y"] + box["height"] * 0.27
    x1, y1 = box["x"] + box["width"] * 0.62, box["y"] + box["height"] * 0.52
    pg.mouse.move(x0, y0); pg.mouse.down(); pg.mouse.move(x1, y1, steps=8); pg.mouse.up()
    pg.wait_for_selector(".composer textarea")
    pg.screenshot(path=shots / "05-composer.png")
    pg.fill(".composer textarea", "为什么要除以 sqrt(d)？两句话。")
    pg.keyboard.press("Enter")
    pg.wait_for_selector(".cbox")
    pg.wait_for_selector(".pin.busy", timeout=5000)
    pg.screenshot(path=shots / "06-asking.png")
    step("asked; waiting for answer")
    pg.wait_for_selector(".cbox .ma:not(.streaming)", timeout=120000)
    pg.wait_for_function("!document.querySelector('.pin.busy')", timeout=30000)
    pg.screenshot(path=shots / "07-answered.png")
    step("answer: " + pg.locator(".cbox .ma").first.inner_text()[:80].replace("\n", " "))

    # follow-up
    pg.fill(".cfoot input", "不除会怎样？一句话。")
    pg.keyboard.press("Enter")
    pg.wait_for_function("document.querySelectorAll('.cbox .ma:not(.streaming)').length >= 2", timeout=120000)
    step("follow-up answered")
    # clip to notes
    pg.locator(".linkbtn", has_text="摘到笔记").first.click()
    pg.wait_for_function("document.querySelector('.notes').value.includes('【问题 1】')")
    pg.wait_for_selector("text=已保存", timeout=5000)
    step("clipped to notes")
    # minimize -> marker -> reopen
    pg.click("[aria-label='缩小这个问题']")
    pg.wait_for_selector(".marker")
    pg.screenshot(path=shots / "08-minimized.png")
    pg.click(".marker"); pg.wait_for_selector(".cbox"); pg.click("[aria-label='缩小这个问题']")
    step("minimize/reopen ok")
    # drawer
    pg.click("text=后台 session"); pg.wait_for_selector(".drawer.open .log.user .tag")
    pg.wait_for_timeout(300)
    pg.screenshot(path=shots / "09-drawer.png")
    step("drawer tags: " + " | ".join(pg.locator(".drawer .tag").all_inner_texts()))
    pg.click("[aria-label='关闭 session 日志']")
    # layout: side mode, then drag splitter
    pg.click("summary:has-text('布局')"); pg.click(".seg button:has-text('左右')")
    pg.mouse.click(5, 880)
    pg.wait_for_timeout(300)
    pg.screenshot(path=shots / "10-side.png")
    sp = pg.locator(".split.v").first.bounding_box()
    pg.mouse.move(sp["x"] + 6, sp["y"] + sp["height"] / 2); pg.mouse.down(); pg.mouse.move(sp["x"] - 200, sp["y"] + sp["height"] / 2, steps=5); pg.mouse.up()
    pg.wait_for_timeout(600)
    step("layout changed; reloading to check persistence")
    pg.reload(); pg.wait_for_selector(".stage img"); pg.wait_for_timeout(800)
    assert pg.locator(".split.v").count() >= 1, "side layout not persisted"
    assert "【问题 1】" in pg.locator(".notes").input_value(), "note not persisted"
    assert pg.locator(".marker").count() == 1, "minimized thread not persisted"
    pg.screenshot(path=shots / "11-after-reload.png")
    step("persistence ok")
    # keyboard nav
    count = "document.querySelector('.count').textContent.trim().startsWith"
    pg.keyboard.press("ArrowRight"); pg.wait_for_function(f"{count}('3 /')")
    pg.keyboard.press("ArrowLeft"); pg.wait_for_function(f"{count}('2 /')")
    step("keyboard nav ok")
    # overview badge
    pg.click("text=导图"); pg.wait_for_selector(".react-flow .badge")
    pg.screenshot(path=shots / "12-overview-badges.png")
    step("overview badge ok")

    # dark + narrow
    d = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", device_scale_factor=2)
    q = d.new_page(); q.on("pageerror", lambda e: log.append(f"narrow pageerror: {e}"))
    q.goto(URL + "#" + pg.url.split("#")[1] + "/p/2")
    q.wait_for_selector(".stage img"); q.wait_for_timeout(800)
    assert q.locator(".marker").count() == 1
    q.screenshot(path=shots / "13-narrow-dark.png", full_page=True)
    q.click(".marker"); q.wait_for_selector(".cbox"); q.wait_for_timeout(300)
    q.screenshot(path=shots / "14-narrow-chat.png")
    step("narrow/dark ok")
    b.close()

print("\n".join(log) or "no console errors")
