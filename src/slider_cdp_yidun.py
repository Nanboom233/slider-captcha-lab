# -*- coding: utf-8 -*-
"""slider_cdp_yidun.py — 网易易盾(Netease Yidun)滑块验证码自动求解器。

基于 slider_cdp.py 的通用三层(识别/轨迹/注入)改造, 针对易盾 embed 模式。

易盾实测关键(与阿里云差异):
  1. 滑块按钮是 .yidun_slider(40px), 不是 .yidun_control(345px 轨道)
  2. 拼图 .yidun_bgimg/.yidun_jigsaw 懒加载: 必须 mousedown 按钮后才加载
  3. 按钮 left 精确 1:1 跟随鼠标; 拼图 left = K_CDP * 按钮left + B_CDP
  4. 拼图块用 style.left 定位; 松手失败后按钮复位、拼图重载
  5. 验证成功后易盾 SDK 自动填充 <input name="NECaptchaValidate">

初始化(由站点自己的登录脚本调用):
    initNECaptcha({ captchaId: '<你的 captchaId>',
                    element: '#captcha', mode: 'embed',
                    onVerify: function(err, ret){} })

依赖: playwright, opencv, numpy, captcha_recognizer(YOLO)
"""
import asyncio
import base64
import random
import sys
import time

import cv2
import numpy as np

from .human_track import generate_drag

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---- 位移响应模型: 拼图 left = K_CDP * 按钮left + B_CDP ----
# 用 calib_cdp_yidun.py 标定; 默认值来自单次实测(稳态近似), 务必标定后回填。
K_CDP = 1.0
B_CDP = -12.5

# ---- 易盾关键 DOM(embed 模式, 渲染在 #captcha 容器内) ----
SEL_BUTTON = "#captcha .yidun_slider"   # 滑块按钮(可拖动的 40px 方块)
SEL_BG = "#captcha .yidun_bg-img"       # 缺口背景图 <img>(注意: 是 bg-img, 不是 bgimg 容器)
SEL_JIGSAW = "#captcha .yidun_jigsaw"   # 拼图块 <img>


def _mouse_dx_for(piece_target):
    """反解按钮位移: 目标拼图 left -> 按钮需拖动的鼠标距离。"""
    if K_CDP <= 0:
        raise ValueError("K_CDP 必须 > 0，请先用 calib_cdp_yidun.py 标定")
    return (piece_target - B_CDP) / K_CDP


async def _grab_image(page, selector):
    """读图片 URL 并用 Playwright request API 下载(网络层请求, 绕过浏览器 CORS)。"""
    url = await page.evaluate(
        "() => { const img = document.querySelector('%s');"
        " return img ? (img.currentSrc || img.src) : null; }" % selector)
    if not url:
        return None
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    try:
        resp = await page.request.get(url, headers={"Referer": page.url})
        if resp.ok:
            return await resp.body()
    except Exception:
        pass
    return None


async def _decode_image(page, selector):
    buf = await _grab_image(page, selector)
    if not buf:
        return None
    try:
        img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is not None and img.ndim == 3 and img.shape[2] == 4:
            img = img[:, :, :3]
        return img
    except Exception:
        return None


async def read_validate(page):
    """读易盾写入的二次校验数据 NECaptchaValidate。验证通过后非空。"""
    return await page.evaluate(
        """() => {
            const el = document.querySelector("input[name='NECaptchaValidate']");
            return el ? (el.value || '') : '';
        }"""
    )


async def read_left(page, sel):
    """读元素 style.left(px), 无 left 返回 0。"""
    return await page.evaluate(
        "(sel) => { const el = document.querySelector(sel);"
        " return el ? (parseFloat(el.style.left) || 0) : null; }", sel)


async def get_gap_target(page):
    """YOLO 识别缺口, 返回拼图块需要移动到的 CSS left(相对 bg 左边缘)。

    拼图块初始 left=0(贴 bg 左边缘), 目标 left = 缺口在 bg 内的显示坐标。
    """
    bg = await _decode_image(page, SEL_BG)
    if bg is None:
        return None, 0.0

    natural = await page.evaluate(
        "() => { const i = document.querySelector('%s');"
        " return i ? i.naturalWidth : null; }" % SEL_BG)
    bb = await page.locator(SEL_BG).first.bounding_box()
    if natural is None or bb is None or natural == 0:
        return None, 0.0
    scale = bb["width"] / natural

    from captcha_recognizer.slider import Slider

    global _yolo
    try:
        _yolo
    except NameError:
        _yolo = Slider()
    r = _yolo.identify(bg[:, :, ::-1])  # BGR->RGB
    box, conf = r[0], float(r[1])
    box_left = float(box[0])

    target_left = box_left * scale
    print(f"    YOLO: conf={conf:.3f} box_left={box_left:.1f} "
          f"scale={scale:.3f} => target_left={target_left:.1f}")
    return target_left, conf


async def solve(page, meta=None):
    """对当前 embed 模式易盾滑块执行一次完整求解。

    返回 (passed, meta)。passed 以 NECaptchaValidate 是否被填充为准。
    用法:
        browser = await p.chromium.launch(headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context()
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await ctx.new_page()
        await page.goto("https://your-site-with-captcha.com")
        passed, meta = await solve(page)
    """
    meta = dict(meta or {})

    # 0) 等滑块按钮渲染完成(embed 模式页面加载后异步初始化)
    try:
        await page.locator(SEL_BUTTON).first.wait_for(state="visible", timeout=15000)
    except Exception as e:
        meta.update({"passed": False, "fail_reason": f"no_button: {e}"})
        return False, meta
    await page.wait_for_timeout(500)
    bb = await page.locator(SEL_BUTTON).first.bounding_box()
    sx = bb["x"] + bb["width"] / 2
    sy = bb["y"] + bb["height"] / 2

    # 1) 接近 + 按下(按下触发拼图懒加载)
    await page.mouse.move(sx - random.uniform(40, 80), sy + random.uniform(-5, 5))
    await page.wait_for_timeout(random.randint(150, 300))
    await page.mouse.move(sx, sy)
    await page.wait_for_timeout(random.randint(150, 300))
    await page.mouse.down()

    # 2) 等背景图加载(懒加载, 按下后才出现; 等 naturalWidth 确保图片解码完成)
    bg_ok = False
    for _ in range(50):
        await page.wait_for_timeout(200)
        nw = await page.evaluate(
            "() => { const el = document.querySelector('%s');"
            " return el ? el.naturalWidth : 0; }" % SEL_BG)
        if nw and nw > 0:
            bg_ok = True
            break
    if not bg_ok:
        await page.mouse.up()
        meta.update({"passed": False, "fail_reason": "bg_not_loaded"})
        return False, meta

    # 3) 识别缺口
    target_left, conf = await get_gap_target(page)
    if target_left is None or conf < 0.3:
        await page.mouse.up()
        meta.update({"target_left": target_left, "yolo_conf": conf,
                     "passed": False, "fail_reason": "low_conf"})
        return False, meta

    # 4) 反解按钮位移 + 人形轨迹拖动
    D = _mouse_dx_for(target_left)
    print(f"    位移换算: target={target_left:.1f} => D={D:.1f}px")

    track = generate_drag(D)
    dur_ms = sum(t[2] for t in track)
    print(f"    轨迹: {len(track)} 事件, {dur_ms:.0f} ms")

    cum = 0.0
    cum_dy = 0.0
    t0 = time.perf_counter()
    sched = 0.0
    for dx, dy, dt_ms in track:
        sched += dt_ms
        cum += dx
        cum_dy += dy
        await page.mouse.move(sx + cum, sy + cum_dy)
        el = (time.perf_counter() - t0) * 1000
        if sched > el:
            await asyncio.sleep((sched - el) / 1000)

    # 5) 松手
    await page.mouse.up()

    # 6) 判定: 易盾 SDK 验证通过后自动填充 NECaptchaValidate
    validate = ""
    for _ in range(25):  # 最多等 ~10s
        await page.wait_for_timeout(400)
        validate = await read_validate(page)
        if validate:
            break

    passed = bool(validate)
    meta.update({
        "target_left": target_left, "yolo_conf": conf,
        "mouse_dx": D, "duration_ms": dur_ms, "event_count": len(track),
        "validate_len": len(validate), "passed": passed,
    })
    return passed, meta
