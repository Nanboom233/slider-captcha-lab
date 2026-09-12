# -*- coding: utf-8 -*-
"""record_human_drag.py — 真人滑块拖动轨迹采集器(纯滑块,无任何注册依赖)。

用法: 打开任意带 aliyunCaptcha(或任意滑块)的页面, 真人手动拖滑块 N 次,
每次拖动自动保存为一条样本(events 原始流 + analysis 摘要)。

输出: human_drag_samples.json
  [{label: 1, events: [{type,t,x,y}...], analysis: {...}}, ...]

标签纪律: 真人样本永远 label=1;绝不用线上判定码做训练标签。
"""
import asyncio
import json
import sys
from datetime import datetime

from playwright.async_api import async_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

OUT = "human_drag_samples.json"
TARGET = 30          # 目标采集条数
PAGE_URL = "https://example.com/"   # ← 换成你的滑块页面

LISTENER_JS = """() => {
    window.__events = [];
    window.__dragging = false;
    const rec = (type) => (e) => {
        if (type === 'mousedown') window.__dragging = true;
        if (type === 'mouseup') window.__dragging = false;
        window.__events.push({
            type: type,
            t: Math.round(performance.now() * 10) / 10,
            x: Math.round(e.clientX * 100) / 100,
            y: Math.round(e.clientY * 100) / 100,
            buttons: e.buttons
        });
    };
    if (!window.__recording) {
        document.addEventListener('mousemove', rec('mousemove'), true);
        document.addEventListener('mousedown', rec('mousedown'), true);
        document.addEventListener('mouseup', rec('mouseup'), true);
        window.__recording = true;
    }
}"""

PULL_JS = """() => {
    const ev = window.__events || [];
    window.__events = [];
    return ev;
}"""


def analyze(events):
    """分析单次拖拽的手势特征(摘要用;完整 59 维特征见 track_features.py)。"""
    moves = [e for e in events if e["type"] == "mousemove"]
    downs = [e for e in events if e["type"] == "mousedown"]
    ups = [e for e in events if e["type"] == "mouseup"]
    if not (downs and ups and len(moves) > 5):
        return None
    t0, t1 = downs[0]["t"], ups[-1]["t"]
    dur = t1 - t0
    drag_moves = [e for e in moves if t0 <= e["t"] <= t1]
    if len(drag_moves) < 5:
        return None
    xs = [e["x"] for e in drag_moves]
    ys = [e["y"] for e in drag_moves]
    intervals = [drag_moves[i + 1]["t"] - drag_moves[i]["t"]
                 for i in range(len(drag_moves) - 1)]
    intervals = [i for i in intervals if i >= 0]
    intervals.sort()
    velocities = []
    for i in range(len(drag_moves) - 1):
        dt = drag_moves[i + 1]["t"] - drag_moves[i]["t"]
        dx = abs(drag_moves[i + 1]["x"] - drag_moves[i]["x"])
        if dt > 0:
            velocities.append(dx / dt)
    return {
        "duration_ms": round(dur),
        "n_moves": len(drag_moves),
        "median_interval_ms": intervals[len(intervals) // 2] if intervals else 0,
        "p90_interval_ms": intervals[int(len(intervals) * 0.9)] if intervals else 0,
        "peak_speed_px_ms": round(max(velocities), 3) if velocities else 0,
        "mean_speed_px_ms": round(sum(velocities) / len(velocities), 3) if velocities else 0,
        "x_start": xs[0], "x_end": xs[-1],
        "x_total": round(xs[-1] - xs[0], 1),
        "y_std": round((sum((y - sum(ys) / len(ys)) ** 2 for y in ys)
                        / len(ys)) ** 0.5, 2),
    }


async def main():
    samples = []
    print(f"真人轨迹采集: 目标 {TARGET} 条")
    print("操作: 页面打开后,手动拖滑块。每次完整拖动(mousedown→mouseup)记 1 条。")
    print("Ctrl+C 提前结束。\n")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 850})
        page = await ctx.new_page()
        await page.goto(PAGE_URL, wait_until="domcontentloaded")
        await page.evaluate(LISTENER_JS)
        pend = []
        while len(samples) < TARGET:
            await asyncio.sleep(2.0)
            ev = await page.evaluate(PULL_JS)
            if ev:
                pend.extend(ev)
            # 一次拖动结束(mousedown 后出现 mouseup)
            has_down = any(e["type"] == "mousedown" for e in pend)
            has_up = any(e["type"] == "mouseup" for e in pend)
            if has_down and has_up:
                a = analyze(pend)
                if a:
                    samples.append({
                        "label": 1,
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "events": pend, "analysis": a,
                    })
                    print(f"[{len(samples)}/{TARGET}] 采到: "
                          f"时长 {a['duration_ms']}ms, {a['n_moves']} moves, "
                          f"peak {a['peak_speed_px_ms']:.2f} px/ms")
                pend = []
            elif not has_down:
                # 拖动前把非拖动事件丢掉(mousemove idle 噪声)
                if pend and pend[-1]["type"] == "mouseup":
                    pend = []
        await browser.close()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=1)
    print(f"\n保存 {len(samples)} 条 -> {OUT}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n手动中断")
