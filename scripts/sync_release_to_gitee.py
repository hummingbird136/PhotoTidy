#!/usr/bin/env python3
"""把 GitHub Actions 构建产物同步到 Gitee Release(GitHub Actions 中运行)。

用法:
    python scripts/sync_release_to_gitee.py

前置:
    - 环境变量 GITEE_TOKEN:Gitee 私人令牌(projects 权限)
    - 环境变量 GITEE_RELEASE_TAG:要同步的 tag(如 latest 或 v0.1.0)
    - 待上传文件位于工作目录 assets/ 下(由 actions/download-artifact 下载)

与第三方 action(release-sync)不同,这里直接调用 Gitee 官方 API:
    - 创建 release 时用 target_commitish=main,tag 不存在时由 Gitee 自动创建
      (第三方 action 手动 POST /tags 建 tag,参数与官方 API 不符,必然 400)
"""

import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

GITEE_API = "https://gitee.com/api/v5"
GITEE_REPO = "hummingbird136/PhotoTidy"
GITEE_TARGET_COMMITISH = "main"  # Gitee 仓库默认分支,release 关联的 commit
ARTIFACTS_DIR = Path("assets")


def gitee_request(method: str, url: str, data: dict | None = None) -> dict:
    """请求 Gitee API,自动带 access_token。"""
    token = os.environ["GITEE_TOKEN"]
    headers = {}
    if data:
        data = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}access_token={urllib.parse.quote(token)}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Gitee API {method} {url} 失败: {e.code} {body}") from e


def gitee_upload_asset(release_id: int, file_path: Path) -> None:
    """上传单个资产到 release,multipart/form-data。"""
    token = os.environ["GITEE_TOKEN"]
    boundary = uuid.uuid4().hex
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    parts = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="access_token"\r\n')
    parts.append(b"\r\n")
    parts.append(token.encode() + b"\r\n")
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode()
    )
    parts.append(f"Content-Type: {content_type}\r\n".encode())
    parts.append(b"\r\n")
    parts.append(file_path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    url = f"{GITEE_API}/repos/{GITEE_REPO}/releases/{release_id}/attach_files"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"上传 {file_path.name} 失败: {e.code} {e.read().decode()}") from e


def main() -> int:
    tag = os.environ.get("GITEE_RELEASE_TAG", "latest")

    files = sorted(
        f for f in ARTIFACTS_DIR.rglob("*") if f.is_file()
    )
    if not files:
        print(f"错误:{ARTIFACTS_DIR} 下没有可上传的文件", file=sys.stderr)
        return 1

    print(f"待上传 {len(files)} 个文件到 tag={tag}:")
    for f in files:
        print(f"  - {f.name} ({f.stat().st_size // 1024} KB)")

    # 1) 查找是否已有同名 release(列表接口;tags/ 接口对不存在返回 null,不可靠)
    print(f"\n检查 Gitee release {tag} ...")
    releases = gitee_request(
        "GET",
        f"{GITEE_API}/repos/{GITEE_REPO}/releases?per_page=100",
    )
    existing = next((r for r in releases if r.get("tag_name") == tag), None)

    if existing:
        release_id = existing["id"]
        print(f"已有 release(id={release_id}),更新并清理旧资产 ...")
        for asset in existing.get("assets", []):
            try:
                gitee_request(
                    "DELETE",
                    f"{GITEE_API}/repos/{GITEE_REPO}/releases/{release_id}/assets/"
                    f"{asset['id']}",
                )
                print(f"  删除旧资产 {asset['name']}")
            except Exception as e:
                print(f"  删除旧资产失败(可忽略): {e}")
    else:
        # 2) 不存在则创建;tag 由 Gitee 基于 target_commitish 自动创建
        print(f"没有该 release,创建中(target_commitish={GITEE_TARGET_COMMITISH}) ...")
        created = gitee_request(
            "POST",
            f"{GITEE_API}/repos/{GITEE_REPO}/releases",
            data={
                "tag_name": tag,
                "target_commitish": GITEE_TARGET_COMMITISH,
                "name": tag,
                "body": "PhotoTidy 自动构建产物,同步自 GitHub Actions。",
                "prerelease": "false",
            },
        )
        release_id = created["id"]
        print(f"已创建 release,id={release_id}")

    # 3) 逐个上传资产
    for f in files:
        print(f"\n上传 {f.name} ...")
        gitee_upload_asset(release_id, f)
        print("  ✓ 成功")

    print(f"\n✅ 同步完成:https://gitee.com/{GITEE_REPO}/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
