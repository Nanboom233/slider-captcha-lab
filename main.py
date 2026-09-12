# -*- coding: utf-8 -*-
"""slider-captcha-lab 使用示例入口。

在项目根目录运行：
    python main.py

求解入口（核心库在 src/slider_captcha_lab/ 包内）：
  - slider_captcha_lab.slider_cdp_yidun.solve(page)     易盾：embed 模式，传入页面即可
  - slider_captcha_lab.slider_cdp.solve(ctx, page, cdp) 阿里云：需三件套
"""
import asyncio
import sys
from pathlib import Path

# 未安装本包、直接从源码运行时，使 slider_captcha_lab 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from playwright.async_api import async_playwright

from slider_captcha_lab.slider_cdp import install_verdict_hook, solve as solve_aliyun
from slider_captcha_lab.slider_cdp_yidun import solve as solve_yidun


async def demo_yidun(url="https://your-site-with-captcha.com"):
    """易盾滑块求解：按下按钮触发拼图懒加载 → YOLO 识别缺口 → 人形轨迹拖动 → 判定。

    注意：易盾滑块按钮(.yidun_slider)需先按下才会加载拼图(.yidun_bg-img)。
    solve() 内部已处理此流程，传入页面即可。
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context()
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        passed, meta = await solve_yidun(page)
        print(f"结果: {'通过' if passed else '未过'} meta={meta}")
        await browser.close()


async def demo_aliyun(url="https://your-site-with-captcha.com"):
    """阿里云滑块求解模板：需先在页面上触发滑块弹出，再调用 solve()。

    换站点时先用 tools/calib_cdp.py 标定 A_CDP/B_CDP，回填 src/slider_captcha_lab/slider_cdp.py 顶部。
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        await install_verdict_hook(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # ↓ 在页面触发滑块(点击按钮/提交表单...)，随后调用：
        # passed, meta = await solve_aliyun(ctx, page, cdp)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(demo_yidun())
