---
name: upload-html-to-lexiang
description: >-
  Upload HTML files (with local relative-path images/css/js/attachments) to a
  Tencent Lexiang knowledge base entry. Packages each HTML plus its relative
  resources into a ZIP bundle (main file renamed to index.html), then uploads
  via the standard three-step flow: file_apply_upload(extension=html,
  PRE_SIGNED_URL) then HTTP PUT then file_commit_upload. The extension=html
  flag tells Lexiang this zip is an HTML bundle, not a regular zip. Use when
  uploading/importing HTML to Lexiang, especially when the HTML references
  local images via relative paths. Also handles batch import of a directory of
  .html files. Credentials shared with upload-markdown-to-lexiang.
  将含本地相对路径资源的 HTML 打包为 ZIP bundle 上传到乐享，自动把主文件改名 index.html。
---

# Upload HTML to Lexiang

把含本地相对路径资源（图片/CSS/JS/附件）的 HTML 打包为 ZIP bundle 上传到乐享知识库。
核心：**主文件改名 index.html + 资源保持原始相对路径结构打包进 zip**，
用 `extension=html` 标记为 HTML bundle，乐享服务端识别后按 HTML 方式处理相对路径资源。

## 硬性规则

1. 只使用从 <https://lexiangla.com/ai/claw> 获取的个人凭证（`lxmcp_` 前缀 MCP API Token）。
2. 凭证与 upload-markdown-to-lexiang 共享（`~/.config/lexiang-upload/`），不重复配置。
3. ZIP bundle 内的主 HTML 文件必须命名为 `index.html`（否则乐享无法识别 bundle 入口）。
4. 必须传 `extension=html`（区分普通 zip 和 HTML bundle）。
5. `file_apply_upload` 的 `name` 参数用原文档标题（取自 HTML `<title>` 或 `--name`），作为 zip 文件名和乐享条目名。不要用 "index" 或固定名。
6. stdout 只输出 JSON，诊断和进度输出到 stderr。

## 上传流程（三步）

```
file_apply_upload(extension=html, upload_type=PRE_SIGNED_URL)
  │  返回 session.session_id + session.objects[0].upload_url（预签名）
  │
  ├─ HTTP PUT zip 到 upload_url（预签名 URL，无需额外签名）
  │
  └─ file_commit_upload(session_id)
     → 确认上传，创建 entry（返回 entry.id）
```

**关键参数**：
- `mime_type=application/zip`（文件是 zip 格式）
- `extension=html`（告诉乐享这是 HTML bundle，不是普通压缩包）
- `upload_type=PRE_SIGNED_URL`（返回预签名 URL，直接 PUT）
- `name`：zip 文件名（如 `report.zip`）
- `size`：zip 文件大小（字节数，字符串）

## 凭证配置

凭证与 upload-markdown-to-lexiang 完全共享。首次使用先配置：

```bash
python3 ~/.workbuddy/skills/upload-markdown-to-lexiang/scripts/lexiang_upload.py auth login
# 多账号：
python3 ~/.workbuddy/skills/upload-markdown-to-lexiang/scripts/lexiang_upload.py auth login --profile csig
```

凭证保存在 `~/.config/lexiang-upload/credentials.json`（default）或
`~/.config/lexiang-upload/profiles/<profile>.json`，权限 0600。

## 上传

单个 HTML 文件：

```bash
python3 scripts/html_import.py report.html \
  --target-url "https://csig.lexiangla.com/pages/<entry_id>?company_from=csig" \
  --profile csig
```

目录下所有 HTML（每个成为独立条目）：

```bash
python3 scripts/html_import.py ./html-dir \
  --target-url "https://csig.lexiangla.com/pages/<entry_id>?company_from=csig" \
  --profile csig --json
```

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
| `--name` | 导入后的条目名（默认取 HTML `<title>`） |
| `--profile` | 凭证 profile（如 csig） |
| `--credential-file` | 凭证文件路径 |
| `--dry-run` | 只检测打包不上传 |
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
