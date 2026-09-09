"""日志配置:CLI/GUI 共用,每运行一次单独落盘。

默认写到 ~/.phototidy/logs(双击启动的 App 其 cwd 可能是 /,
写 cwd/logs 会启动即 PermissionError);目录不可写时退回仅控制台日志。
"""

import logging
import os
import time

LOG_DIR = os.path.join(os.path.expanduser("~"), ".phototidy", "logs")


def setup_file_logger(log_dir: str = LOG_DIR) -> str:
    """返回实际日志文件路径;目录不可写时返回 "(控制台)"。"""
    try:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, time.strftime("run_%Y%m%d_%H%M%S.log"))
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler()
        path = ""
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    return path or "(控制台)"
