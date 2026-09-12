# -*- coding: utf-8 -*-
"""scipy_stats_free.py - 无 scipy 依赖的统计工具。"""
import numpy as np


def ks_statistic(a, b):
    """两样本 KS 距离(a, b 为已排序数组)。"""
    a = np.sort(np.asarray(a))
    b = np.sort(np.asarray(b))
    all_vals = np.concatenate([a, b])
    cdf_a = np.searchsorted(a, all_vals, side="right") / len(a)
    cdf_b = np.searchsorted(b, all_vals, side="right") / len(b)
    return float(np.max(np.abs(cdf_a - cdf_b)))
