# -*- coding: utf-8 -*-
"""calib_cdp_yidun.py — 易盾滑块位移响应标定探针。

测「按钮位移 → 拼图块位移」的线性映射: 拼图 left = K * 按钮 left + B。

易盾 embed 模式实测(见 debug 记录):
  - 滑块按钮是 .yidun_slider(不是 .yidun_control, 后者是 345px 轨道)
  - 拼图 .yidun_bgimg/.yidun_jigsaw 是懒加载: 必须按下按钮才加载
  - 按钮 left 精确 1:1 跟随鼠标; 拼图 left = 按钮 left + B(B 为固定偏移)
  - 拼图块用 style.left 定位, 松手失败后按钮复位、拼图重载

流程:
  1. 等 .yidun_slider 可见
  2. page.mouse.down 按下按钮触发拼图懒加载
  3. 等 .yidun_bgimg 加载完成
  4. 分段拖动按钮, 每段等稳定后读按钮 left 与拼图 left
  5. 最小二乘拟合 K、B, 回填 slider_cdp_yidun.py

用法: python calib_cdp_yidun.py
"""
import asyncio
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from playwright.async_api import async_playwright

SEL_BUTTON = "#captcha .yidun_slider"
SEL_BG = "#captcha .yidun_bg-img"   # 缺口背景图 <img>(不是 bgimg 容器)
SEL_JIGSAW = "#captcha .yidun_jigsaw"

TARGET_URL = "https://your-site-with-captcha.com"


async def read_left(page, sel):
    """读元素 style.left(px), 无 left 返回 0。"""
    return await page.evaluate(
        "(sel) => { const el = document.querySelector(sel);"
        " return el ? (parseFloat(el.style.left) || 0) : null; }", sel)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context()
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await ctx.new_page()
        await page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")

        print("[1] 等滑块按钮 ...")
        await page.locator(SEL_BUTTON).first.wait_for(state="visible", timeout=20000)
        await page.wait_for_timeout(1500)
        bb = await page.locator(SEL_BUTTON).first.bounding_box()
        sx = bb["x"] + bb["width"] / 2
        sy = bb["y"] + bb["height"] / 2
        print(f"    按钮中心: ({sx:.1f},{sy:.1f})")

        print("[2] 按按钮触发拼图懒加载 ...")
        await page.mouse.move(sx, sy)
        await page.wait_for_timeout(200)
        await page.mouse.down()
        bgw = 0
        for _ in range(30):
            await page.wait_for_timeout(200)
            bgw = await page.evaluate(
                "() => { const el = document.querySelector('%s');"
                " return el ? el.getBoundingClientRect().width : 0; }" % SEL_BG)
            if bgw and bgw > 0:
                break
        if not bgw:
            print("    !! 背景图始终未加载, 请检查网络/验证码状态")
            await page.mouse.up()
            await browser.close()
            return 1
        print(f"    背景图已加载 (宽 {bgw:.0f}px)")

        print("[3] 分段拖动采样(按钮left, 拼图left) ...")
        # 从 30px 起采样: 前 30px 是拼图块 transition 起步期(未稳定), 会污染拟合
        targets = [30, 50, 70, 90, 110, 130, 150, 170, 190, 210]
        pts = []
        for tx in targets:
            await page.mouse.move(sx + tx, sy)
            await page.wait_for_timeout(600)  # 等 transition/JS 稳定后再读
            bl = await read_left(page, SEL_BUTTON)
            jl = await read_left(page, SEL_JIGSAW)
            if bl is not None:
                pts.append((bl, jl if jl is not None else 0.0))
                print(f"    按钮left={bl:6.1f}  拼图left={jl}")

        await page.mouse.up()

        if len(pts) >= 2:
            xs = [a for a, _ in pts]
            ys = [b for _, b in pts]
            n = len(pts)
            sxs = sum(xs); sys_ = sum(ys)
            sxy = sum(a * b for a, b in zip(xs, ys))
            sxx = sum(a * a for a in xs)
            K = (n * sxy - sxs * sys_) / (n * sxx - sxs * sxs)
            B = (sys_ - K * sxs) / n
            print(f"\n==> 拟合: 拼图left = {K:.4f} * 按钮left + {B:.2f}")
            print(f"==> 建议回填 slider_cdp_yidun.py: K_CDP = {K:.4f}, B_CDP = {B:.2f}")

        await asyncio.sleep(1)
        await browser.close()
        return 0


if __name__ == "__main__":
    asyncio.run(main())
