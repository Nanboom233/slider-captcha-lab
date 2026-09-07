# -*- coding: utf-8 -*-
"""train_shape_scorer.py - 轨迹形态打分器: 人 vs 机器 二分类。

标签纪律:
  人工样本(human_drag_samples.json) label=1(人)
  生成器轨迹(label=0) 用当前 feedback 变体生成
  绝不用线上 T001/F001 做训练标签(避免"模仿被判过"过拟合)

用途: auto_batch 提交前对轨迹打分,低于阈值的重生成(离线闸门)。
"""
import json
import sys

import numpy as np

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, ".")
from human_track import generate_drag
from track_features import extract_features, events_to_track

MODEL_FILE = "shape_scorer.json"

# ---------------- 数据准备 ----------------
FEAT_KEYS = None


def featurize(track):
    global FEAT_KEYS
    f = extract_features(track)
    if FEAT_KEYS is None:
        FEAT_KEYS = sorted(f.keys())
    return [float(f.get(k, 0.0) or 0.0) for k in FEAT_KEYS]


def load_dataset():
    # label=1: 人工
    humans = json.load(open("human_drag_samples.json", encoding="utf-8"))
    X, y = [], []
    for h in humans:
        tr = events_to_track(h.get("events") or [])
        if len(tr) >= 10:
            X.append(featurize(tr))
            y.append(1)
    n_human = len(X)
    # label=0: 生成(feedback 变体,当前线上配置)
    import os
    os.environ["TRACK_VARIANT"] = "feedback"
    n_gen = 0
    for _ in range(n_human * 6):   # 1:6 比例
        tr = generate_drag(220.0, bias_px=1.0)
        X.append(featurize(tr))
        y.append(0)
        n_gen += 1
    print(f"数据集: 人工 {n_human} (label=1) + 生成 {n_gen} (label=0)")
    return np.array(X), np.array(y)


def main():
    X, y = load_dataset()

    # 标准化参数存下来(推理时用)
    mu = X.mean(axis=0)
    sd = X.std(axis=0) + 1e-9
    Xn = (X - mu) / sd

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score, LeaveOneOut

    # 随机森林(主模型) — LOO 对人工样本
    rf = RandomForestClassifier(n_estimators=300, max_depth=6,
                                class_weight="balanced", random_state=42)
    rf.fit(Xn, y)
    # 人工样本 LOO CV(只对 label=1 的 30 条做留一,衡量"真人保持率")
    humans_idx = np.where(y == 1)[0]
    gen_idx = np.where(y == 0)[0]
    # 简化 CV: 5-fold 分层
    from sklearn.model_selection import StratifiedKFold
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    acc = cross_val_score(rf, Xn, y, cv=cv, scoring="accuracy").mean()
    auc = cross_val_score(rf, Xn, y, cv=cv, scoring="roc_auc").mean()
    print(f"5-fold: acc={acc:.3f} auc={auc:.3f}")

    # 人工保持率: 全部人工样本的打分分布
    scores_human = rf.predict_proba(Xn[humans_idx])[:, 1]
    scores_gen = rf.predict_proba(Xn[gen_idx])[:, 1]
    print(f"\n人工打分: p10={np.percentile(scores_human,10):.3f} "
          f"p50={np.percentile(scores_human,50):.3f} "
          f"min={scores_human.min():.3f}")
    print(f"生成打分: p10={np.percentile(scores_gen,10):.3f} "
          f"p50={np.percentile(scores_gen,50):.3f} "
          f"max={scores_gen.max():.3f}")

    # 阈值: 90% 的人工样本通过
    thr = float(np.percentile(scores_human, 10))
    keep_rate_gen = float((scores_gen >= thr).mean())
    print(f"\n阈值(保 90% 真人): {thr:.3f}")
    print(f"生成轨迹通过率: {keep_rate_gen*100:.1f}% "
          f"→ 意味着闸门平均重生成 {(1/(keep_rate_gen+1e-9)-1)*100:.0f}% 的轨迹")

    # 特征重要性 TOP 10
    imp = sorted(zip(FEAT_KEYS, rf.feature_importances_),
                 key=lambda x: -x[1])[:10]
    print("\n特征重要性 TOP10:")
    for k, v in imp:
        print(f"  {k:<24} {v:.3f}")

    # 保存模型(纯 JSON,不依赖 pickle)
    trees_export = {
        "feature_keys": FEAT_KEYS,
        "mu": mu.tolist(),
        "sd": sd.tolist(),
        "threshold": thr,
        "note": "RandomForest 300x6, 训练数据 30人工+180生成(feedback)",
        "sklearn_model": None,   # 推理用 inference_shape_scorer.py 的手写实现
    }
    json.dump(trees_export, open(MODEL_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n元数据已存 {MODEL_FILE}")
    # 模型本体用 pickle(sklearn 标准方式)
    import pickle
    with open("shape_scorer.pkl", "wb") as f:
        pickle.dump({"model": rf, "mu": mu, "sd": sd,
                     "threshold": thr, "feature_keys": FEAT_KEYS}, f)
    print("模型已存 shape_scorer.pkl")


if __name__ == "__main__":
    main()
