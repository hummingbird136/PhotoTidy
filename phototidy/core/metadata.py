"""元数据提取与时间解析:统一 MediaHandler 接口(A5/A6)、多格式时间兜底(B11)。

时间策略(与行为基线一致,阶段 1 保留):
  1. EXIF / 视频元数据的拍摄时间
  2. 文件时间戳回退(取 ctime/mtime 较早者)
"""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    from PIL.ExifTags import TAGS

    _HAS_PIL = True
except ImportError:  # pragma: no cover
    _HAS_PIL = False

TIME_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S%z")

CHUNK_SIZE = 1024 * 1024  # 1MB 块读取(B9:原 4096 字节过碎)


def file_hash(path: str) -> str:
    md5 = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            md5.update(chunk)
    return md5.hexdigest()


def parse_capture_time(raw: str | None):
    """多格式时间解析,失败返回 None(B11:统一兜底)。"""
    if not raw:
        return None
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(str(raw), fmt)
        except ValueError:
            continue
    try:
        from dateutil import parser as date_parser

        return date_parser.parse(str(raw), fuzzy=True)
    except Exception:
        logger.debug("无法解析时间 %r,回退文件时间戳", raw)
        return None


def file_timestamp(path: str) -> datetime:
    """文件时间戳回退:取 ctime/mtime 较早者(与基线一致)。"""
    st = Path(path).stat()
    return datetime.fromtimestamp(min(st.st_ctime, st.st_mtime))


class MediaHandler:
    """统一元数据提取接口(A5)。照片/视频各一实现。"""

    def extract(self, path: str) -> dict:
        """返回 {'metadata': JSON str|None, 'capture_time': str|None, 'size': str|None}"""
        raise NotImplementedError


class PhotoHandler(MediaHandler):
    def extract(self, path: str) -> dict:
        metadata_json, capture_raw, size = None, None, None
        if _HAS_PIL:
            try:
                with Image.open(path) as img:  # B5:with 管理资源
                    size = f"{img.width}x{img.height}"
                    exif = img._getexif()
            except Exception as e:
                logger.warning("读取照片失败 %s: %s", path, e)
            else:
                if exif:
                    data = {TAGS[k]: str(v) for k, v in exif.items() if k in TAGS}
                    metadata_json = json.dumps(data, ensure_ascii=False)
                    capture_raw = data.get("DateTimeOriginal") or data.get("DateTimeDigitized")
        return {"metadata": metadata_json, "capture_time": capture_raw, "size": size}


class VideoHandler(MediaHandler):
    def extract(self, path: str) -> dict:
        capture_raw, metadata_json = None, None
        try:
            from mutagen.mp4 import MP4

            video = MP4(path)
            capture_raw = (video.get("©day") or [None])[0] or None
            metadata_json = json.dumps(
                {"DateTimeOriginal": capture_raw or "", "CreateDate": capture_raw or ""},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.warning("读取视频元数据失败 %s: %s", path, e)
        return {"metadata": metadata_json, "capture_time": capture_raw, "size": None}


_PHOTO, _VIDEO = PhotoHandler(), VideoHandler()


def handler_for(media_type: str) -> MediaHandler:
    return _PHOTO if media_type == "photo" else _VIDEO


def resolve_capture_date(path: str, capture_raw: str | None, date_fmt: str) -> str | None:
    """按时间策略解析归档日期;无时间信息返回 None(基线:跳过该文件)。

    文件时间戳不可读(OSError,如被并发移走/权限)也视为无时间信息,返回 None 跳过,
    不让扫描因单个文件崩溃。"""
    try:
        dt = parse_capture_time(capture_raw) or file_timestamp(path)
    except OSError:
        return None
    return dt.strftime(date_fmt) if dt else None
