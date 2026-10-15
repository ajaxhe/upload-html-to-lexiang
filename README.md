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

本工具只依赖 Python 3 标准库，**无需安装任何第三方包**。

### 2. 配置乐享凭证（一次性，5 分钟）

工具通过乐享 MCP 接口上传文件，需要一个**个人凭证**（包含两个值）。整个配置只需做一次。

#### 第 1 步：打开凭证页面

浏览器访问 <https://lexiangla.com/ai/claw>，用你的企业账号登录（需要所在企业已开通乐享）。

#### 第 2 步：复制两个值

在页面上获取以下两个值：

| 值 | 长什么样 | 说明 |
|----|---------|------|
| `mcp_token` | `lxmcp_` 开头的一长串字符 | 接口访问令牌，**相当于密码，不要泄露给他人** |
| `company_from` | 一个短字符串（如 `mycompany`） | 你的企业标识，用来路由到正确的企业 |

#### 第 3 步：写入配置文件

仓库自带配置模板 `config/credentials.example.json`，复制一份命名为 `credentials.json` 再填入真实值即可（在本仓库根目录执行）：

```bash
cp config/credentials.example.json config/credentials.json
chmod 600 config/credentials.json
```

然后打开 `config/credentials.json`，把两个值替换成第 2 步复制的真实值：

```json
{
  "mcp_token": "lxmcp_替换成你的token",
  "company_from": "替换成你的企业标识",
  "mcp_base_url": "https://mcp.lexiang-app.com/mcp?preset=all",
  "credential_help_url": "https://lexiangla.com/ai/claw"
}
```

**字段说明**：

| 字段 | 必填 | 说明 |
|------|:---:|------|
| `mcp_token` | ✅ | 第 2 步获取的 token |
| `company_from` | ✅ | 第 2 步获取的企业标识 |
| `mcp_base_url` | 否 | MCP 接口地址，**私有化环境必改**（换成自己域名，见下方「私有化环境配置」）；公有云用户保持默认或删掉此字段均可 |
| `credential_help_url` | 否 | 凭证帮助页地址，私有化环境可换成内部说明页；一般不用改 |

**新手常见错误**：
- JSON 里的引号必须是**英文双引号** `"`，不能用中文引号 `“”`
- 两个必填值缺一不可，少了会报 `凭证缺少字段：xxx`
- `chmod 600` 是把文件权限设为仅本人可读，防止凭证被其他用户读取，建议保留
- `config/` 目录（模板 `credentials.example.json` 除外）已被 `.gitignore` 排除，凭证**不会被提交到 git 仓库**，放心填写

#### 多账号配置（可选）

只有一个账号时，用上面的 `config/credentials.json` 即可（默认）。如果你有**多个企业**的凭证，用命名 profile 区分：

```bash
mkdir -p config/profiles
cat > config/profiles/myorg.json << 'EOF'
{
  "mcp_token": "lxmcp_替换成token",
  "company_from": "替换成企业标识"
}
EOF
chmod 600 config/profiles/myorg.json
```

使用时加 `--profile myorg` 指定；不加 `--profile` 时始终读默认的 `config/credentials.json`。

#### 私有化环境配置（可选）

脚本默认连公有云（`https://mcp.lexiang-app.com/mcp`）。在**乐享私有化环境**运行时，把凭证文件里的 `mcp_base_url` 字段换成私有化 MCP 接口地址（向私有化环境管理员获取）——直接改 `config/credentials.json`（由 `credentials.example.json` 复制而来）里的对应字段即可：

```json
{
  "mcp_token": "私有化环境的token",
  "company_from": "你的企业标识",
  "mcp_base_url": "https://你的私有化域名/mcp?preset=all"
}
```

说明：
- `mcp_base_url` 可自带 query 参数（如 `?preset=all`），拼接时会**保留已配参数**，并自动确保 `preset=all` 与 `company_from` 两个参数始终存在（不重复追加）
- 不写 `mcp_base_url` 字段 = 连公有云
- 临时调试也可用命令行 `--mcp-base-url` 覆盖（优先级：命令行 > 凭证文件 > 公有云默认）

#### 配置文件放在哪里？（优先级从高到低）

| 位置 | 何时读取 |
|------|---------|
| `--credential-file <路径>` | 命令行显式指定时 |
| `config/profiles/<名字>.json` | 指定 `--profile <名字>` 时 |
| `config/credentials.json` | 默认 |
| `~/.config/lexiang-upload/` 同名结构 | 上面都不存在时的兼容回退（老版本路径） |

#### 如何验证配置是否成功？

先跑一个不需要凭证的预检（验证环境）：

```bash
python3 scripts/html_import.py samples/01-基础富文本结构.html --dry-run --json
```

预检通过后，找一个小 HTML 文件真实上传一次（验证凭证）。常见报错对照：

| 报错信息 | 原因与解决办法 |
|---------|---------------|
| `凭证文件不存在：...` | 还没做第 3 步，或文件路径/文件名不对 |
| `凭证缺少字段：mcp_token` | JSON 里少了字段，或引号写成了中文引号 |
| `乐享凭证无效或已过期（401）` | token 失效了，回到 <https://lexiangla.com/ai/claw> 重新获取并更新配置文件 |

## 使用

### 找到上传目标（--target-url 怎么填）

打开乐享网页版，进入你想上传到的**目录（文件夹）**页面，复制浏览器地址栏的完整 URL，形如：

```
https://你的企业域名.lexiangla.com/pages/一串字母数字?company_from=你的企业标识
```

把这个 URL 原样传给 `--target-url` 即可，工具会自动解析出目标目录。

### 单个 HTML 文件

```bash
python3 scripts/html_import.py report.html \
  --target-url "https://你的企业域名.lexiangla.com/pages/<目录entry_id>?company_from=你的企业标识"
```

> 工具会**自动判断**上传方式：HTML 没有引用本地文件 → 直接上传 HTML 源文件（可预览）；引用了本地图片/视频/CSS 等相对路径资源 → 自动打包 ZIP bundle 上传。

### 目录下所有 HTML（每个成为独立条目）

```bash
python3 scripts/html_import.py ./html-dir \
  --target-url "https://你的企业域名.lexiangla.com/pages/<目录entry_id>?company_from=你的企业标识" \
  --profile myorg --json
```

### 原地更新已有条目（不新建）

文档已在乐享、内容有变更时用。**不加 `--entry-id` 重传会在同目录堆出同名重复条目**（乐享不会自动合并）。

`--entry-id` 的取法：打开乐享里那个文档，地址栏 `/pages/` 后面的一串字母数字就是它的 entry_id。

```bash
python3 scripts/html_import.py report.html \
  --entry-id "<该条目自身的 entry_id，取自 /pages/<id>>" \
  --name "条目名（建议与现有条目一致，避免改名）" \
  --force --profile myorg --json
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
| `--target-url` | 乐享目标目录页 URL（自动解析 parent_entry_id） | 新建时二选一 |
| `--parent-entry-id` | 目标父节点 entry_id | 新建时二选一 |
| `--entry-id` | **原地更新**已有的 `file` 类型条目（entry_id 取自 `/pages/<id>`）；指定后无需 `--parent-entry-id`，不加则每次都新建 | 否 |
| `--raw` | 强制直传 `.html` 原文；仅适用于**无相对路径资源**的自包含 HTML（默认无资源时自动走此模式） | 否 |
| `--bundle` | 强制 ZIP bundle 打包（默认有相对路径资源时自动走此模式） | 否 |
| `--name` | 导入后的条目名（默认取 HTML `<title>`） | 否 |
| `--profile` | 凭证 profile（多账号时指定，如 myorg） | 否 |
| `--credential-file` | 凭证文件路径 | 否 |
| `--mcp-base-url` | MCP 接口地址（私有化环境；优先级：本参数 > 凭证文件 `mcp_base_url` > 公有云默认） | 否 |
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

> **上传模式自动判断（默认）**：无本地相对路径资源 → 直传 HTML 源文件（`name=<标题>.html` + `mime_type=text/html`），可直接预览；有相对路径资源 → ZIP bundle 打包（`name=<标题>.zip` + `mime_type=application/zip` + `extension=html`），服务端解包转换后预览。`--raw` / `--bundle` 可强制覆盖。
>
> ⚠️ **ZIP bundle 有 20MB 大小限制**（乐享后端限制，2026-10-15 确认）：超限上传会被服务端拒绝。脚本在打包后自动预检：`--dry-run` 会标记 `oversize: true` 并警告；实际上传超限直接本地报错并给出解决办法（压缩图片/视频、拆分文档、大文件单独上传后外链引用）。
>
> ⚠️ 2026-10-15 实测：小 zip（几 KB）也在 commit 阶段报 `50090009 导入任务失败`，已反馈乐享团队排查（大小只是该错误码的触发条件之一）。**不要用 `.html` 名绕过**——那会把 zip 字节原样存成 `.html` 文件，commit 成功但预览必坏。临时替代：小资源可转 data URI 内联后走 raw 直传。
>
> MCP 网关要求 URL 带 `preset=all` 参数才返回全量业务工具（本工具已内置）。

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
- **兼容性如实记录**：缺失资源降级为占位提示文本，不静默丢弃；每次输出成功/降级/失败三态清单。
- **幂等 + 增量 + 断点续传**：以源文件路径为 key、content_hash 判断变更，manifest 立即落盘，重跑不重不漏。
- **纯标准库实现**：仅依赖 Python 3 标准库（urllib/zipfile/html.parser/hashlib），无需安装第三方包。
- **凭证独立**：只需一个 `lxmcp_` token + `company_from`，一行命令写入 JSON 文件即可。

## 与其他上传方式的区别

| 方式 | 工具 | entry_type | 适用场景 |
|------|------|-----------|---------|
| **本工具**：自动判断（raw 直传 / ZIP bundle） | `file_apply_upload` | file | 单 HTML 直传可预览；带相对资源打包 bundle |
| Markdown 图文 | upload-markdown-to-lexiang | page | Markdown 转乐享在线页面 |
| 纯 HTML 文本 | `entry_import_content`(content_type=html) | page | 无本地资源的 HTML 转可编辑页面 |

## 幂等与增量

- **幂等**：同一文件重复上传不会创建重复条目。脚本用 content_hash（基于源 HTML + 资源文件内容）判断是否变更，未变更则跳过。
- **增量**：源文件修改后重跑，content_hash 不同会触发重新上传并更新 manifest。
- **断点续传**：manifest 每次成功后立即原子写入，中断后重跑只处理未完成的。
- **manifest 位置**：仓库目录下 `manifests/html-import.json`（legacy 路径 `~/.config/lexiang-upload/manifests/` 自动回退兼容）

```bash
# 强制重新上传（忽略幂等）
python3 scripts/html_import.py report.html --target-url "..." --profile myorg --force

# 禁用 manifest（每次全量上传）
python3 scripts/html_import.py report.html --target-url "..." --profile myorg --no-incremental
```

## 兼容性降级

缺失的相对路径资源（文件不存在）不会导致上传失败，而是降级为占位提示文本 `[资源缺失：xxx]` 写入 index.html，并在报告中标记为 `degraded` 状态。每次运行输出三态统计：

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

## 测试样本

仓库内置 `samples/` 目录，提供 9 个覆盖不同场景的 HTML 测试文件及其 `assets/` 资源：

| 文件 | 测试重点 | 依赖 assets |
|------|---------|:-----------:|
| `01-基础富文本结构.html` | 标题/列表/表格/代码块/引用/超链接 | 否 |
| `02-图片-外链引用.html` | https 外链图片 | 否 |
| `03-图片-base64内联.html` | data URI 内联图片 | 否 |
| `04-图片-相对路径.html` | 本地相对路径图片（srcset/picture） | 是 |
| `05-附件-超链接.html` | a 标签附件链接 | 是 |
| `06-混合内容综合.html` | 图片+附件混合 | 是 |
| `07-样式与脚本.html` | 外部 CSS/JS | 是 |
| `08-乐享原生HTML格式.html` | lx-* 格式回环导入 | 否 |
| `09-视频-相对路径.html` | 本地相对路径视频（video/source/poster） | 是 |

`assets/` 含 5 种格式图片（png/jpg/gif/webp/svg）、测试视频（mp4）、4 种附件（txt/csv/pdf/zip）和 CSS/JS。

```bash
# 用样本预检（不需凭证）
python3 scripts/html_import.py samples/ --dry-run --json

# 用样本实际上传（需配置凭证）
python3 scripts/html_import.py samples/04-图片-相对路径.html \
  --target-url "https://你的企业域名.lexiangla.com/pages/<目录entry_id>?company_from=你的企业标识"
```

## 限制

- 创建的是 file 条目（`entry_type=file`），不是可编辑的 page 条目
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
