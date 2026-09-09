#!/usr/bin/env python3
"""本地编译 + 上传到 GitHub Release 的脚本。

用法:
    python scripts/local_release.py <tag>          # 编译当前平台产物并上传到指定 release
    python scripts/local_release.py v0.1.0

说明:
- PyInstaller 不支持交叉编译,必须在目标平台上运行:
    * Windows → PhotoTidy.exe
    * macOS   → PhotoTidy-macos.zip
- 依赖 gh CLI 已登录且对仓库有写权限。
- 若 release 已存在则追加上传资产,否则创建。
"""

import hashlib
import os
import shutil
import subprocess
import sys


def run(cmd, **kw):
    print(f"\n$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kw)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_windows() -> list[str]:
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--windowed", "--onefile", "--name", "PhotoTidy",
         "--collect-all", "customtkinter", "scripts/package_gui.py"])
    exe = os.path.join("dist", "PhotoTidy.exe")
    sha = sha256_file(exe)
    with open("PhotoTidy.exe.sha256", "w", encoding="ascii") as f:
        f.write(f"{sha}  PhotoTidy.exe\n")
    print(f"sha256: {sha}")
    return [exe, "PhotoTidy.exe.sha256"]


def build_macos() -> list[str]:
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--windowed", "--name", "PhotoTidy", "--collect-all", "customtkinter",
         "--distpath", "dist", "--workpath", "build", "scripts/package_gui.py"])
    app = os.path.join("dist", "PhotoTidy.app")
    zip_name = "PhotoTidy-macos.zip"
    if os.path.exists(zip_name):
        os.remove(zip_name)
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, zip_name])
    sha = sha256_file(zip_name)
    with open(f"{zip_name}.sha256", "w", encoding="ascii") as f:
        f.write(f"{sha}  {zip_name}\n")
    print(f"sha256: {sha}")
    return [zip_name, f"{zip_name}.sha256"]


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/local_release.py <tag>", file=sys.stderr)
        sys.exit(1)
    tag = sys.argv[1]

    # 确保依赖已安装
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        run([sys.executable, "-m", "pip", "install", "-e", ".[gui]", "pyinstaller"])

    if sys.platform == "win32":
        artifacts = build_windows()
    elif sys.platform == "darwin":
        artifacts = build_macos()
    else:
        print(f"不支持的平台: {sys.platform}", file=sys.stderr)
        sys.exit(1)

    # 上传到 GitHub Release(若不存在则创建)
    repo = "hummingbird136/PhotoTidy"
    print(f"\n上传到 {repo} release {tag} ...")
    cmd = ["gh", "release", "upload", tag, *artifacts,
           "--repo", repo, "--clobber"]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        # release 不存在,创建它
        print("release 不存在,创建中...")
        run(["gh", "release", "create", tag, *artifacts,
             "--repo", repo, "--title", tag,
             "--notes", f"PhotoTidy {tag}"])
    else:
        print("上传完成")

    print(f"\n✅ 产物已发布: https://github.com/{repo}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
