"""PhotoTidy(拾光):照片/视频扫描、去重、按日期归档。"""

try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("phototidy")
except PackageNotFoundError:  # 源码直跑未安装场景
    __version__ = "0.0.0.dev0"
