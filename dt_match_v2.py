# -*- coding: utf-8 -*-
"""dt_match_v2.py - 修正单位后的人工 vs 本地 dt 分布对比。

人工样本 t 单位是 ms(events 间 5.2ms),events_to_track 返回的 dt 已是 ms,
之前 *1000 是错的。
"""
import json
import sys

import numpy as np

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

humans = json.load(open("human_drag_samples.json", encoding="utf-8"))
dts = []
for h in humans:
    ev = h.get("events") or []
    for a, b in zip(ev, ev[1:]):
        dt = b["t"] - a["t"]
        if dt > 0:
            dts.append(dt)
dts = np.array(dts)
print("人工 dt(ms):")
print(f"  n={len(dts)} 中位 {np.median(dts):.2f} 均值 {np.mean(dts):.2f}")
for q in (5, 10, 25, 50, 75, 90, 95, 99):
    print(f"  p{q}: {np.percentile(dts, q):.2f}")
bins = [0, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 30, 50, 100, 1000]
hist, _ = np.histogram(dts, bins=bins)
print("  直方图:")
for i in range(len(hist)):
    pct = hist[i] / len(dts) * 100
    print(f"    [{bins[i]:>3},{bins[i+1]:>3}) {pct:5.1f}% " + "#" * int(pct / 2))

sys.path.insert(0, ".")
from human_track import generate_drag

gen = np.array([t[2] for t in generate_drag(220.0, bias_px=1.0)])
print(f"\n本地生成 dt: 中位 {np.median(gen):.2f} 均值 {np.mean(gen):.2f}")
hist2, _ = np.histogram(gen, bins=bins)
print("  直方图:")
for i in range(len(hist2)):
    pct = hist2[i] / len(gen) * 100
    print(f"    [{bins[i]:>3},{bins[i+1]:>3}) {pct:5.1f}% " + "#" * int(pct / 2))

# 事件数对比
n_human = [len([1 for a, b in zip(h["events"], h["events"][1:])
                if b["t"] > a["t"]]) for h in humans]
print(f"\n人工拖动事件数: 中位 {np.median(n_human):.0f} "
      f"范围 [{min(n_human)},{max(n_human)}]")
n_gen = [len(generate_drag(220.0, bias_px=1.0)) for _ in range(20)]
print(f"本地生成事件数: 中位 {np.median(n_gen):.0f} "
      f"范围 [{min(n_gen)},{max(n_gen)}]")

# 人工的时长
durs = [h.get("analysis", {}).get("duration_ms", 0) for h in humans]
print(f"人工拖动时长: 中位 {np.median(durs):.0f}ms "
      f"范围 [{min(durs)},{max(durs)}]")
