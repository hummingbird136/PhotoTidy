"""扫描入库:遍历目录,解析元数据与拍摄时间,写入 SQLite(阶段一:只读)。

验收对照规则 ①:EXIF 优先 + 时间戳回退、hash 判重、无拍摄时间跳过、JSON 元数据。
验收对照规则 ②:重扫同路径幂等(不重复计 occurrence_count)。
"""

import logging
import os

from .. import db as db_module
from .metadata import (
    file_hash,
    file_timestamp,
    handler_for,
    parse_capture_time,
    resolve_capture_date,
)

log = logging.getLogger(__name__)

_CAPTURE_TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _iter_media_files(root: str, config):
    # media_type_for 对所有非图片扩展名默认 video,不能用它过滤,需先按格式集合筛
    formats = {f.lower() for f in config.all_formats()}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() not in formats:
                continue
            path = os.path.join(dirpath, name)
            if os.path.isfile(path):
                yield path


def scan(root: str, db_path: str, config, progress_cb=None) -> dict:
    """扫描 root 下所有受支持媒体文件并入库。返回统计。

    progress_cb:可选回调,每处理完一个文件调用 progress_cb(path)(供 GUI 进度条)。
    """
    db_module.init_db(db_path)
    scanned = 0
    skipped_no_time = 0
    with db_module.connect(db_path) as conn:
        for path in _iter_media_files(root, config):
            if progress_cb:
                progress_cb(path)
            media_type = config.media_type_for(path)
            handler = handler_for(media_type)
            try:
                info = handler.extract(path)
            except Exception as exc:  # 元数据损坏不阻塞扫描
                log.warning("元数据解析失败 %s: %s", path, exc)
                info = {"metadata": None, "capture_time": None, "size": None}
            capture_date = resolve_capture_date(
                path, info.get("capture_time"), config.date_folder_format
            )
            if capture_date is None:
                # 无拍摄时间:跳过,不打扰用户
                log.info("跳过(无拍摄时间): %s", path)
                skipped_no_time += 1
                continue
            try:
                dt = parse_capture_time(info.get("capture_time")) or file_timestamp(path)
            except OSError:  # 与 resolve_capture_date 同口径:stat 失败视为跳过
                skipped_no_time += 1
                continue
            path = os.path.abspath(path)
            row = {
                "full_path": path,
                "name": os.path.basename(path),
                "metadata": info.get("metadata") or "{}",  # handler 已产出 JSON str
                "size": info.get("size") or str(os.path.getsize(path)),
                "capture_time": dt.strftime(_CAPTURE_TIME_FMT),
                "capture_date": capture_date,
                "hash": file_hash(path),
                "media_type": media_type,
            }
            # 重扫同路径:仅更新字段,occurrence_count 不变(幂等)。
            # 文件被 organize/dedupe 移到新路径:认领旧记录(改 full_path),
            # 不另插新行,避免每次 run 后库随文件数翻倍膨胀。
            # 新路径撞上磁盘上仍存在的同 hash 文件才是真重复,occurrence_count +1。
            # 必须先查重再 upsert,否则 find_by_hash 会查到刚插入的自己。
            existing = conn.execute("SELECT 1 FROM photos WHERE full_path = ?", (path,)).fetchone()
            if not existing:
                stale = db_module.find_stale_by_hash(conn, row["hash"], path)
                if stale:
                    db_module.update_path_after_move(conn, stale, path)
                elif db_module.find_by_hash(conn, row["hash"]):
                    db_module.increment_occurrence(conn, row["hash"])
            db_module.upsert_media(conn, row)
            scanned += 1
    return {"scanned": scanned, "skipped_no_time": skipped_no_time}
