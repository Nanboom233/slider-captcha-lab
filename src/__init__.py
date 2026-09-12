"""slider-captcha-lab 核心库。

模块一览：
  human_track      人形轨迹生成（通用核心算法）
  track_features   59 维轨迹形态特征
  scipy_stats_free 无 scipy 依赖的统计工具
  slider_cdp       阿里云滑块求解器（AliyunCaptcha）
  slider_cdp_yidun 网易易盾滑块求解器（Netease Yidun）

用法（在项目根目录运行，见根目录 main.py）：
    from src.slider_cdp_yidun import solve
"""

__all__ = [
    "human_track",
    "track_features",
    "scipy_stats_free",
    "slider_cdp",
    "slider_cdp_yidun",
]
