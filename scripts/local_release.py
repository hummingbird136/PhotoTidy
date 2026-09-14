#!/usr/bin/env python3
"""本地编译 + 上传到 GitHub Release(离线/无 Actions 时的备用发布方式)。

用法:
    python scripts/local_release.py <tag>      # 编译当前平台产物并上传到指定 release
    python scripts/local_release.py v0.1.0

说明:
- 构建逻辑全部在 scripts/build.py(与 CI 同源),本脚本只负责"构建 + 用 gh 上传"。
- PyInstaller 不支持交叉编译,必须在目标平台运行:Windows 产 exe,macOS 产 zip。
- 依赖 gh CLI 已登录且对仓库有写权限;release 不存在则创建。
"""

import subprocess
import sys
from pathlib import Path

# Windows 控制台默认编码可能无法输出中文,统一强制 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

REPO = "hummingbird136/PhotoTidy"
OUT_DIR = Path("release")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/local_release.py <tag>", file=sys.stderr)
        return 1
    tag = sys.argv[1]

    # 1) 统一构建(含 --selftest 与 sha256),产物在 release/
    run([sys.executable, "scripts/build.py"])

    # 2) 上传到 GitHub Release(若不存在则创建)
    assets = sorted(str(p) for p in OUT_DIR.glob("*"))
    print(f"\n上传 {len(assets)} 个文件到 {repo_url(tag)} ...")
    if run(["gh", "release", "upload", tag, *assets, "--repo", REPO, "--clobber"], check=False).returncode != 0:
        print("release 不存在,创建中...")
        run(["gh", "release", "create", tag, *assets, "--repo", REPO, "--title", tag,
             "--notes", f"PhotoTidy {tag}"])
    else:
        print("上传完成")

    print(f"\n✅ 产物已发布: {repo_url(tag)}")
    return 0


def repo_url(tag: str) -> str:
    return f"https://github.com/{REPO}/releases/tag/{tag}"


if __name__ == "__main__":
    sys.exit(main())
