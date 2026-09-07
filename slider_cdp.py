# -*- coding: utf-8 -*-
"""slider_cdp.py — Aliyun 滑块验证码 CDP 自动求解器(独立库)。

只做滑块这一件事:
  1. YOLO 识别缺口位置(支持任何 aliyunCaptcha 实例)
  2. 二次模型反解鼠标行程(piece.left = A*m + B*m²)
  3. 人形轨迹生成(慢逼近+谷底+末端甩鞭, 见 human_track.py)
  4. CDP 事件注入(不碰真实光标)
  5. 判定码读取(-verify 响应里的 VerifyCode)

依赖: playwright, opencv, numpy, captcha_recognizer(YOLO)
"""
import asyncio
import base64
import random
import time

import cv2
import numpy as np
from playwright.async_api import async_playwright

from human_track import generate_drag

# ---- CDP 场景二次响应模型(标定方法见 calib_cdp.py) ----
# piece.left = A_CDP * m + B_CDP * m^2
# m = 鼠标总位移(px), piece.left = 拼图块最终 left(CSS px)
# 不同站点/分辨率需用 calib_cdp.py 重新标定这两个系数
A_CDP, B_CDP = 0.0769, 0.003552

# ---- 判定码(阿里云滑块) ----
# T001=通过, F001=风控拦截(轨迹/环境被判定机器), F015=位置误差
VERDICTS = []


async def read_piece(page):
    """读拼图块当前位置(用于落点误差测量)。"""
    return await page.evaluate("""() => {
        const p = document.getElementById('aliyunCaptcha-puzzle');
        const m = document.querySelector('.aliyunCaptcha-show');
        return {left: p ? (parseFloat(p.style.left) || 0) : null, modal: !!m};
    }""")


async def install_verdict_hook(page, verdicts=None):
    """拦截 -verify 响应,提取判定码。实测格式: {"Result": {"VerifyCode": "T001"}}"""
    vs = verdicts if verdicts is not None else VERDICTS

    async def on_response(resp):
        try:
            if "-verify." in resp.url and "json" in resp.headers.get("content-type", ""):
                data = await resp.json()
                code = ""
                res = data.get("Result")
                if isinstance(res, dict):
                    code = res.get("VerifyCode") or ""
                elif isinstance(res, str):
                    code = res
                if not code:
                    code = data.get("result") or ""
                code = code.upper()
                if code:
                    vs.append((code, code == "T001"))
                    print(f"    [判定] {code} ({'通过' if code == 'T001' else '未过'})")
        except Exception:
            pass

    page.on("response", on_response)


async def get_gap_target(ctx, page):
    """YOLO 识别缺口,返回 (target_left, conf)。

    target_left: 拼图块需要移动到的 left 值(CSS px,页面坐标)
    conf: YOLO 置信度
    """
    bg_url = await page.evaluate(
        "() => { const i = document.getElementById('aliyunCaptcha-img');"
        "        return i ? (i.currentSrc || i.src) : ''; }")
    piece_url = await page.evaluate(
        "() => { const i = document.getElementById('aliyunCaptcha-puzzle');"
        "        return i ? (i.currentSrc || i.src) : ''; }")

    async def grab(u):
        if u.startswith("data:"):
            return base64.b64decode(u.split(",", 1)[1])
        return await (await ctx.request.get(u)).body()

    bg = cv2.imdecode(np.frombuffer(await grab(bg_url), np.uint8), cv2.IMREAD_UNCHANGED)
    piece = cv2.imdecode(np.frombuffer(await grab(piece_url), np.uint8), cv2.IMREAD_UNCHANGED)
    if bg.ndim == 3 and bg.shape[2] == 4:
        bg = bg[:, :, :3]

    from captcha_recognizer.slider import Slider
    global _yolo
    try:
        _yolo
    except NameError:
        _yolo = Slider()
    r = _yolo.identify(bg[:, :, ::-1])  # BGR->RGB
    box, conf = r[0], float(r[1])
    box_left = float(box[0])

    # 图像自然尺寸 -> 页面显示尺寸的换算
    natural = await page.evaluate(
        "() => { const i = document.getElementById('aliyunCaptcha-img');"
        "        return i ? i.naturalWidth : 296; }")
    bb = await page.locator("#aliyunCaptcha-img").bounding_box()
    scale = bb["width"] / natural

    # 拼图块 alpha 通道的左边缘偏移(块内透明区不算)
    if piece.ndim == 3 and piece.shape[2] == 4:
        alpha = (piece[:, :, 3] > 128).astype(np.uint8)
    else:
        g = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)
        alpha = (g > 30).astype(np.uint8)
    ys, xs = np.where(alpha > 0)
    ex0 = int(xs.min()) if len(xs) else 0

    target_left = box_left * scale - ex0
    print(f"    YOLO: conf={conf:.3f} target_left={target_left:.1f}")
    return target_left, conf


async def cdp_event(cdp, typ, x, y, buttons):
    params = {
        "type": typ, "x": x, "y": y, "button": "left",
        "buttons": buttons, "pointerType": "mouse",
    }
    if typ in ("mousePressed", "mouseReleased"):
        params["clickCount"] = 1
    await cdp.send("Input.dispatchMouseEvent", params)


async def solve(ctx, page, cdp, meta=None):
    """对当前弹出的 aliyunCaptcha 滑块执行一次完整求解。

    返回 (passed, meta)。meta 记录全部实验量(轨迹指标/落点/判定码),
    供离线分析(captcha_runs.jsonl 模式)。

    用法:
        ctx, page = await browser.new_context(), await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        await install_verdict_hook(page)
        # ... 页面操作触发滑块弹出 ...
        passed, meta = await solve(ctx, page, cdp)
    """
    meta = dict(meta or {})
    verdict_start = len(VERDICTS)

    # 1) 识别
    target_left, conf = await get_gap_target(ctx, page)
    if conf < 0.3:
        meta.update({"target_left": target_left, "yolo_conf": conf,
                     "passed": False, "fail_reason": "low_conf"})
        return False, meta

    sbb = await page.locator("#aliyunCaptcha-sliding-slider").first.bounding_box()
    sx = sbb["x"] + sbb["width"] / 2
    sy = sbb["y"] + sbb["height"] / 2

    # 2) 接近动作(页面内移动到滑块附近,不碰真实光标)
    await page.mouse.move(sx - random.uniform(60, 120), sy + random.uniform(-30, 30),
                          steps=random.randint(8, 14))
    await page.wait_for_timeout(random.randint(200, 400))
    await page.mouse.move(sx, sy, steps=5)
    await page.wait_for_timeout(random.randint(250, 500))

    # 3) 二次模型反解鼠标总行程 + 落点故意偏(真人"差一点"手感)
    disc = A_CDP ** 2 + 4 * B_CDP * target_left
    D = (-A_CDP + float(np.sqrt(disc))) / (2 * B_CDP)
    bias_px = 0.0
    err_piece = 0.0
    if random.random() < 0.85:
        # 落点只偏正(实测: 偏负 F001 率 71% vs 偏正 54%)
        err_piece = random.uniform(2.0, 4.0)
        slope_end = A_CDP + 2 * B_CDP * D
        bias_px = err_piece / slope_end
    print(f"    二次换算: target={target_left:.1f} => D={D:.1f} px"
          f"{f' (故意偏 {bias_px:+.1f})' if bias_px else ''}")

    # 4) 人形轨迹 + 注入
    track = generate_drag(D, bias_px=bias_px)
    dur_ms = sum(t[2] for t in track)
    print(f"    轨迹: {len(track)} 事件, {dur_ms:.0f} ms")

    await cdp_event(cdp, "mousePressed", sx, sy, 1)
    await page.wait_for_timeout(random.randint(50, 110))

    t0 = time.perf_counter()
    sched = 0.0
    cum = 0.0
    cum_dy = 0.0
    # y 必须累计(track 的 dy 是路径增量,直接 sy+dy 会丢掉 y 形态)
    for dx, dy, dt_ms in track:
        sched += dt_ms
        cum += dx
        cum_dy += dy
        await cdp_event(cdp, "mouseMoved", sx + cum, sy + cum_dy, 1)
        el = (time.perf_counter() - t0) * 1000
        if sched > el:
            await asyncio.sleep((sched - el) / 1000)

    st_now = await read_piece(page)
    final_left = st_now["left"] if st_now["left"] is not None else 0.0
    err = target_left - final_left
    print(f"    落点: err={err:+.1f} (不回正,直接松)")

    await page.wait_for_timeout(random.randint(10, 40))
    await cdp_event(cdp, "mouseReleased", sx + cum, sy + cum_dy, 0)

    # 5) 等判定(-verify 响应,由 install_verdict_hook 捕获)
    await page.wait_for_timeout(9000)
    new_verdicts = VERDICTS[verdict_start:]
    if new_verdicts:
        verdict, passed = new_verdicts[-1]
    else:
        st = await read_piece(page)
        passed = not st["modal"]
        verdict = "T001" if passed else "?"

    meta.update({
        "target_left": target_left, "yolo_conf": conf,
        "mouse_dx": D, "bias_piece": err_piece, "bias_mouse": bias_px,
        "duration_ms": dur_ms, "event_count": len(track),
        "actual_error": -err, "verdict": verdict, "passed": bool(passed),
    })
    return bool(passed), meta


# ---------------- 演示: 打开任意图床页触发滑块并求解 ----------------
async def demo(url="https://example.com/"):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        cdp = await ctx.new_cdp_session(page)
        await install_verdict_hook(page)
        # 在这里触发你的滑块(点击按钮/提交表单...)
        # passed, meta = await solve(ctx, page, cdp)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(demo())
