# -*- coding: utf-8 -*-
"""track_features.py - 轨迹特征提取与稳健统计。

输入 track 格式: [(dx, dy, dt_ms), ...]
也支持 events 格式: [{x,y,t/t_ms}, ...] 转换。

特征体系 v2:
  - 全局统计(时长/事件/速度分位/dt 分布/Y 漂移/jerk)
  - 时间细分特征(_temporal_shape_features): 速度曲线形状、
    加速/减速切换节奏、分段统计、末端减速行为
"""
import math


def pct(vals, p):
    vals = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return 0.0
    vals = sorted(vals)
    k = min(len(vals) - 1, max(0, int(round(p / 100.0 * (len(vals) - 1)))))
    return vals[k]


def mean(vals):
    vals = list(vals)
    return sum(vals) / len(vals) if vals else 0.0


def std(vals):
    vals = list(vals)
    if len(vals) < 2:
        return 0.0
    m = mean(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / (len(vals) - 1))


def events_to_track(events):
    """把绝对坐标事件转为 [(dx,dy,dt_ms)]。"""
    pts = []
    for e in events:
        if "x" not in e or "y" not in e:
            continue
        t = e.get("t_ms", e.get("t", 0.0))
        pts.append((float(e["x"]), float(e["y"]), float(t)))
    pts.sort(key=lambda x: x[2])
    out = []
    for i in range(1, len(pts)):
        x0, y0, t0 = pts[i - 1]
        x1, y1, t1 = pts[i]
        dt = t1 - t0
        if dt > 0:
            out.append((x1 - x0, y1 - y0, dt))
    return out


def _temporal_shape_features(track, vels, accels, dts, dts_ms):
    """速度曲线形状 / 分段时间粒度 / 末端减速行为的细粒度特征。"""
    out = {}
    n = len(vels)
    short_keys = (
        "accel_sign_changes", "accel_sign_change_rate",
        "accel_first_change_pos", "accel_last_change_pos",
        "speed_local_maxima", "speed_local_minima",
        "speed_plateau_ratio", "reacceleration_count",
        "decel_smoothness", "pre_release_micro",
        "last_200ms_events", "last_200ms_speed_p50",
        "turn_points_density", "speed_reversal_count",
    )
    if n < 3:
        for k in short_keys:
            out[k] = 0.0
        for i in range(10):
            out[f"seg{i}_speed_p50"] = 0.0
            out[f"seg{i}_dt_mean"] = 0.0
        return out

    # ---------- A. 速度曲线形状 ----------
    # 加速度变号次数: 加速↔减速的切换(核心指纹)。
    # 真人因视觉反馈而减速-再加速;脚本无反馈,曲线单调。
    signs = [1 if a > 1e-9 else (-1 if a < -1e-9 else 0) for a in accels]
    nz = [s for s in signs if s != 0]
    sign_changes = 0
    change_positions = []
    for i in range(1, len(nz)):
        if nz[i] != nz[i - 1]:
            sign_changes += 1
            change_positions.append(i / max(1, len(nz) - 1))
    out["accel_sign_changes"] = float(sign_changes)
    out["accel_sign_change_rate"] = sign_changes / max(1, len(nz))
    out["accel_first_change_pos"] = change_positions[0] if change_positions else 0.0
    out["accel_last_change_pos"] = change_positions[-1] if change_positions else 0.0

    # 局部极大/极小(超过峰值 20% 的有效波动)
    thr = 0.20 * max(vels)
    local_max = sum(1 for i in range(1, n - 1)
                    if vels[i] >= vels[i - 1] and vels[i] >= vels[i + 1] and vels[i] > thr)
    local_min = sum(1 for i in range(1, n - 1)
                    if vels[i] <= vels[i - 1] and vels[i] <= vels[i + 1])
    out["speed_local_maxima"] = float(local_max)
    out["speed_local_minima"] = float(local_min)

    # 速度平台期: 相邻帧速度差 < 峰值 5% 视为平台
    v_max = max(vels)
    plateau = sum(1 for i in range(1, n)
                  if abs(vels[i] - vels[i - 1]) < 0.05 * max(v_max, 1e-9))
    out["speed_plateau_ratio"] = plateau / max(1, n - 1)

    # 二次加速: 明显减速后(速度跌到近期峰值 70% 以下)速度重新上升
    reacc = 0
    falling = False
    v_peak_recent = vels[0]
    for i in range(1, n):
        v = vels[i]
        v_peak_recent = max(v_peak_recent, v)
        if v < v_peak_recent * 0.7:
            falling = True
        elif falling and v > vels[i - 1]:
            reacc += 1
            falling = False
            v_peak_recent = v
    out["reacceleration_count"] = float(reacc)

    # 速度方向反转(含负速度,真人回拉时出现)
    out["speed_reversal_count"] = float(sum(
        1 for i in range(1, n) if (vels[i] > 0) != (vels[i - 1] > 0)))
    # 转折点密度(一阶差分变号 / 帧数)
    d1 = [vels[i] - vels[i - 1] for i in range(1, n)]
    turns = sum(1 for i in range(1, len(d1)) if (d1[i] > 0) != (d1[i - 1] > 0))
    out["turn_points_density"] = turns / max(1, len(d1))

    # ---------- B. 分段时间粒度(10 段) ----------
    n_seg = 10
    seg_len = max(1, n // n_seg)
    for s in range(n_seg):
        lo = s * seg_len
        hi = n if s == n_seg - 1 else (s + 1) * seg_len
        seg_v = vels[lo:hi]
        seg_dt = dts_ms[lo:hi]
        out[f"seg{s}_speed_p50"] = pct(seg_v, 50) if seg_v else 0.0
        out[f"seg{s}_dt_mean"] = mean(seg_dt) if seg_dt else 0.0

    # ---------- C. 减速尾巴 / 释放前行为 ----------
    # 末端减速平滑度: 最后 20% 帧的加速度变异系数(越小越平顺)
    tail_acc = accels[int(len(accels) * 0.8):] if len(accels) > 5 else accels
    if len(tail_acc) > 2:
        out["decel_smoothness"] = std(tail_acc) / max(1.0, abs(mean(tail_acc)) + 1e-9)
    else:
        out["decel_smoothness"] = 0.0

    # 释放前 200ms 的微调事件数与速度(从尾部倒序累积时间)
    remaining = 0.2
    pre_events = 0
    pre_speeds = []
    pre_micro = 0
    for t in reversed(track):
        dt_s = t[2] / 1000.0
        if remaining <= 0:
            break
        pre_events += 1
        speed = abs(t[0]) / max(dt_s, 1e-6)
        pre_speeds.append(speed)
        if 0 < abs(t[0]) < 1.0:
            pre_micro += 1
        remaining -= dt_s
    out["last_200ms_events"] = float(pre_events)
    out["last_200ms_speed_p50"] = pct(pre_speeds, 50) if pre_speeds else 0.0
    out["pre_release_micro"] = pre_micro / max(1, len(track))
    return out


def extract_features(track):
    """提取全部特征(全局统计 + 时间细分)。"""
    if not track:
        return {}
    dxs = [float(x[0]) for x in track]
    dys = [float(x[1]) for x in track]
    dts_ms = [max(float(x[2]), 1e-6) for x in track]
    dts = [x / 1000.0 for x in dts_ms]
    duration_s = sum(dts)
    distance_px = sum(dxs)
    vels = [dx / dt for dx, dt in zip(dxs, dts)]
    accels = []
    for i in range(1, len(vels)):
        accels.append((vels[i] - vels[i - 1]) / max(dts[i], 1e-6))
    jerks = []
    for i in range(1, len(accels)):
        jerks.append((accels[i] - accels[i - 1]) / max(dts[i + 1], 1e-6))
    y_abs = []
    yy = 0.0
    for dy in dys:
        yy += dy
        y_abs.append(yy)
    peak_speed = max(vels, default=0.0)
    peak_idx = vels.index(peak_speed) if vels else 0
    # 主峰计数: 超过峰值 25% 的局部极大
    peaks = 0
    for i in range(1, len(vels) - 1):
        if vels[i] > vels[i - 1] and vels[i] > vels[i + 1] and vels[i] > 0.25 * peak_speed:
            peaks += 1

    feats = {
        "event_count": len(track),
        "duration_s": duration_s,
        "distance_px": distance_px,
        "dt_min_ms": min(dts_ms),
        "dt_mean_ms": mean(dts_ms),
        "dt_std_ms": std(dts_ms),
        "dt_cv": std(dts_ms) / max(mean(dts_ms), 1e-6),
        "dt_p50_ms": pct(dts_ms, 50),
        "dt_p90_ms": pct(dts_ms, 90),
        "dt_p95_ms": pct(dts_ms, 95),
        "speed_p50": pct(vels, 50),
        "speed_p90": pct(vels, 90),
        "speed_p95": pct(vels, 95),
        "speed_max": peak_speed,
        "peak_speed_pos": peak_idx / max(1, len(vels) - 1),
        "speed_peaks": peaks,
        "accel_p95_abs": pct([abs(x) for x in accels], 95),
        "accel_max_abs": max([abs(x) for x in accels], default=0.0),
        "jerk_p95_abs": pct([abs(x) for x in jerks], 95),
        "jerk_max_abs": max([abs(x) for x in jerks], default=0.0),
        "neg_dx_ratio": sum(1 for x in dxs if x < 0) / len(dxs),
        "zero_dx_ratio": sum(1 for x in dxs if abs(x) < 1e-9) / len(dxs),
        "y_total": sum(dys),
        "y_std": std(y_abs),
        "y_max_abs": max([abs(x) for x in y_abs], default=0.0),
    }

    # 时间细分特征: 加速/减速切换节奏、分段统计、末端行为
    feats.update(_temporal_shape_features(track, vels, accels, dts, dts_ms))
    return feats


def summarize_feature_rows(rows):
    if not rows:
        return {}
    keys = sorted({k for r in rows for k in r.keys() if isinstance(r.get(k), (int, float))})
    out = {}
    for k in keys:
        vals = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
        out[k] = {"n": len(vals), "mean": mean(vals), "p50": pct(vals, 50), "p90": pct(vals, 90), "min": min(vals), "max": max(vals)}
    return out
