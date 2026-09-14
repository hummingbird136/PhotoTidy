#!/usr/bin/env python3
"""跨平台打包入口 —— CI 与本地发布共用的唯一构建逻辑。

职责:
    1. 按当前平台调用 PyInstaller(Windows:单 exe;macOS:.app 后 ditto 压 zip)
    2. 对产物运行 --selftest 冒烟自检(可用 --no-selftest 跳过)
    3. 用 hashlib 统一生成 sha256(消除 shasum / Get-FileHash 分歧)
    4. 把可发布文件统一放到 release/ 目录,文件名固定:
         Windows → PhotoTidy.exe, PhotoTidy.exe.sha256
         macOS   → PhotoTidy-macos.zip, PhotoTidy-macos.zip.sha256

用法:
    python scripts/build.py                    # 打包 + 自检
    python scripts/build.py --no-selftest      # 仅打包
    python scripts/build.py --print-pyinstaller-version   # 给 CI 锁定版本用

注意:PyInstaller 不支持交叉编译,本脚本必须在目标平台上运行。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 唯一版本事实来源:CI 用 --print-pyinstaller-version 读取后安装,
# 保证每次构建可复现,避免上游新版引入意外破坏。
PYINSTALLER_VERSION = "6.21.0"

ROOT = Path(__file__).resolve().parent.parent
ENTRY = "scripts/package_gui.py"
WORK_DIST = ROOT / "build" / "pyi-dist"   # PyInstaller 中间产物
WORK_BUILD = ROOT / "build" / "pyi-work"
OUT_DIR = ROOT / "release"               # 最终可发布文件

# PyInstaller 公共参数(平台差异仅在 onefile / BUNDLE,由此处统一维护)
_COMMON_ARGS = [
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name",
    "PhotoTidy",
    "--collect-all",
    "customtkinter",
    "--distpath",
    str(WORK_DIST),
    "--workpath",
    str(WORK_BUILD),
    ENTRY,
]


def run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _publish(src: Path, dest_name: str) -> Path:
    """把产物复制到 release/ 并生成同名 .sha256,返回发布文件路径。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / dest_name
    shutil.copy2(src, dest)
    digest = sha256_file(dest)
    (OUT_DIR / f"{dest_name}.sha256").write_text(
        f"{digest}  {dest_name}\n", encoding="ascii"
    )
    print(f"sha256({dest_name}) = {digest}")
    return dest


def selftest(executable: Path) -> None:
    print(f"\n冒烟自检: {executable} --selftest")
    subprocess.run([str(executable), "--selftest"], cwd=ROOT, check=True)


def build_windows(do_selftest: bool) -> list[Path]:
    run([sys.executable, "-m", "PyInstaller", "--onefile", *_COMMON_ARGS])
    exe = WORK_DIST / "PhotoTidy.exe"
    if do_selftest:
        selftest(exe)
    published = _publish(exe, "PhotoTidy.exe")
    return [published, Path(f"{published}.sha256")]


def build_macos(do_selftest: bool) -> list[Path]:
    run([sys.executable, "-m", "PyInstaller", *_COMMON_ARGS])
    app = WORK_DIST / "PhotoTidy.app"
    if do_selftest:
        selftest(app / "Contents" / "MacOS" / "PhotoTidy")
    zip_path = WORK_DIST / "PhotoTidy-macos.zip"
    if zip_path.exists():
        zip_path.unlink()
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(zip_path)])
    published = _publish(zip_path, "PhotoTidy-macos.zip")
    return [published, Path(str(published) + ".sha256")]


def main() -> int:
    parser = argparse.ArgumentParser(description="PhotoTidy 跨平台打包")
    parser.add_argument("--no-selftest", action="store_true", help="跳过冒烟自检")
    parser.add_argument(
        "--print-pyinstaller-version",
        action="store_true",
        help="打印锁定的 PyInstaller 版本后退出",
    )
    args = parser.parse_args()

    if args.print_pyinstaller_version:
        print(PYINSTALLER_VERSION)
        return 0

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            f"缺少 PyInstaller,请先安装: pip install pyinstaller=={PYINSTALLER_VERSION}",
            file=sys.stderr,
        )
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*"):
        old.unlink()

    if sys.platform == "win32":
        products = build_windows(not args.no_selftest)
    elif sys.platform == "darwin":
        products = build_macos(not args.no_selftest)
    else:
        print(f"不支持的平台: {sys.platform}", file=sys.stderr)
        return 1

    print("\n✅ 可发布文件:")
    for p in products:
        print(f"  {os.path.relpath(p, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
