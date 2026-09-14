#!/usr/bin/env python3
"""把 GitHub Actions 构建产物同步到 Gitee Release(GitHub Actions 中运行)。

用法:
    python scripts/sync_release_to_gitee.py

前置:
    - 环境变量 GITEE_TOKEN:Gitee 私人令牌(projects 权限)
    - 环境变量 GITEE_RELEASE_TAG:要同步的 tag(如 latest 或 v0.1.0)
    - 待上传文件位于工作目录 assets/ 下(由 actions/download-artifact 下载)

直接调用 Gitee 官方 API:创建 release 时用 target_commitish=main,
tag 不存在时由 Gitee 自动创建。

健壮性设计:
    - 所有请求带超时,避免跨境网络卡死时挂到 CI 的 6 小时上限
    - 网络错误 / 408 / 429 / 5xx 指数退避重试最多 4 次
    - 任何写入日志的内容都经过 access_token 脱敏,防止令牌随错误信息泄露
"""

import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

GITEE_API = "https://gitee.com/api/v5"
GITEE_REPO = "hummingbird136/PhotoTidy"
GITEE_TARGET_COMMITISH = "main"  # Gitee 仓库默认分支,release 关联的 commit
ARTIFACTS_DIR = Path("assets")

TIMEOUT_API = 60      # 普通 API 请求(秒)
TIMEOUT_UPLOAD = 600  # 附件上传(秒,跨境大文件)
MAX_ATTEMPTS = 4
_RETRIABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_TOKEN_RE = re.compile(r"(access_token=)[^&\s\"']+", re.IGNORECASE)


def redact(text: str) -> str:
    """抹掉文本里的 access_token,防止令牌写入 CI 日志。"""
    return _TOKEN_RE.sub(r"\1***", text)


def open_with_retry(req: urllib.request.Request, timeout: int):
    """带超时与指数退避重试的 urlopen。4xx(除可重试状态码外)立即抛错。"""
    last_err: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in _RETRIABLE_STATUS or attempt == MAX_ATTEMPTS - 1:
                raise
        except (urllib.error.URRError, TimeoutError, OSError) as e:
            last_err = e
            if attempt == MAX_ATTEMPTS - 1:
                raise
        delay = 2**attempt
        code = getattr(last_err, "code", "网络错误")
        print(f"  请求失败({code}),{delay}s 后重试({attempt + 1}/{MAX_ATTEMPTS - 1}) ...")
        time.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover


def gitee_request(method: str, url: str, data: dict | None = None) -> dict:
    """请求 Gitee API,自动带 access_token。

    Gitee 规定:POST/PUT 等带 body 的请求,access_token 放表单字段;
    GET/DELETE 等无 body 的请求,access_token 放 query string。
    """
    token = os.environ["GITEE_TOKEN"]
    headers = {}
    if data is not None:
        payload = {**data, "access_token": token}
        body = urllib.parse.urlencode(payload).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
    else:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}access_token={urllib.parse.quote(token)}"
        req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with open_with_retry(req, TIMEOUT_API) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        # url 可能含 query 形式的 token,响应体也脱敏后再抛
        detail = redact(e.read().decode(errors="replace"))
        raise RuntimeError(f"Gitee API {method} {redact(url)} 失败: {e.code} {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Gitee API {method} {redact(url)} 网络错误: {e}") from e


def gitee_upload_asset(release_id: int, file_path: Path) -> None:
    """上传单个资产到 release,multipart/form-data(token 放在表单而非 URL)。"""
    token = os.environ["GITEE_TOKEN"]
    boundary = uuid.uuid4().hex
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="access_token"\r\n',
        b"\r\n",
        token.encode() + b"\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n".encode(),
        b"\r\n",
        file_path.read_bytes() + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)

    url = f"{GITEE_API}/repos/{GITEE_REPO}/releases/{release_id}/attach_files"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with open_with_retry(req, TIMEOUT_UPLOAD) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        detail = redact(e.read().decode(errors="replace"))
        raise RuntimeError(f"上传 {file_path.name} 失败: {e.code} {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"上传 {file_path.name} 网络错误: {e}") from e


def main() -> int:
    tag = os.environ.get("GITEE_RELEASE_TAG", "latest")

    files = sorted(f for f in ARTIFACTS_DIR.rglob("*") if f.is_file())
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
                print(f"  删除旧资产失败(可忽略): {redact(str(e))}")
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

    # 3) 逐个上传资产(单个失败不影响其余文件,最后汇总)
    failed = []
    for f in files:
        print(f"\n上传 {f.name} ...")
        try:
            gitee_upload_asset(release_id, f)
            print("  ✓ 成功")
        except Exception as e:
            failed.append(f.name)
            print(f"  ✗ {redact(str(e))}", file=sys.stderr)

    if failed:
        print(f"\n⚠️ {len(failed)} 个文件上传失败: {', '.join(failed)}", file=sys.stderr)
        print("可重跑本工作流,已成功的文件会随「先删后传」逻辑自动清理重建。", file=sys.stderr)
        return 1

    print(f"\n✅ 同步完成:https://gitee.com/{GITEE_REPO}/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
