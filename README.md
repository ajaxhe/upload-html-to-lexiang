# upload-html-to-lexiang

将含本地相对路径资源（图片 / CSS / JS / 附件）的 HTML 打包为 ZIP bundle 上传到腾讯乐享知识库。

## 解决什么问题

HTML 文件通过 `<img src="assets/photo.png">` 引用本地相对路径图片时，直接导入乐享会丢失图片。本工具把 HTML 及其所有相对路径资源打包成一个 ZIP bundle（主文件改名 `index.html`），用 `extension=html` 标记为 HTML bundle 上传，乐享服务端识别后按 HTML 方式解析相对路径资源。

## 工作原理

```
源 HTML + 相对路径资源
  │
  ├─ 1. 扫描 HTML，检测所有相对路径引用（img/srcset/link/script/a/video...）
  │     过滤掉 http(s)://、data: URI、# 锚点等非本地引用
  │
  ├─ 2. 打包 ZIP bundle
  │     主 HTML → index.html（乐享 bundle 入口要求）
  │     资源 → 保持原始目录结构（assets/photo.png 原样）
  │
  ├─ 3. file_apply_upload(extension=html, upload_type=PRE_SIGNED_URL)
  │     → 返回预签名 upload_url + session_id
  │
  ├─ 4. HTTP PUT zip 到预签名 URL（无需额外签名）
  │
  └─ 5. file_commit_upload(session_id)
     → 确认上传，创建 entry（返回 entry.id）
```

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/ajaxhe/upload-html-to-lexiang.git
cd upload-html-to-lexiang
```

### 2. 配置乐享凭证

凭证与 [upload-markdown-to-lexiang](https://github.com/ajaxhe/lexiang-openapi-skill) 共享，首次使用先配置：

```bash
# 需先安装 upload-markdown-to-lexiang skill
python3 ~/.workbuddy/skills/upload-markdown-to-lexiang/scripts/lexiang_upload.py auth login

# 多账号：
python3 ~/.workbuddy/skills/upload-markdown-to-lexiang/scripts/lexiang_upload.py auth login --profile csig
```

凭证保存在 `~/.config/lexiang-upload/credentials.json`（default）或 `~/.config/lexiang-upload/profiles/<profile>.json`，权限 0600。

访问 https://lexiangla.com/ai/claw 获取个人凭证（`lxmcp_` 前缀 MCP API Token）。

## 使用

### 单个 HTML 文件

```bash
python3 scripts/html_import.py report.html \
  --target-url "https://csig.lexiangla.com/pages/<目录entry_id>?company_from=csig" \
  --profile csig
```

### 目录下所有 HTML（每个成为独立条目）

```bash
python3 scripts/html_import.py ./html-dir \
  --target-url "https://csig.lexiangla.com/pages/<目录entry_id>?company_from=csig" \
  --profile csig --json
```

### 预检模式（只检测打包不上传）

```bash
python3 scripts/html_import.py report.html --dry-run --json
```

输出示例：

```json
{
  "results": [
    {
      "source": "report.html",
      "title": "测试报告",
      "index_name": "index.html",
      "zip_size": 10430,
      "file_count": 6,
      "included_resources": ["assets/photo.png", "assets/style.css"],
      "missing_resources": [],
      "status": "dry_run"
    }
  ]
}
```

## 参数

| 参数 | 说明 | 必填 |
|------|------|:----:|
| `source` | HTML 文件路径或含 .html 的目录 | 是 |
| `--target-url` | 乐享目标目录页 URL（自动解析 parent_entry_id） | 二选一 |
| `--parent-entry-id` | 目标父节点 entry_id | 二选一 |
| `--name` | 导入后的条目名（默认取 HTML `<title>`） | 否 |
| `--profile` | 凭证 profile（如 csig） | 否 |
| `--credential-file` | 凭证文件路径 | 否 |
| `--dry-run` | 只检测打包不上传 | 否 |
| `--json` | stdout 只输出 JSON | 否 |

## ZIP bundle 结构

```
report.zip
├── index.html          ← 主 HTML（从原文件改名）
├── assets/
│   ├── photo.png       ← 相对路径资源保持原结构
│   ├── style.css
│   └── app.js
└── docs/
    └── guide.pdf
```

乐享服务端识别 `extension=html` 后，以 `index.html` 为入口解析 bundle，相对路径资源按原始路径匹配。

## 资源检测范围

| 标签 / 属性 | 处理方式 |
|-------------|---------|
| `<img src>` 相对路径 | 打包进 zip |
| `<img srcset>` / `<picture><source>` | 拆分多 URL 逐个打包 |
| `<link href>` CSS | 打包进 zip |
| `<script src>` JS | 打包进 zip |
| `<a href>` 本地文件 | 打包进 zip |
| `<video src>` / `<source src>` / `<audio src>` | 打包进 zip |
| `http(s)://` 外链 | 保留原样（不打包） |
| `data:` URI | 保留原样（不打包） |

## 设计原则

- **零 token 执行**：扫描、打包、上传全程由脚本完成，HTML 正文和资源二进制从不进入 LLM 上下文。脚本只输出 JSON 摘要。
- **纯标准库实现**：仅依赖 Python 3 标准库（urllib/zipfile/html.parser/hashlib），无需安装第三方包。
- **凭证复用**：与 upload-markdown-to-lexiang 共享凭证体系，不重复配置。

## 与其他上传方式的区别

| 方式 | 工具 | entry_type | 适用场景 |
|------|------|-----------|---------|
| **本工具**：ZIP bundle | `file_apply_upload`(extension=html) | file | HTML + 相对路径资源 |
| Markdown 图文 | upload-markdown-to-lexiang | page | Markdown 转乐享在线页面 |
| 纯 HTML 文本 | `entry_import_content`(content_type=html) | page | 无本地资源的 HTML 转页面 |

## 限制

- 创建的是 file 条目（`entry_type=file`），不是可编辑的 page 条目
- 无幂等/增量同步：重复上传同一文件会创建多个同名条目
- 不处理 CSS `url()` 中的相对路径（仅处理 HTML 标签属性）

## 开发

```bash
# 语法检查
python3 -c "import ast; ast.parse(open('scripts/html_import.py').read()); print('OK')"

# 预检验证
python3 scripts/html_import.py <test-dir> --dry-run --json
```

## License

MIT
