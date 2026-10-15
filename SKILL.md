---
name: upload-html-to-lexiang
description: >-
  Upload HTML files (with local relative-path images/css/js/attachments) to a
  Tencent Lexiang knowledge base entry. Packages each HTML plus its relative
  resources into a ZIP bundle (main file renamed to index.html), then uploads
  via the standard three-step flow: file_apply_upload(extension=html,
  PRE_SIGNED_URL) then HTTP PUT then file_commit_upload. The extension=html
  flag tells Lexiang this zip is an HTML bundle, not a regular zip. Missing
  resources are degraded to placeholder notices (never silently dropped).
  Idempotent re-runs via content-hash manifest: unchanged files are skipped,
  changed files are re-uploaded. Use when uploading/importing HTML to Lexiang,
  especially when the HTML references local images via relative paths. Also
  handles batch import of a directory of .html files. Credentials are a simple
  JSON file obtained from https://lexiangla.com/ai/claw. 将含本地相对路径资源的 HTML 打包为 ZIP bundle 上传到乐享。
---

# Upload HTML to Lexiang

把含本地相对路径资源（图片/CSS/JS/附件）的 HTML 打包为 ZIP bundle 上传到乐享知识库。
核心：**主文件改名 index.html + 资源保持原始相对路径结构打包进 zip**，
用 `extension=html` 标记为 HTML bundle，乐享服务端识别后按 HTML 方式处理相对路径资源。

## 硬性规则

1. 只使用从 <https://lexiangla.com/ai/claw> 获取的个人凭证（`lxmcp_` 前缀 MCP API Token）。
2. 配置文件（凭证 + 端点 + 目的端，每账号一个子目录）：**默认 `config/default/config.json`**，多账号 `config/<profile>/config.json`（`--profile <name>` 指定），权限 0600。格式：`{"mcp_token": "lxmcp_xxx", "company_from": "xxx"}` + 可选字段：`mcp_base_url`（私有化 MCP 接口地址，endpoint 跟凭证走不硬编码；不配 = 公有云 `https://mcp.lexiang-app.com/mcp`；可自带 query 参数如 `?preset=all`，拼接时保留已配参数并自动确保 `preset=all` 与 `company_from` 存在）、`credential_help_url`、`target_entry_id`（默认目标目录，CLI 优先）、`target_space_id`（无 entry_id 时自动解析知识库根目录）。legacy 自动回退：`config/credentials.json`、`config/profiles/<name>.json`、`~/.config/lexiang-upload/` 同名结构。命令行 `--mcp-base-url` 可临时覆盖（优先级：CLI > 配置文件 > 默认）。
3. **上传模式自动判断（默认 auto，2026-10-15 起）**：先检测 HTML 是否引用本地相对路径资源——**无依赖 → raw 直传 HTML 源文件**（`mime_type=text/html`、`name=<标题>.html`，可直接预览）；**有依赖 → ZIP bundle 打包**（主文件改名 `index.html` + 资源保持相对路径，`extension=html` 标记，服务端解包转换）。`--raw` / `--bundle` 可强制覆盖。
4. bundle 必须传 `extension=html`（区分普通 zip 和 HTML bundle）。
5. `file_apply_upload` 的 `name` 参数用原文档标题（取自 HTML `<title>` 或 `--name`）：raw 模式后缀 `.html`；**bundle 模式后缀必须是 `.zip`**（`<标题>.zip`）——`.zip` 名 + `extension=html` 才会走服务端 HTML bundle 解包转换管道。
6. 缺失资源**不静默丢弃**：降级为 `[资源缺失：xxx]` 占位提示文本写进 index.html。
7. 每次上传后立即落盘 manifest，重跑时以 content_hash 判断是否跳过（幂等）。
8. stdout 只输出 JSON，诊断和进度输出到 stderr。
9. **乐享 MCP 网关（元工具）模式与 preset=all**：2026-10 起乐享 MCP 服务端要求 MCP URL 带 `preset=all` 查询参数，`tools/list` 才返回全量业务工具（93 个）；不带时只有 `call_tool` 等 7 个元工具，业务工具（`file_apply_upload` / `file_commit_upload` / `entry_describe_entry`）直呼会返回 HTTP 200 + 文本 `tool is not allowed: <tool>`（不是 JSON-RPC error）。`MCPClient.url` 已内置 `preset=all`。同时保留 `MCPClient._request` 兜底：万一业务工具仍被拒（返回 `tool is not allowed`），自动改走 `call_tool`（`{tool_name, arguments}`）转发。⚠️ **元工具（`get_tool_schema`）不能经 `call_tool` 调**（返回 `cannot call meta tool`），故兜底只在业务工具被拒时触发，**不要无条件转发**。
10. **更新已有条目必须传 `--entry-id`**：不传就是新建，重传会在同目录堆出同名条目（乐享不会自动合并）。
11. **bundle 大小限制 20MB（2026-10-15 乐享团队确认）**：HTML bundle 的 zip 包不能超过 20MB，超限 `file_commit_upload` 报 `50090009 导入任务失败`。脚本已内置本地预检：dry-run 标记 `oversize: true` 并警告；实际上传超限直接本地报错（含最大资源提示与解决办法：压缩图片/视频、拆分文档、大文件单独上传后外链引用），不发无效请求。⚠️ **待确认**：1.7KB~243KB 的小 zip 同样报 50090009（2026-10-15 多次实测），说明 50090009 是通用「导入任务失败」错误码、大小只是触发条件之一，小 zip 失败另有原因（已反馈乐享团队排查）。
12. ⚠️ **不要用 `.html` 名绕过 bundle 报错**——那会跳过解包管道、把 zip 字节原样存成 `.html` 文件，commit 虽成功但**预览必坏**（2026-10-15 实测下载确认 PK 头）。带资源场景的临时替代：把相对路径资源转 data URI 内联进 HTML 后自动走 raw 直传（小图片/CSS 可行，大视频不现实）。

## 上传流程（三步）

```
file_apply_upload(upload_type=PRE_SIGNED_URL)
  │  返回 session.session_id + session.objects[0].upload_url（预签名）
  │
  ├─ HTTP PUT 文件到 upload_url（预签名 URL，无需额外签名）
  │
  └─ file_commit_upload(session_id)
     → 确认上传，创建 entry（返回 entry.id）
     → 成功后写 manifest（source_path -> {content_hash, entry_id}）
```

**关键参数**：

raw 模式（无相对路径依赖，自动选择）：
- `mime_type=text/html`
- `name=<标题>.html`
- 不打 zip、不传 extension

bundle 模式（有相对路径依赖，自动选择）：
- `mime_type=application/zip`（bundle 文件本身是 zip 格式）
- `extension=html`（告诉乐享这是 HTML bundle，不是普通压缩包）
- `name=<标题>.zip`（`.zip` 后缀 + `extension=html` 才走 bundle 解包转换管道）
- `size`：zip 文件大小（字节数，字符串）
- `upload_type=PRE_SIGNED_URL`（返回预签名 URL，直接 PUT）

## 兼容性处理（原则2）

| 资源状态 | 处理方式 | 报告状态 |
|---------|---------|---------|
| 本地文件存在 | 打包进 zip，保持原始相对路径 | succeed |
| 本地文件缺失 | 降级为 `[资源缺失：xxx — 源文件不存在，已跳过]` 占位提示 | degraded |
| 上传失败 | 记录错误信息 | error |

每次运行输出三态 summary：

```json
{
  "summary": {
    "total": 3,
    "succeed": 2,
    "skipped": 1,
    "degraded": 0,
    "failed": 0,
    "degraded_resources": []
  }
}
```

## 幂等 + 增量 + 断点续传（原则3）

- **唯一标识**：源文件绝对路径（resolve 后的绝对路径）
- **变更检测**：content_hash = sha256(HTML 原文 + 所有资源文件字节)，基于源文件内容而非 zip 字节（zip 元数据不稳定）
- **manifest 路径**：本 skill 目录下 `manifests/html-import.json`，权限 0600（legacy 路径 `~/.config/lexiang-upload/manifests/html-import.json` 自动回退兼容）
- **状态结构**：`{source_path: {content_hash, entry_id, entry_name, uploaded_at}}`
- **幂等**：重跑时 content_hash 相同 → 跳过上传，复用已记录的 entry_id
- **增量**：源文件变更 → content_hash 不同 → 重新上传并更新 manifest
- ⚠️ **默认是「新建条目」而非「更新原条目」**：不带 `--entry-id` 重传会在乐享目录堆出同名条目。**要原地更新务必用 `--entry-id <entry_id>`** —— 脚本会先 `entry_describe_entry` 取 `target_id` 作为 `file_id`，并把 `parent_entry_id` 指向**该条目自身**（乐享「场景2」的硬性要求，填父目录会失败）。若历史遗留了重复条目，用 `call_tool` 调 `entry_delete_entry`（`tool_name=entry_delete_entry`, `arguments={entry_id}`）删除，再用 `entry_list_children` 复核目录。
- **断点续传**：manifest 每次成功上传后立即落盘（原子写入 .tmp + rename），中断后重跑只处理未完成的
- **跨会话**：状态跟源文件走，不绑会话，换设备也能续传

## 凭证配置

配置默认放在 **本 skill 目录下**（`config/`），每账号一个子目录；`~/.config/lexiang-upload/` 等旧路径自动回退。

1. 访问 <https://lexiangla.com/ai/claw> 获取个人凭证（`lxmcp_` 前缀 token + `company_from`）
2. 复制模板到账号目录并填值（skill 目录按实际路径替换；默认账号为 `default`）：

```bash
mkdir -p <skill-dir>/config/default
cp <skill-dir>/config/config.example.json <skill-dir>/config/default/config.json
# 编辑 config/default/config.json 替换 mcp_token / company_from
chmod 600 <skill-dir>/config/default/config.json
```

多账号用命名 profile（目录名 = profile 名）：

```bash
mkdir -p <skill-dir>/config/myorg
cp <skill-dir>/config/config.example.json <skill-dir>/config/myorg/config.json
# 编辑 config/myorg/config.json 填入该账号的值
chmod 600 <skill-dir>/config/myorg/config.json
```

> AI agent 可在用户提供凭证后自动执行上述命令完成配置，无需用户手动操作。
> ⚠️ `config/` 与 `manifests/` 已在 `.gitignore` 中排除，凭证和上传状态不会泄漏到 git 仓库。

## 上传

单个 HTML 文件：

```bash
python3 scripts/html_import.py report.html \
  --target-url "https://lexiangla.com/pages/<entry_id>?company_from=abc" \
  --profile myorg
```

目录下所有 HTML（每个成为独立条目，未变更的自动跳过）：

```bash
python3 scripts/html_import.py ./html-dir \
  --target-url "https://lexiangla.com/pages/<entry_id>?company_from=abc" \
  --profile myorg --json
```

强制重新上传（忽略 manifest 幂等检查）：

```bash
python3 scripts/html_import.py report.html \
  --target-url "https://lexiangla.com/pages/<entry_id>?company_from=abc" \
  --profile myorg --force
```

**原地更新已有条目**（不新建；文档已在乐享、内容变更时用）：

```bash
python3 scripts/html_import.py report.html \
  --entry-id "<该条目自身的 entry_id>" \
  --name "条目名（建议与现有条目一致，避免改名）" \
  --force --profile myorg --json
```

> **模式自动判断（默认）**：无本地相对路径资源 → raw 直传 `.html` 原文（可直接预览）；有相对路径资源 → ZIP bundle 打包。`--raw` / `--bundle` 强制覆盖（`--raw` 遇有相对引用的 HTML 会直接报错）。

只做预检（检测资源、打包、不上传）：

```bash
python3 scripts/html_import.py report.html --dry-run --json
```

## 参数

| 参数 | 说明 |
|------|------|
| `source` | HTML 文件路径或含 .html 的目录 |
| `--target-url` | 乐享目标目录页 URL（自动解析 parent_entry_id） |
| `--parent-entry-id` | 目标父节点 entry_id（与 --target-url 二选一） |
| `--entry-id` | **原地更新**已有的 `file` 类型条目（entry_id 取自 `/pages/<id>`）；指定后无需 `--parent-entry-id`。不加则每次都新建 |
| `--raw` | 强制直传 `.html` 原文（仅适用于无相对路径资源的自包含 HTML；默认无资源时自动走此模式） |
| `--bundle` | 强制 ZIP bundle 打包（默认有相对路径资源时自动走此模式） |
| `--name` | 导入后的条目名（默认取 HTML `<title>`） |
| `--profile` | 凭证 profile（多账号时指定，如 myorg） |
| `--config-file` | 配置文件路径（兼容旧参数名 `--credential-file`） |
| `--dry-run` | 只检测打包不上传 |
| `--force` | 强制重新上传，忽略 manifest 幂等检查 |
| `--no-incremental` | 禁用 manifest（每次全量上传，不记状态） |
| `--manifest-path` | manifest 文件路径（默认 skill 目录下 `manifests/html-import.json`） |
| `--json` | stdout 只输出 JSON |

## ZIP bundle 结构

```
report.zip
├── index.html          ← 主 HTML（从原文件改名）
├── assets/
│   ├── image.png       ← 相对路径资源保持原结构
│   ├── style.css
│   └── script.js
└── ...
```

乐享服务端识别 `extension=html` 后，以 `index.html` 为入口解析 bundle，
相对路径资源（`assets/image.png`、`./style.css` 等）按原始路径匹配。

## 资源检测范围

| 标签/属性 | 处理 |
|-----------|------|
| `<img src>` 相对路径 | 打包进 zip |
| `<img srcset>` / `<picture><source>` | 拆分多 URL 逐个打包 |
| `<link href>` CSS | 打包进 zip |
| `<script src>` JS | 打包进 zip |
| `<a href>` 本地文件 | 打包进 zip |
| `http(s)://` 外链 | 保留原样（不打包） |
| `data:` URI | 保留原样（不打包） |

## 与其他上传方式的区别

| 方式 | 工具 | entry_type | 适用场景 |
|------|------|-----------|---------|
| **本 skill**：ZIP bundle | `file_apply_upload`(extension=html) | file | HTML + 相对路径资源 |
| Markdown 图文 | upload-markdown-to-lexiang | page | Markdown 转乐享在线页面 |
| 纯 HTML 文本 | `entry_import_content`(content_type=html) | page | 无本地资源的 HTML 转页面 |

## 修改与验证

```bash
python3 -c "import ast; ast.parse(open('scripts/html_import.py').read()); print('OK')"
python3 scripts/html_import.py <test-dir> --dry-run --json
```
