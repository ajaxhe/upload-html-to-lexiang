#!/usr/bin/env python3
"""Upload HTML files (with local relative-path resources) to Tencent Lexiang.

Resolves the core pain point: HTML referencing local images/assets via relative
paths cannot be imported as a standalone file (resources lost). This tool
packages each HTML + its resources into a ZIP bundle (main file renamed to
index.html), then drives the standard three-step Lexiang file upload flow:

  1. file_apply_upload(extension=html, upload_type=PRE_SIGNED_URL)
     -> returns presigned upload_url + session_id
  2. HTTP PUT the ZIP to the presigned URL
  3. file_commit_upload(session_id) -> confirms upload, creates entry

The extension=html parameter tells Lexiang that this zip is an HTML bundle
(not a regular zip file), so relative-path resources inside are resolved
against index.html at the archive root.

Credentials are stored in ~/.config/lexiang-upload/ (JSON format:
{"mcp_token": "lxmcp_xxx", "company_from": "xxx"}), obtained from
https://lexiangla.com/ai/claw.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

VERSION = "0.3.0"
CLI_API = "1"
CREDENTIAL_HELP_URL = "https://lexiangla.com/ai/claw"
MCP_BASE_URL = "https://mcp.lexiang-app.com/mcp"
DEFAULT_CREDENTIALS = Path("~/.config/lexiang-upload/credentials.json").expanduser()
MANIFEST_PATH = Path("~/.config/lexiang-upload/manifests/html-import.json").expanduser()
PROFILE_CREDENTIALS_DIR = Path("~/.config/lexiang-upload/profiles").expanduser()
PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
class AuthError(RuntimeError):
    pass


class MCPError(RuntimeError):
    pass


@dataclass
class PersonalCredential:
    mcp_token: str
    company_from: str

    @classmethod
    def from_dict(cls, data: dict) -> "PersonalCredential":
        candidates = [data]
        candidates.extend(
            value
            for key in ("mcp", "credential", "credentials", "auth")
            if isinstance((value := data.get(key)), dict)
        )

        def pick(*keys: str) -> str:
            for candidate in candidates:
                for key in keys:
                    if candidate.get(key):
                        return str(candidate[key]).strip()
            return ""

        return cls(
            mcp_token=pick("mcp_token", "access_token", "token", "LEXIANG_TOKEN"),
            company_from=pick("company_from", "mcp_company_from"),
        )

    @classmethod
    def from_text(cls, raw: str) -> "PersonalCredential":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return cls.from_dict(value)
        token_match = re.search(r"\blxmcp_[A-Za-z0-9_-]+\b", raw)
        company_match = re.search(
            r"(?:[?&\s\"']|^)company_from[=\s]+([A-Za-z0-9_-]+)", raw
        )
        return cls(
            mcp_token=token_match.group(0) if token_match else "",
            company_from=(
                urllib.parse.unquote(company_match.group(1))
                if company_match
                else ""
            ),
        )

    def validate_shape(self) -> None:
        missing = [
            label
            for label, value in (
                ("mcp_token", self.mcp_token),
                ("company_from", self.company_from),
            )
            if not value
        ]
        if missing:
            raise AuthError(f"凭证缺少字段：{', '.join(missing)}")


def resolve_credential_file(profile: str | None, credential_file: str | None) -> Path:
    if credential_file:
        return Path(credential_file).expanduser()
    if profile:
        if not PROFILE_RE.match(profile):
            raise AuthError(f"profile 名非法：{profile}")
        return PROFILE_CREDENTIALS_DIR / f"{profile}.json"
    return DEFAULT_CREDENTIALS


def load_credential(profile: str | None, credential_file: str | None) -> PersonalCredential:
    path = resolve_credential_file(profile, credential_file)
    if not path.exists():
        raise AuthError(
            f"凭证文件不存在：{path}\n"
            f"请访问 {CREDENTIAL_HELP_URL} 获取凭证，"
            f"然后执行：\n"
            f"  mkdir -p ~/.config/lexiang-upload && "
            f"cat > ~/.config/lexiang-upload/credentials.json << 'EOF'\n"
            f'  {{"mcp_token": "lxmcp_你的token", "company_from": "你的企业标识"}}\n'
            f"  EOF\n"
            f"  chmod 600 ~/.config/lexiang-upload/credentials.json"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise AuthError(f"凭证文件 JSON 解析失败：{error}") from error
    cred = PersonalCredential.from_dict(data)
    cred.validate_shape()
    try:
        if path.stat().st_mode & 0o077:
            path.chmod(0o600)
    except OSError:
        pass
    return cred


# --------------------------------------------------------------------------- #
# MCP client (JSON-RPC 2.0 over HTTP, shared session)
# --------------------------------------------------------------------------- #
class MCPClient:
    def __init__(self, credential: PersonalCredential, retries: int = 3) -> None:
        self.credential = credential
        self.retries = retries
        self.access_token = credential.mcp_token
        self.session_id = ""
        self._initialize()

    @property
    def url(self) -> str:
        return f"{MCP_BASE_URL}?company_from={urllib.parse.quote(self.credential.company_from)}"

    @staticmethod
    def _parse_transport(raw: str) -> dict:
        candidates = [raw.strip()]
        candidates.extend(
            line[5:].strip()
            for line in raw.splitlines()
            if line.strip().startswith("data:")
        )
        for candidate in candidates:
            if not candidate or candidate == "[DONE]":
                continue
            try:
                outer = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if "error" in outer:
                raise MCPError(f"MCP JSON-RPC error: {outer['error']}")
            result = outer.get("result", outer)
            if isinstance(result, dict) and result.get("isError"):
                content = result.get("content", [])
                message = (
                    content[0].get("text", "unknown error")
                    if content
                    else "unknown error"
                )
                raise MCPError(message)
            return result
        raise MCPError("MCP 返回无法解析")

    def _post(self, payload: dict, *, initialize: bool = False) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.access_token}",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        last_error: Exception | None = None
        for attempt in range(self.retries):
            request = urllib.request.Request(
                self.url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    if initialize:
                        self.session_id = response.headers.get("Mcp-Session-Id", "")
                    return self._parse_transport(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                if error.code == 401:
                    raise AuthError(
                        f"乐享凭证无效或已过期（401）。请访问 {CREDENTIAL_HELP_URL} 续期"
                    ) from error
                if error.code == 403 and "WAF" in detail:
                    raise MCPError(
                        "乐享 WAF 拦截（403）。参数可能含敏感模式。"
                        "本工具走 zip 文件上传，正常不应触发；请检查参数。"
                    ) from error
                if error.code < 500 or attempt == self.retries - 1:
                    raise MCPError(f"MCP HTTP {error.code}: {detail[:500]}") from error
                last_error = error
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                if attempt == self.retries - 1:
                    break
            time.sleep(2**attempt)
        raise MCPError(f"MCP 网络请求失败，已重试 {self.retries} 次：{last_error}")

    def _initialize(self) -> None:
        self._post(
            {
                "jsonrpc": "2.0",
                "id": uuid.uuid4().hex,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "html-import", "version": VERSION},
                },
            },
            initialize=True,
        )

    def _request(self, tool: str, arguments: dict) -> dict:
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": uuid.uuid4().hex,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )

    def json(self, tool: str, arguments: dict) -> dict:
        result = self._request(tool, arguments)
        content = result.get("content", [])
        text = content[0].get("text", "") if content else ""
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise MCPError(f"{tool} 应返回 JSON，实际为：{text[:500]}") from error
        if isinstance(value, dict) and value.get("code", 0) != 0:
            raise MCPError(f"{tool}: {value.get('message', value)}")
        return value


# --------------------------------------------------------------------------- #
# HTML resource detection
# --------------------------------------------------------------------------- #
class _ResourceCollector(HTMLParser):
    """Collect relative-path resource references from an HTML document.

    Only local relative references are kept; absolute URLs, data: URIs,
    anchors, mailto/tel/javascript schemes are filtered out.
    """

    SINGLE_ATTR = {
        "img": ("src",),
        "script": ("src",),
        "link": ("href",),
        "source": ("src",),
        "video": ("src", "poster"),
        "audio": ("src",),
        "track": ("src",),
        "embed": ("src",),
        "input": ("src",),
        "object": ("data",),
    }
    SRCSET_TAGS = {"img", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.resources: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v or "") for k, v in attrs}
        tag_lower = tag.lower()

        for attr in self.SINGLE_ATTR.get(tag_lower, ()):
            value = attr_map.get(attr, "")
            if value:
                self._add(value)

        if tag_lower in self.SRCSET_TAGS:
            srcset = attr_map.get("srcset", "")
            if srcset:
                for chunk in srcset.split(","):
                    url = chunk.strip().split(" ")[0].strip()
                    if url:
                        self._add(url)

        if tag_lower == "a":
            href = attr_map.get("href", "")
            if href:
                self._add(href)

    def _add(self, raw: str) -> None:
        url = raw.strip()
        if not url:
            return
        lower = url.lower()
        if lower.startswith(("http://", "https://", "//", "data:", "#")):
            return
        if lower.startswith(("mailto:", "tel:", "javascript:", "blob:")):
            return
        path_part = url.split("#", 1)[0].split("?", 1)[0]
        if not path_part:
            return
        self.resources.add(path_part)


def detect_resources(html_path: Path) -> list[str]:
    """Return de-duplicated list of relative resource paths referenced in html."""
    try:
        text = html_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    parser = _ResourceCollector()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        pass
    return sorted(parser.resources)


def resolve_resource(html_path: Path, ref: str) -> Path | None:
    """Resolve a relative ref against the html file dir; return Path if exists."""
    cleaned = ref.lstrip("./").lstrip(".\\")
    candidate = (html_path.parent / cleaned).resolve()
    if candidate.is_file():
        return candidate
    return None


def extract_title(html_path: Path) -> str:
    try:
        text = html_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return html_path.stem
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if match:
        title = match.group(1).strip()
        if title:
            return title
    match = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.IGNORECASE | re.DOTALL)
    if match:
        title = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        if title:
            return title
    return html_path.stem


# --------------------------------------------------------------------------- #
# ZIP bundle packaging
# --------------------------------------------------------------------------- #
@dataclass
class BundleResult:
    zip_path: Path
    size: int
    index_name: str
    content_hash: str
    included_resources: list[str]
    degraded_resources: list[str]
    missing_resources: list[str]
    file_count: int


def package_html_bundle(html_path: Path, dest_dir: Path, zip_name: str | None = None) -> BundleResult:
    """Build a ZIP bundle containing index.html + all relative-path resources.

    The original html is copied as index.html at the zip root; every detected
    relative resource is placed at its original relative path so that links
    remain valid inside the archive. Missing resources (file not found) are
    degraded to a placeholder notice inside index.html so the document is still
    usable. The extension=html flag tells Lexiang to treat this as an HTML
    bundle (not a regular zip).

    zip_name controls the zip file name (without .zip extension). It defaults
    to the html file stem. This name is also passed to file_apply_upload as
    the entry name, so it determines the displayed document title in Lexiang.
    """
    base_name = zip_name or html_path.stem
    resources_refs = detect_resources(html_path)
    included: list[str] = []
    degraded: list[str] = []
    missing: list[str] = []

    # content_hash over source files (deterministic across re-runs, unlike zip bytes)
    # hash = sha256(html_content + each resource file bytes)
    hasher = hashlib.sha256()

    staging = Path(tempfile.mkdtemp(prefix="lxhtml_"))
    try:
        # read original html; we may rewrite resource refs for missing ones
        html_content = html_path.read_text(encoding="utf-8", errors="replace")
        hasher.update(html_content.encode("utf-8"))

        # main html renamed to index.html (Lexiang requires this for bundles)
        index_path = staging / "index.html"

        for ref in resources_refs:
            src = resolve_resource(html_path, ref)
            if src is None:
                # degrade missing resource to a visible placeholder notice
                placeholder = (
                    f'[资源缺失：{ref} — 源文件不存在，已跳过。请补充后重新上传。]'
                )
                html_content = html_content.replace(ref, placeholder)
                degraded.append(ref)
                continue
            rel = ref.lstrip("./").lstrip(".\\").replace("\\", "/")
            dst = staging / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            included.append(ref)
            # include resource file bytes in content_hash
            hasher.update(src.read_bytes())

        index_path.write_text(html_content, encoding="utf-8")

        zip_path = dest_dir / f"{base_name}.zip"
        file_count = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(staging):
                for name in files:
                    fp = Path(root) / name
                    arcname = fp.relative_to(staging).as_posix()
                    zf.write(fp, arcname)
                    file_count += 1

        content_hash = hasher.hexdigest()

        return BundleResult(
            zip_path=zip_path,
            size=zip_path.stat().st_size,
            index_name="index.html",
            content_hash=content_hash,
            included_resources=included,
            degraded_resources=degraded,
            missing_resources=missing,
            file_count=file_count,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Manifest: idempotency + incremental + resume (principle 3)
# --------------------------------------------------------------------------- #
class Manifest:
    """Persisted state mapping source path -> upload record.

    Enables idempotent re-runs (skip unchanged), incremental updates
    (re-upload changed), and resume after interruption (state survives
    across sessions). State follows the source file, not the session.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self._data, dict):
                    self._data = {}
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def get(self, source: str) -> dict | None:
        return self._data.get(source)

    def is_unchanged(self, source: str, content_hash: str) -> bool:
        record = self.get(source)
        return bool(record and record.get("content_hash") == content_hash)

    def upsert(self, source: str, content_hash: str, entry_id: str, entry_name: str) -> None:
        self._data[source] = {
            "content_hash": content_hash,
            "entry_id": entry_id,
            "entry_name": entry_name,
            "uploaded_at": int(time.time()),
        }
        self._save()

    def remove(self, source: str) -> None:
        if source in self._data:
            del self._data[source]
            self._save()


# --------------------------------------------------------------------------- #
# Presigned URL upload
# --------------------------------------------------------------------------- #
def put_to_presigned_url(upload_url: str, file_path: Path, mime_type: str) -> None:
    """HTTP PUT file content to a presigned COS URL (no signature needed)."""
    data = file_path.read_bytes()
    request = urllib.request.Request(
        upload_url,
        data=data,
        headers={
            "Content-Type": mime_type,
            "Content-Length": str(len(data)),
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            if response.status not in (200, 201, 204):
                body = response.read().decode("utf-8", errors="replace")[:500]
                raise MCPError(f"COS PUT 失败：HTTP {response.status} {body}")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:500]
        raise MCPError(f"COS PUT HTTP {error.code}: {body}") from error


# --------------------------------------------------------------------------- #
# Lexiang upload flow
# --------------------------------------------------------------------------- #
def _extract_session(result: dict) -> tuple[str, str]:
    """Extract (upload_url, session_id) from file_apply_upload result."""
    session = result.get("data", {}).get("session", {})
    session_id = session.get("session_id", "")
    objects = session.get("objects", [])
    upload_url = ""
    if isinstance(objects, list) and objects:
        upload_url = objects[0].get("upload_url", "") if isinstance(objects[0], dict) else ""
    if not upload_url or not session_id:
        raise MCPError(
            f"file_apply_upload 未返回 upload_url 或 session_id，"
            f"完整返回：{json.dumps(result, ensure_ascii=False)[:800]}"
        )
    return upload_url, session_id


def import_one_html(
    client: MCPClient,
    html_path: Path,
    parent_entry_id: str,
    name: str | None,
    dry_run: bool,
    manifest: Manifest | None = None,
    force: bool = False,
) -> dict:
    """Package one html (+resources) into a zip bundle and upload to Lexiang.

    Idempotency (principle 3): if manifest is provided and the source file's
    content_hash matches a previous upload, skip unless force=True.
    """
    source_key = str(html_path.resolve())
    title = name or extract_title(html_path)

    with tempfile.TemporaryDirectory(prefix="lxhtml_zip_") as tmp:
        bundle = package_html_bundle(html_path, Path(tmp), zip_name=title)

        # three-state classification: succeed / degraded / error
        has_degraded = bool(bundle.degraded_resources)
        status = "degraded" if has_degraded else "ok"

        record: dict = {
            "source": str(html_path),
            "title": title,
            "index_name": bundle.index_name,
            "content_hash": bundle.content_hash[:12],
            "zip_size": bundle.size,
            "file_count": bundle.file_count,
            "included_resources": bundle.included_resources,
            "degraded_resources": bundle.degraded_resources,
            "missing_resources": bundle.missing_resources,
            "status": "dry_run" if dry_run else status,
        }

        if dry_run:
            record["message"] = "dry-run 完成，未实际上传"
            return record

        # idempotency check: skip if unchanged and manifest has record
        if manifest and not force and manifest.is_unchanged(source_key, bundle.content_hash):
            prev = manifest.get(source_key)
            record["status"] = "skipped"
            record["entry_id"] = prev.get("entry_id", "")
            record["entry_name"] = prev.get("entry_name", "")
            record["message"] = "内容未变更，跳过上传（幂等）"
            return record

        # step 1: apply for upload (extension=html tells Lexiang this is an HTML bundle)
        apply_result = client.json(
            "file_apply_upload",
            {
                "parent_entry_id": parent_entry_id,
                "name": f"{title}.zip",
                "size": str(bundle.size),
                "mime_type": "application/zip",
                "extension": "html",
                "upload_type": "PRE_SIGNED_URL",
            },
        )
        upload_url, session_id = _extract_session(apply_result)
        record["session_id"] = session_id

        # step 2: PUT zip to presigned URL
        put_to_presigned_url(upload_url, bundle.zip_path, "application/zip")
        record["cos_uploaded"] = True

        # step 3: commit upload (creates the entry)
        commit_result = client.json(
            "file_commit_upload",
            {"session_id": session_id},
        )
        entry = commit_result.get("data", {}).get("entry", {})
        record["entry_id"] = entry.get("id", "")
        record["entry_name"] = entry.get("name", "")
        record["entry_type"] = entry.get("entry_type", "")
        record["status"] = "degraded" if has_degraded else "succeed"
        record["message"] = "上传成功" + ("（含降级资源）" if has_degraded else "")

        # persist to manifest for future idempotency
        if manifest and record.get("entry_id"):
            manifest.upsert(
                source_key,
                bundle.content_hash,
                record["entry_id"],
                record["entry_name"],
            )
        return record


# --------------------------------------------------------------------------- #
# URL parsing helper
# --------------------------------------------------------------------------- #
def parse_lexiang_url(url: str) -> str:
    """Extract entry_id from a lexiangla.com /pages/<id> URL."""
    parsed = urllib.parse.urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]
    for i, part in enumerate(path_parts):
        if part == "pages" and i + 1 < len(path_parts):
            return path_parts[i + 1]
    return ""


def _summarize(results: list[dict]) -> dict:
    """Build a three-state summary: succeed / degraded / failed (principle 2)."""
    succeed = [r for r in results if r.get("status") in ("succeed", "ok")]
    skipped = [r for r in results if r.get("status") == "skipped"]
    degraded = [r for r in results if r.get("status") == "degraded"]
    failed = [r for r in results if r.get("status") == "error"]
    all_degraded_resources: list[str] = []
    for r in degraded:
        all_degraded_resources.extend(r.get("degraded_resources", []))
    return {
        "total": len(results),
        "succeed": len(succeed),
        "skipped": len(skipped),
        "degraded": len(degraded),
        "failed": len(failed),
        "degraded_resources": all_degraded_resources,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="html_import.py",
        description="上传 HTML（含本地相对路径资源）到乐享知识库（HTML bundle 方式）。",
    )
    parser.add_argument(
        "source",
        help="源 HTML 文件路径或包含 .html 的目录",
    )
    parser.add_argument(
        "--parent-entry-id",
        help="目标乐享父节点 entry_id（从 /pages/<id> 链接提取）",
    )
    parser.add_argument(
        "--target-url",
        help="乐享目标目录 URL（自动解析 parent_entry_id，覆盖上面参数）",
    )
    parser.add_argument("--name", help="导入后的文件标题（默认取 HTML <title>）")
    parser.add_argument("--profile", help="凭证 profile（多账号时指定，如 myorg）")
    parser.add_argument("--credential-file", help="凭证文件路径")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打包检测不上传，输出将包含的资源清单",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新上传，忽略 manifest 幂等检查（默认未变更的会跳过）",
    )
    parser.add_argument(
        "--no-incremental",
        action="store_true",
        help="禁用 manifest 记录（不读写状态文件，每次都全量上传）",
    )
    parser.add_argument(
        "--manifest-path",
        help=f"manifest 文件路径（默认 {MANIFEST_PATH}）",
    )
    parser.add_argument("--json", action="store_true", help="stdout 只输出 JSON")
    parser.add_argument("--version", action="version", version=f"html_import {VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = (lambda msg: None) if args.json else (lambda msg: print(msg, file=sys.stderr))

    # resolve target
    parent_entry_id = args.parent_entry_id or ""
    if args.target_url:
        parent_entry_id = parent_entry_id or parse_lexiang_url(args.target_url)
    if not args.dry_run and not parent_entry_id:
        print(
            "错误：需要 --parent-entry-id 或 --target-url 指向乐享目录页",
            file=sys.stderr,
        )
        return 2

    # collect html files
    source = Path(args.source).expanduser()
    if source.is_file():
        if source.suffix.lower() != ".html":
            print(f"错误：{source} 不是 .html 文件", file=sys.stderr)
            return 2
        html_files = [source]
    elif source.is_dir():
        html_files = sorted(source.glob("*.html"))
        if not html_files:
            print(f"错误：目录 {source} 下没有 .html 文件", file=sys.stderr)
            return 2
    else:
        print(f"错误：{source} 不存在", file=sys.stderr)
        return 2

    log(f"发现 {len(html_files)} 个 HTML 文件")

    # dry run does not need credentials
    if args.dry_run:
        results = []
        for hf in html_files:
            with tempfile.TemporaryDirectory(prefix="lxhtml_zip_") as tmp:
                bundle = package_html_bundle(hf, Path(tmp))
            status = "degraded" if bundle.degraded_resources else "ok"
            results.append(
                {
                    "source": str(hf),
                    "title": extract_title(hf),
                    "index_name": bundle.index_name,
                    "content_hash": bundle.content_hash[:12],
                    "zip_size": bundle.size,
                    "file_count": bundle.file_count,
                    "included_resources": bundle.included_resources,
                    "degraded_resources": bundle.degraded_resources,
                    "missing_resources": bundle.missing_resources,
                    "status": status,
                }
            )
        summary = _summarize(results)
        print(json.dumps({"results": results, "summary": summary}, ensure_ascii=False, indent=2))
        return 0

    # load credential + init MCP
    try:
        cred = load_credential(args.profile, args.credential_file)
    except AuthError as error:
        print(f"凭证错误：{error}", file=sys.stderr)
        return 3
    log(f"使用凭证 profile={args.profile or 'default'}, company_from={cred.company_from}")

    try:
        client = MCPClient(cred)
    except (AuthError, MCPError) as error:
        print(f"MCP 初始化失败：{error}", file=sys.stderr)
        return 3

    # init manifest for idempotency (principle 3)
    manifest = None
    if not args.no_incremental:
        mpath = Path(args.manifest_path).expanduser() if args.manifest_path else MANIFEST_PATH
        manifest = Manifest(mpath)
        log(f"manifest: {mpath}")

    results = []
    exit_code = 0
    for hf in html_files:
        log(f"处理：{hf.name}")
        try:
            record = import_one_html(
                client, hf, parent_entry_id, args.name, args.dry_run,
                manifest=manifest, force=args.force,
            )
            results.append(record)
            eid = record.get("entry_id", "")
            if eid:
                log(
                    f"  -> {record['status']}: "
                    f"entry_id={eid[:16]}, "
                    f"resources={len(record.get('included_resources', []))}/"
                    f"{len(record.get('included_resources', [])) + len(record.get('degraded_resources', []))}"
                )
            else:
                log(f"  -> {record.get('status')}: {record.get('message', '')}")
        except (AuthError, MCPError) as error:
            results.append({"source": str(hf), "status": "error", "message": str(error)})
            log(f"  -> 失败：{error}")
            exit_code = 1

    summary = _summarize(results)
    print(json.dumps({"results": results, "summary": summary}, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
