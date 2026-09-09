"""配置集中管理:单一来源(A7)。

默认配置可经 YAML 文件覆盖(--config)。格式集合、日期目录格式在此唯一定义。
"""

from dataclasses import asdict, dataclass, field

# 首版支持范围:照片 jpg/jpeg/png/heic;视频 mp4/mov/avi。
# RAW/GIF 未默认启用,可通过 YAML 配置自行添加扩展名。
DEFAULT_IMAGE_FORMATS = (".jpg", ".jpeg", ".png", ".heic")
DEFAULT_VIDEO_FORMATS = (".mp4", ".mov", ".avi")

# 日期目录格式:单一口径(原 %Y-%m-%d 与 %Y/%m-%d 混用问题消除)
DATE_FOLDER_FORMAT = "%Y-%m-%d"


@dataclass
class Config:
    """运行配置,字段即 YAML 可覆盖项。"""

    image_formats: list = field(default_factory=lambda: list(DEFAULT_IMAGE_FORMATS))
    video_formats: list = field(default_factory=lambda: list(DEFAULT_VIDEO_FORMATS))
    date_folder_format: str = DATE_FOLDER_FORMAT
    duplicate_dir_name: str = "duplicate"  # 重复文件移入的子目录名(相对扫描根目录)
    mode: str = "move"  # move | copy(copy 模式:复制→hash 校验→删源)
    db_path: str = ""  # 空 = 使用默认用户目录

    def all_formats(self) -> tuple:
        return tuple(self.image_formats) + tuple(self.video_formats)

    def media_type_for(self, path: str) -> str:
        ext = path.lower().rsplit(".", 1)[-1]
        if "." + ext in [f.lower() for f in self.image_formats]:
            return "photo"
        return "video"

    @classmethod
    def load(cls, config_path: str | None = None) -> "Config":
        cfg = cls()
        if config_path:
            import yaml  # 延迟导入,CLI 未用 --config 时不强依赖

            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for key, value in data.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)


def default_db_path() -> str:
    """数据库默认位置:用户目录下,避免污染扫描目录(阶段 1 决策)。"""
    import os

    return os.path.join(os.path.expanduser("~"), ".phototidy", "photos.db")
