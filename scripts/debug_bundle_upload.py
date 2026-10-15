#!/usr/bin/env python3
"""HTML bundle 上传诊断脚本：全程打印接口输入输出参数，供乐享团队排查。

构造一个最小测试 HTML（含 1 张相对路径 PNG），打包 ZIP bundle
（index.html + assets/pic.png），走标准三步上传流程，每一步打印：

  1. file_apply_upload  —— 完整入参 JSON + 完整返回 JSON
  2. HTTP PUT           —— 预签名 URL + Content-Type + HTTP 状态码 + 字节数
  3. file_commit_upload —— 完整入参 JSON + 完整返回 JSON（失败时提取 request_id）

同时打印 MCP endpoint、初始化请求、zip 内容清单。所有输出同步写入
日志文件（默认 debug-bundle-log.txt），凭证 token 自动脱敏（只显示前 8 位）。

用法：
  python3 scripts/debug_bundle_upload.py --parent-entry-id <目录entry_id> \
      --profile csig
  python3 scripts/debug_bundle_upload.py --target-url "https://.../pages/<id>?..." \
      --profile myorg --keep --out my-debug.txt

  --keep    上传成功后保留测试条目（默认自动删除，避免留垃圾）
  --zip 20  可选：用随机数据把 zip 撑到指定 MB 数，测试大小限制场景

仅依赖 Python 3 标准库；凭证复用 html_import 的加载逻辑
（config/credentials.json 或 config/profiles/<name>.json，含 mcp_base_url 支持）。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from html_import import (  # noqa: E402
    MCPError,
    PersonalCredential,
    load_credential,
    package_html_bundle,
    parse_lexiang_url,
)

VERSION = "0.1.0"

# 最小 1x1 红色 PNG（base64 直解，零外部依赖）
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc000000301010018dd8db00000000049"
    "454e44ae426082"
)

TEST_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>bundle诊断-最小测试</title></head>
<body>
<h1>HTML bundle 上传诊断</h1>
<p>由 debug_bundle_upload.py 生成：index.html + assets/pic.png（1x1 PNG）。</p>
<p><img src="assets/pic.png" alt="测试图片"></p>
</body>
</html>
"""


class Tee:
    """同时打印到终端和日志文件。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.buffer = io.StringIO()

    def __call__(self, *args) -> None:
        line = " ".join(str(a) for a in args)
        print(line)
        self.buffer.write(line + "\n")

    def flush(self) -> None:
        self.path.write_text(self.buffer.getvalue(), encoding="utf-8")


def mask(token: str) -> str:
    return f"{token[:8]}...{token[-4:]}（长度 {len(token)}）" if token else "(空)"


def section(log: Tee, title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


class DebugMCP:
    """精简 JSON-RPC 客户端：每个请求前后打印完整 payload 与原始响应。"""

    def __init__(self, credential: PersonalCredential, log: Tee) -> None:
        self.credential = credential
        self.log = log
        self.session_id = ""
        base = credential.resolved_mcp_base_url
        parsed = urllib.parse.urlparse(base)
        query = {
            k: v[0]
            for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()
            if v
        }
        query.setdefault("preset", "all")
        query["company_from"] = credential.company_from
        self.url = parsed._replace(query=urllib.parse.urlencode(query)).geturl()
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {credential.mcp_token}",
        }

    def call(self, method: str, params: dict, *, is_tool: bool = True) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": method,
            "params": params,
        }
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        self.log(f"\n>>> 请求 [{method}]")
        self.log(f"    URL    : {self.url}")
        self.log(f"    Headers: Authorization=Bearer {mask(self.credential.mcp_token)}"
                 + (f", Mcp-Session-Id={self.session_id}" if self.session_id else ""))
        self.log("    Payload:")
        for line in json.dumps(payload, ensure_ascii=False, indent=2).splitlines():
            self.log(f"      {line}")

        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read().decode("utf-8", errors="replace")
                http_status = response.status
                new_session = response.headers.get("Mcp-Session-Id", "")
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            http_status = error.code
            new_session = ""

        self.log(f"<<< 响应 HTTP {http_status}")
        if new_session:
            self.log(f"    Mcp-Session-Id: {new_session}")
            self.session_id = new_session
        # 原始响应（可能是 SSE data: 行，全部打出来）
        self.log("    Raw:")
        for line in raw.splitlines():
            if line.strip():
                self.log(f"      {line}")

        # 解析 JSON（兼容 SSE data: 包裹）
        body = None
        for candidate in (raw.strip(), *[l[5:].strip() for l in raw.splitlines()
                                         if l.strip().startswith("data:")]):
            if not candidate or candidate == "[DONE]":
                continue
            try:
                body = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        if body is None:
            raise MCPError(f"响应无法解析为 JSON：{raw[:500]}")
        return body

    def tool(self, name: str, arguments: dict) -> dict:
        """调用业务工具（直呼，preset=all 下可用）。"""
        body = self.call("tools/call", {"name": name, "arguments": arguments})
        # JSON-RPC 响应外层 {jsonrpc, id, result}，业务内容在 result 里
        result = body.get("result", body)
        if result.get("isError"):
            content = result.get("content", [])
            raise MCPError(content[0].get("text", "unknown") if content else "unknown")
        content = result.get("content", [])
        text = content[0].get("text", "") if content else ""
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"raw_text": text}


def extract_request_id(value: dict) -> str:
    text = json.dumps(value, ensure_ascii=False)
    import re
    m = re.search(r'"request_id"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else ""


def build_test_bundle(dest: Path, target_mb: float) -> Path:
    """生成最小测试 HTML + 资源，打包 bundle zip。target_mb>0 时追加随机大文件。"""
    staging = dest / "staging"
    (staging / "assets").mkdir(parents=True, exist_ok=True)
    (staging / "index.html").write_text(TEST_HTML, encoding="utf-8")
    (staging / "assets" / "pic.png").write_bytes(TINY_PNG)
    if target_mb > 0:
        import os
        (staging / "assets" / "big.bin").write_bytes(os.urandom(int(target_mb * 1024 * 1024)))
    zip_path = dest / "debug-bundle-test.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in sorted(staging.rglob("*")):
            if fp.is_file():
                zf.write(fp, fp.relative_to(staging).as_posix())
    return zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="debug_bundle_upload.py",
        description="HTML bundle 上传诊断：打印三步上传的全部接口入参/出参",
    )
    parser.add_argument("--parent-entry-id", help="目标父节点 entry_id")
    parser.add_argument("--target-url", help="乐享目标目录 URL（自动解析 entry_id）")
    parser.add_argument("--profile", help="凭证 profile（如 csig）")
    parser.add_argument("--credential-file", help="凭证文件路径")
    parser.add_argument("--mcp-base-url", help="覆盖 MCP 接口地址（调试用）")
    parser.add_argument("--keep", action="store_true",
                        help="上传成功后保留测试条目（默认自动删除）")
    parser.add_argument("--zip-mb", type=float, default=0,
                        help="用随机数据把 zip 撑到指定 MB（测试大小限制场景）")
    parser.add_argument("--out", default="debug-bundle-log.txt", help="日志输出文件")
    args = parser.parse_args(argv)

    parent_entry_id = args.parent_entry_id or ""
    if args.target_url:
        parent_entry_id = parent_entry_id or parse_lexiang_url(args.target_url)
    if not parent_entry_id:
        print("错误：需要 --parent-entry-id 或 --target-url", file=sys.stderr)
        return 2

    out_path = Path(args.out).resolve()
    log = Tee(out_path)

    section(log, "HTML bundle 上传诊断")
    log(f"时间       : {__import__('datetime').datetime.now().isoformat()}")
    log(f"脚本版本   : v{VERSION}  (html_import 凭证/打包逻辑)")
    log(f"日志文件   : {out_path}")
    log(f"目标父节点 : {parent_entry_id}")
    log(f"zip 目标大小: {args.zip_mb or '最小（~1KB）'} MB")

    # ---- 凭证 ----
    section(log, "步骤 0：加载凭证")
    try:
        cred = load_credential(args.profile, args.credential_file)
    except Exception as error:
        log(f"凭证加载失败：{error}")
        log.flush()
        return 3
    if args.mcp_base_url:
        cred.mcp_base_url = args.mcp_base_url
    log(f"凭证来源   : profile={args.profile or 'default'}")
    log(f"mcp_token  : {mask(cred.mcp_token)}")
    log(f"company_from: {cred.company_from}")
    log(f"mcp_base_url: {cred.resolved_mcp_base_url}")

    client = DebugMCP(cred, log)

    # ---- 打包 ----
    section(log, "步骤 1：构造测试 bundle（index.html + assets/pic.png）")
    with tempfile.TemporaryDirectory(prefix="lxdebug_") as tmp:
        zip_path = build_test_bundle(Path(tmp), args.zip_mb)
        zip_size = zip_path.stat().st_size
        log(f"zip 路径   : {zip_path}")
        log(f"zip 大小   : {zip_size} bytes ({zip_size / 1024:.1f} KB)")
        log("zip 内容   :")
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                log(f"  {info.filename}  {info.file_size} bytes")

        # ---- MCP initialize ----
        section(log, "步骤 2：MCP initialize")
        try:
            init_result = client.call("initialize", {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "debug-bundle-upload", "version": VERSION},
            })
            log(f"initialize 结果 keys: {list(init_result.get('result', init_result).keys())}")
        except Exception as error:
            log(f"initialize 失败：{error}")
            log.flush()
            return 3

        # ---- apply ----
        section(log, "步骤 3：file_apply_upload（申请上传凭证）")
        apply_args = {
            "parent_entry_id": parent_entry_id,
            "name": "debug-bundle-test.zip",
            "size": str(zip_size),
            "mime_type": "application/zip",
            "extension": "html",
            "upload_type": "PRE_SIGNED_URL",
        }
        log("（入参见下方 Payload；extension=html 标记 HTML bundle）")
        try:
            apply_result = client.tool("file_apply_upload", apply_args)
        except MCPError as error:
            log(f"file_apply_upload 失败：{error}")
            log.flush()
            return 1
        session = apply_result.get("data", {}).get("session", {})
        upload_url = ""
        objects = session.get("objects", [])
        if objects and isinstance(objects[0], dict):
            upload_url = objects[0].get("upload_url", "")
        session_id = session.get("session_id", "")
        if not (upload_url and session_id):
            log("⚠️ 未解析到 upload_url / session_id，请检查上方原始返回")
            log.flush()
            return 1
        log(f"\n✓ 解析：session_id={session_id}")
        log(f"  upload_url={upload_url[:100]}...")

        # ---- PUT ----
        section(log, "步骤 4：HTTP PUT zip 到预签名 URL")
        data = zip_path.read_bytes()
        put_request = urllib.request.Request(
            upload_url, data=data,
            headers={"Content-Type": "application/zip",
                     "Content-Length": str(len(data))},
            method="PUT",
        )
        try:
            with urllib.request.urlopen(put_request, timeout=300) as response:
                log(f"PUT URL    : {upload_url}")
                log(f"PUT 大小   : {len(data)} bytes")
                log(f"PUT 响应   : HTTP {response.status}")
                put_ok = response.status in (200, 201, 204)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")[:500]
            log(f"PUT URL    : {upload_url}")
            log(f"PUT 失败   : HTTP {error.code} {body}")
            log.flush()
            return 1
        if not put_ok:
            log("PUT 状态码异常")
            log.flush()
            return 1

        # ---- commit ----
        section(log, "步骤 5：file_commit_upload（确认上传，触发导入任务）")
        try:
            commit_result = client.tool("file_commit_upload", {"session_id": session_id})
        except MCPError as error:
            section(log, "❌ file_commit_upload 失败（导入任务失败）")
            log(f"错误信息: {error}")
            rid = extract_request_id({"t": str(error)})
            if rid:
                log(f"request_id: {rid}")
            log(f"\n日志已保存：{out_path}（可直接发给乐享团队排查）")
            log.flush()
            return 1

        entry = commit_result.get("data", {}).get("entry", {})
        entry_id = entry.get("id", "")
        section(log, "✅ 三步上传全部成功")
        log(f"entry_id  : {entry_id}")
        log(f"entry_name: {entry.get('name', '')}")
        log(f"预览链接  : https://lexiangla.com/pages/{entry_id}（按企业域名替换）")
        log("注意：导入为异步任务，status 可能先为 processing，稍后变 finished")

        # ---- 清理 ----
        if args.keep:
            section(log, "保留测试条目（--keep）")
        else:
            section(log, "步骤 6：删除测试条目（默认清理，--keep 可保留）")
            try:
                del_result = client.tool("entry_delete_entry", {"entry_id": entry_id})
                log(f"删除结果: code={del_result.get('code', '?')}")
            except MCPError as error:
                log(f"删除失败（需手动清理 entry {entry_id}）：{error}")

    log(f"\n日志已保存：{out_path}（可直接发给乐享团队排查）")
    log.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
