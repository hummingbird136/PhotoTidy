"""通用文件操作(独立 file 层,A6)。

- 跨平台文件名安全化(Windows 非法字符、保留名、长路径)
- 同名冲突自动改名而非覆盖(基线修复项)
- move / copy(复制→校验→删源)两种模式
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

WINDOWS_ILLEGAL = '\\/:*?"<>|'
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_PATH_LEN = 240  # Windows 260 上限留余量


def sanitize_name(name: str) -> str:
    """过滤非法字符并规避 Windows 保留名(时间字符串含 ':' 尤其注意)。"""
    cleaned = "".join("_" if c in WINDOWS_ILLEGAL else c for c in name).rstrip(" .")
    if not cleaned:
        cleaned = "_"
    if cleaned.split(".")[0].upper() in WINDOWS_RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def safe_dir_name(name: str, max_len: int = MAX_PATH_LEN) -> str:
    return sanitize_name(name)[:max_len]


def unique_path(dest: Path) -> Path:
    """同名冲突自动改名:name.ext → name_1.ext / name_2.ext ...(不覆盖)"""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for i in range(1, 10000):
        candidate = dest.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法为 {dest} 生成不冲突的目标路径")


def move(src: str | Path, dest_dir: str | Path) -> Path:
    """移动文件到 dest_dir,返回最终路径(已做冲突改名)。"""
    src, dest_dir = Path(src), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_path(dest_dir / src.name)
    shutil.move(str(src), str(dest))
    logger.info("移动 %s -> %s", src, dest)
    return dest


def copy_verify_delete(src: str | Path, dest_dir: str | Path) -> Path:
    """copy 模式:复制 → hash 校验 → 删源(本地→NAS 场景防中断丢数据)。"""
    from .metadata import file_hash

    src, dest_dir = Path(src), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    src_hash = file_hash(str(src))
    dest = unique_path(dest_dir / src.name)
    shutil.copy2(str(src), str(dest))
    if file_hash(str(dest)) != src_hash:
        dest.unlink(missing_ok=True)
        raise OSError(f"复制校验失败(hash 不一致): {src} -> {dest}")
    Path(src).unlink()
    logger.info("复制并校验通过,删除源 %s -> %s", src, dest)
    return dest
