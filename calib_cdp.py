# -*- coding: utf-8 -*-
"""calib_cdp.py - CDP 场景 K 值标定探针。

只做一件事: CDP 匀速拖 100px,读 piece.left,算 K = piece.left / 100。
不注入完整验证答案,拿数据就停。输出建议的 K 回填值。
"""
import asyncio
import base64
import json
import os
import random
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

os.environ["PROXY_MODE"] = "off"
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)

sys.path.insert(0, ".")
from playwright.async_api import async_playwright
from common.browser import inject_stealth

PROXY = {"server": "http://127.0.0.1:PROXY_PORT"}
EMAIL = os.environ.get("SIGNUP_EMAIL", "probecheck8271@MAIL_DOMAIN")


async def read_piece(page):
    return await page.evaluate("""() => {
        const p = document.getElementById('aliyunCaptcha-puzzle');
        const m = document.querySelector('.aliyunCaptcha-show');
        return {left: p ? (parseFloat(p.style.left) || 0) : null, modal: !!m};
    }""")


async def cdp_event(cdp, typ, x, y, buttons):
    params = {
        "type": typ, "x": x, "y": y, "button": "left",
        "buttons": buttons, "pointerType": "mouse",
    }
    if typ in ("mousePressed", "mouseReleased"):
        params["clickCount"] = 1
    await cdp.send("Input.dispatchMouseEvent", params)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, proxy=PROXY)
        ctx = await browser.new_context(viewport={"width": 960, "height": 600},
                                        locale="en-US")
        page = await ctx.new_page()
        await inject_stealth(ctx, page)
        cdp = await ctx.new_cdp_session(page)

        print("[1] 填表触发滑块 ...")
        await page.goto("https://DEMO_SITE/auth?action=signup", timeout=60000,
                        wait_until="domcontentloaded")
        try:
            await page.locator("#splash-screen").first.wait_for(state="hidden", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(2500)
        await page.locator('input[name="username"]').first.fill("probe_user", timeout=15000)
        await page.locator('input[name="email"]').first.fill(EMAIL)
        await page.locator('input[name="password"]').first.fill("ProbeTest123!x")
        await page.locator('input[name="checkPassword"]').first.fill("ProbeTest123!x")
        box = page.locator('span[role="checkbox"]').first
        if await box.get_attribute("aria-checked") != "true":
            await box.click()
        await page.wait_for_timeout(1000)
        await page.locator('button[type="submit"]:not([disabled])').first.click(timeout=10000)
        await page.locator("#aliyunCaptcha-sliding-slider").first.wait_for(
            state="visible", timeout=12000)
        await page.wait_for_timeout(1800)

        sbb = await page.locator("#aliyunCaptcha-sliding-slider").first.bounding_box()
        sx = sbb["x"] + sbb["width"] / 2
        sy = sbb["y"] + sbb["height"] / 2
        print(f"    滑块中心(CSS): ({sx:.1f},{sy:.1f})")

        # 接近 + 按下
        await page.mouse.move(sx - 80, sy + 15, steps=10)
        await page.wait_for_timeout(300)
        await page.mouse.move(sx, sy, steps=5)
        await page.wait_for_timeout(400)
        await cdp_event(cdp, "mousePressed", sx, sy, 1)
        await page.wait_for_timeout(120)

        # 匀速拖 100px, 20 步, 每步 50ms(总共 1s,慢速排除 transition 干扰)
        DRAG = 100.0
        N = 20
        print(f"[2] CDP 匀速拖 {DRAG}px ({N}步x50ms) ...")
        lefts = []
        for i in range(1, N + 1):
            x = sx + DRAG * i / N
            await cdp_event(cdp, "mouseMoved", x, sy + random.uniform(-0.3, 0.3), 1)
            await page.wait_for_timeout(50)
            st = await read_piece(page)
            if st["left"] is not None:
                lefts.append((i * DRAG / N, st["left"]))

        await page.wait_for_timeout(300)
        st = await read_piece(page)
        print(f"[3] 最终: 鼠标位移={DRAG:.0f}px piece.left={st['left']:.2f}")
        if len(lefts) >= 2:
            # 用中段点拟合 K(起点附近可能有死区)
            mid = [pt for pt in lefts if 30 <= pt[0] <= 90]
            if len(mid) >= 2:
                (m1, p1), (m2, p2) = mid[0], mid[-1]
                k_fit = (p2 - p1) / (m2 - m1)
                print(f"    中段拟合 K = {k_fit:.4f}")
            k_total = st["left"] / DRAG
            print(f"    全程 K = {k_total:.4f}")
            print(f"\n==> 建议回填 attempt_cdp: K = {k_total:.3f}")
        print("\n[4] 采样点(鼠标x, piece.left):")
        for m, pl in lefts[::2]:
            print(f"    {m:6.1f}  {pl:7.2f}")

        await asyncio.sleep(3)
        await browser.close()
        return 0


if __name__ == "__main__":
    asyncio.run(main())
