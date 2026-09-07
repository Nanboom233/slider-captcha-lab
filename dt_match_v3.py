# -*- coding: utf-8 -*-
"""dt_match_v3.py - 分布差距结论 + 修正方案。

已确认事实:
  人工 dt:   中位 5.0ms,但分布极宽——p90=13.3, p95=51.7, p99=210ms
            且 6.8% < 3ms,有 50-1000ms 的大停顿尾巴(~5%)
  本地 dt:   中位 8.2ms,分布死板(91% 集中在 8-10ms!)——这是机器指纹
  人工事件数: 中位 121 (71-281)
  本地事件数: 中位 82 (67-99)

问题清楚了:
  1. 本地 dt 分布太窄(8-10ms 占 91%),人工是"5ms 主体 + 宽尾巴"
  2. 本地事件数(82) < 人工(121),因为 dt 偏大
  3. 需要: dt 分布重采样(混合分布: 主体 4-6ms + 长尾),事件数自然上升
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

# 提取人工 dt 的经验分布(去零)
dts_nz = dts[dts > 0.5]
print(f"人工 dt 经验样本: {len(dts_nz)} 个")

# 模拟: 用人工经验分布重采样一条轨迹
def human_resample_track(target_dx=220.0, rng=None):
    """用人工 dt 经验分布 + 人工位移分布重采样生成轨迹。

    简化版: dt 从经验分布重采样,dx 按最小急动度形状分配。
    """
    rng = rng or np.random.default_rng()
    # 事件数: 从人工经验(121 中位)采样,按距离调整
    # 人工 220px 拖动约 91-121 事件;我们目标 ~110 事件
    n = int(rng.normal(112, 18))
    n = max(60, min(n, 200))
    # dt 重采样
    dts = rng.choice(dts_nz, size=n, replace=True)
    # 位移: minimum-jerk 形状
    u = np.linspace(0, 1, n + 1)[1:]
    s = 10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5
    dx_all = target_dx * np.diff(np.concatenate([[0], s]))
    # 时间轴: 累计 dt
    t = np.concatenate([[0], np.cumsum(dts)])[:n]
    track = []
    for i in range(n):
        jitter = rng.uniform(-0.4, 0.4) if rng.random() < 0.35 else 0
        track.append((float(dx_all[i] + jitter),
                      float(rng.normal(0, 0.6)),
                      float(dts[i])))
    # 距离守恒
    err = target_dx - sum(x[0] for x in track)
    track[-1] = (track[-1][0] + err, track[-1][1], track[-1][2])
    return track


track = human_resample_track()
dts_gen = np.array([t[2] for t in track])
print(f"\n重采样轨迹: {len(track)} 事件, 总时长 {dts_gen.sum():.0f}ms")
print(f"  dt 中位 {np.median(dts_gen):.2f}")
print(f"  距离和 {sum(t[0] for t in track):.2f} (目标 220)")

# 与人工分布的 KS 距离(粗略)
from scipy_stats_free import ks_statistic
d1 = np.sort(dts_nz)
d2 = np.sort(dts_gen)
ks = ks_statistic(d1, d2)
print(f"  与人工 dt 分布的 KS 距离: {ks:.3f} (越小越像)")

# 本地生成器对照
sys.path.insert(0, ".")
from human_track import generate_drag
gen = generate_drag(220.0, bias_px=1.0)
dts_old = np.sort(np.array([t[2] for t in gen]))
ks_old = ks_statistic(d1, dts_old)
print(f"  现有生成器 KS 距离: {ks_old:.3f}")
