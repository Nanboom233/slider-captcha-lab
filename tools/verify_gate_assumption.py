# -*- coding: utf-8 -*-
"""verify_gate_assumption.py - 闸门前提复核: event_count 只对 F001 有判别力?

之前 AUC=0.818 是全体失败(含 F015 位置错/EXC 异常)混出来的。
F015 是 YOLO 低置信导致的位置错位,与事件数无关。
真正该看的是: T001 vs F001(风控)。
"""
import json
import sys

import numpy as np

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

runs = []
for line in open("captcha_runs.jsonl", encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    if r.get("track_variant") != "feedback":
        continue
    if r.get("batch_session_id", "") not in (
            "20260907-084959-bcrv7q", "20260907-094340-ybevor",
            "20260907-132131-6mn03s"):
        continue
    if r.get("verdict") in ("T001", "F001"):
        runs.append(r)

t001 = [r["event_count"] for r in runs if r["verdict"] == "T001"]
f001 = [r["event_count"] for r in runs if r["verdict"] == "F001"]
print(f"T001(通过): n={len(t001)}  中位 {np.median(t001):.0f}  均值 {np.mean(t001):.1f}  范围 [{min(t001)},{max(t001)}]")
print(f"F001(风控): n={len(f001)}  中位 {np.median(f001):.0f}  均值 {np.mean(f001):.1f}  范围 [{min(f001)},{max(f001)}]")
print(f"每个 F001 的事件数: {sorted(f001)}")
print(f"\nT001 的事件数分位数: 20%={np.percentile(t001,20):.0f} 50%={np.percentile(t001,50):.0f} 80%={np.percentile(t001,80):.0f}")

# AUC: T001 vs F001
pos, neg = t001, f001
wins = ties = 0
for p in pos:
    for n in neg:
        if p < n:      # 事件数少 → 更可能通过(方向: 小优)
            wins += 1
        elif p == n:
            ties += 1
auc = (wins + 0.5 * ties) / (len(pos) * len(neg))
print(f"\nAUC(T001 vs F001, 方向=事件少优): {auc:.3f}")
