# -*- coding: utf-8 -*-
"""shape_gap_diagnosis.py - 人工 vs 生成 TOP 特征的逐项分布对比。"""
import json
import sys

import numpy as np

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, ".")
import os
os.environ["TRACK_VARIANT"] = "feedback"
from human_track import generate_drag
from track_features import extract_features, events_to_track

humans = json.load(open("human_drag_samples.json", encoding="utf-8"))
h_rows = [extract_features(events_to_track(h.get("events") or []))
          for h in humans]
h_rows = [r for r in h_rows if r]
g_rows = [extract_features(generate_drag(220.0, bias_px=1.0))
          for _ in range(120)]

keys = ["pre_release_micro", "dt_min_ms", "accel_max_abs", "accel_p95_abs",
        "jerk_max_abs", "jerk_p95_abs", "y_std", "zero_dx_ratio",
        "speed_reversal_count", "dt_cv", "event_count", "duration_s",
        "speed_max", "peak_speed_pos"]

print(f"{'特征':<22} {'人工p10':>9} {'人工p50':>9} {'人工p90':>9} "
      f"{'生成p10':>9} {'生成p50':>9} {'生成p90':>9}")
for k in keys:
    hv = sorted(float(r.get(k, 0) or 0) for r in h_rows)
    gv = sorted(float(r.get(k, 0) or 0) for r in g_rows)
    q = lambda a, p: a[min(len(a) - 1, int(len(a) * p / 100))]
    print(f"{k:<22} {q(hv,10):>9.3f} {q(hv,50):>9.3f} {q(hv,90):>9.3f} "
          f"{q(gv,10):>9.3f} {q(gv,50):>9.3f} {q(gv,90):>9.3f}")
