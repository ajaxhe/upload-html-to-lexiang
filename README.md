# upload-html-to-lexiang

将 HTML 文件上传到腾讯乐享知识库：**无本地资源时直传 HTML 源文件**（可直接预览），**含相对路径资源（图片 / CSS / JS / 视频 / 附件）时自动打包为 ZIP bundle** 上传。

## 解决什么问题

- HTML 引用了本地相对路径资源（如 `<img src="assets/photo.png">`）时，直接导入乐享会丢失资源。本工具把 HTML 及其所有相对路径资源打包成一个 ZIP bundle（主文件改名 `index.html`），用 `extension=html` 标记为 HTML bundle 上传，乐享服务端识别后按 HTML 方式解析相对路径资源。
- HTML 没有引用任何本地文件时，直传 HTML 源文件（`text/html`），在乐享中可直接预览。

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

### 2. 配置文件（一次性，5 分钟）

工具通过乐享 MCP 接口上传文件。所有配置放在一个 JSON 文件里：`config/<账号>/config.json`（默认账号目录 `config/default/`），包含**凭证、接口地址、默认上传目标**三类信息——必填三项，其余可选。整个配置只需做一次。

#### 第 1 步：打开凭证页面

浏览器访问 <https://lexiangla.com/ai/claw>，用你的企业账号登录（需要所在企业已开通乐享）。

#### 第 2 步：复制两个凭证值

在页面上获取以下两个值：

| 值 | 长什么样 | 说明 |
|----|---------|------|
| `mcp_token` | `lxmcp_` 开头的一长串字符 | 接口访问令牌，**相当于密码，不要泄露给他人** |
| `company_from` | 一个短字符串（如 `mycompany`） | 你的企业标识，用来路由到正确的企业 |

#### 第 3 步：找到目标知识库的 Space ID

打开乐享网页版，进入你要上传到的**知识库**，浏览器地址栏 `/spaces/` 后面的一串字母数字就是 space_id（如 `https://xxx.lexiangla.com/spaces/50f32b...` → space_id 为 `50f32b...`）。个人知识库同样适用。

#### 第 4 步：写入配置文件

复制模板到默认账号目录 `config/default/config.json`，再填入真实值（在本仓库根目录执行）：

```bash
cp config/default/config.example.json config/default/config.json
chmod 600 config/default/config.json
```

然后打开 `config/default/config.json`，替换其中的值：

```json
{
  "mcp_token": "lxmcp_替换成你的token",
  "company_from": "替换成你的企业标识",
  "target_space_id": "必填：目标知识库ID",
  "mcp_base_url": "https://mcp.lexiang-app.com/mcp?preset=all",
  "credential_help_url": "https://lexiangla.com/ai/claw",
  "target_entry_id": "可选：目标目录entry_id（不填默认上传到知识库根目录，本行可删）"
}
```

**字段说明**：

| 字段 | 必填 | 说明 |
|------|:---:|------|
| `mcp_token` | ✅ | 第 2 步获取的 token |
| `company_from` | ✅ | 第 2 步获取的企业标识 |
| `target_space_id` | ✅ | **目标知识库 ID**（第 3 步获取）：默认上传到该知识库；`target_entry_id` 未填时上传到该知识库的**根目录** |
| `target_entry_id` | 否 | **默认上传目录**的 entry_id：固定上传到知识库里的某个子目录时填写，不填 = 根目录；命令行参数优先 |
| `mcp_base_url` | 否 | MCP 接口地址，**私有化环境必改**（换成自己域名，见下方「私有化环境配置」）；公有云用户保持默认或删掉此行均可 |
| `credential_help_url` | 否 | 凭证帮助页地址，私有化环境可换成内部说明页；一般不用改 |

**最简配置**：只需要前三行——

```json
{
  "mcp_token": "lxmcp_你的token",
  "company_from": "你的企业标识",
  "target_space_id": "你的目标知识库ID"
}
```

上传时不用传任何目标参数，默认进该知识库的根目录。

其余可选字段（`mcp_base_url` / `credential_help_url` / `target_entry_id`）**用不到就整行删除**，留着模板里的中文占位文字会导致上传报错。

**新手常见错误**：
- JSON 里的引号必须是**英文双引号** `"`，不能用中文引号 `“”`
- 三个必填值缺一不可，少了会报 `配置缺少必填字段：xxx`
- 可选字段用不到就**整行删除**，不要留着"替换成…"的占位文字
- `chmod 600` 是把文件权限设为仅本人可读，防止凭证被其他用户读取，建议保留
- 除模板 `config/default/config.example.json` 外，`config/` 下所有账号配置已被 `.gitignore` 排除，凭证**不会被提交到 git 仓库**，放心填写

#### 多账号配置（可选）

每个账号一个子目录（目录名 = profile 名）。默认账号用 `config/default/`，其他账号例如 `myorg`：

```bash
mkdir -p config/myorg
cp config/default/config.example.json config/myorg/config.json
chmod 600 config/myorg/config.json
# 然后编辑 config/myorg/config.json 填入该账号的值
```

使用时加 `--profile myorg` 指定；不加 `--profile` 时读 `config/default/config.json`。

#### 私有化环境配置（可选）

脚本默认连公有云（`https://mcp.lexiang-app.com/mcp`）。在**乐享私有化环境**运行时，把配置文件里的 `mcp_base_url` 字段换成私有化 MCP 接口地址（向私有化环境管理员获取）——直接改账号目录下的 `config.json` 对应字段即可：

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
- 临时调试也可用命令行 `--mcp-base-url` 覆盖（优先级：命令行 > 配置文件 > 公有云默认）

#### 配置文件放在哪里？（优先级从高到低）

| 位置 | 何时读取 |
|------|---------|
| `--config-file <路径>` | 命令行显式指定时 |
| `config/<profile名>/config.json` | 指定 `--profile <名字>` 时（目录名 = profile 名） |
| `config/default/config.json` | 默认（不加 `--profile`） |
| `config/credentials.json`、`config/profiles/<名字>.json` | 兼容回退（v0.7 及更早的单文件结构） |
| `~/.config/lexiang-upload/` 同名结构 | 兼容回退（更早版本路径） |

#### 如何验证配置是否成功？

先跑一个不需要凭证的预检（验证环境）：

```bash
python3 scripts/html_import.py samples/01-基础富文本结构.html --dry-run --json
```

预检通过后，找一个小 HTML 文件真实上传一次（验证凭证）。常见报错对照：

| 报错信息 | 原因与解决办法 |
|---------|---------------|
| `配置文件不存在：...` | 还没做第 4 步，或文件路径/文件名不对 |
| `配置缺少必填字段：mcp_token` | JSON 里少了字段，或引号写成了中文引号 |
| `配置缺少必填字段：target_space_id` | 少了目标知识库 ID（第 3 步获取），或删多了行 |
| `乐享凭证无效或已过期（401）` | token 失效了，回到 <https://lexiangla.com/ai/claw> 重新获取并更新配置文件 |

## 使用

### 找到上传目标（--target-url 怎么填）

打开乐享网页版，进入你想上传到的**目录（文件夹）**页面，复制浏览器地址栏的完整 URL，形如：

```
https://你的企业域名.lexiangla.com/pages/一串字母数字?company_from=你的企业标识
```

把这个 URL 原样传给 `--target-url` 即可，工具会自动解析出目标目录。

**不想每次传目标参数？** 配置文件里的 `target_space_id`（必填）已决定默认上传到哪个知识库——不配 `target_entry_id` 就是该知识库的**根目录**。想固定到某个子目录，把目录的 entry_id 写进 `target_entry_id` 字段；命令行显式指定的目标优先生效。

### 单个 HTML 文件

配置文件已含 `target_space_id` 时，最简单的用法——默认上传到该知识库根目录（配了 `target_entry_id` 则进那个目录）：

```bash
python3 scripts/html_import.py report.html
```

也可以临时指定其他目录（优先级高于配置文件）：

```bash
python3 scripts/html_import.py report.html \
  --target-url "https://你的企业域名.lexiangla.com/pages/<目录entry_id>?company_from=你的企业标识"
```

> 工具会**自动判断**上传方式：HTML 没有引用本地文件 → 直接上传 HTML 源文件（可预览）；引用了本地图片/视频/CSS 等相对路径资源 → 自动打包 ZIP bundle 上传。

### 目录下所有 HTML（每个成为独立条目）

```bash
python3 scripts/html_import.py ./html-dir --profile myorg --json
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
| `--target-url` | 临时指定乐享目标目录页 URL（自动解析 parent_entry_id；不传则用配置文件默认目标） | 否 |
| `--parent-entry-id` | 临时指定目标父节点 entry_id（不传则用配置文件默认目标） | 否 |
| `--entry-id` | **原地更新**已有的 `file` 类型条目（entry_id 取自 `/pages/<id>`）；指定后无需 `--parent-entry-id`，不加则每次都新建 | 否 |
| `--raw` | 强制直传 `.html` 原文；仅适用于**无相对路径资源**的自包含 HTML（默认无资源时自动走此模式） | 否 |
| `--bundle` | 强制 ZIP bundle 打包（默认有相对路径资源时自动走此模式） | 否 |
| `--name` | 导入后的条目名（默认取 HTML `<title>`） | 否 |
| `--profile` | 账号 profile，对应 `config/<名字>/` 目录（如 myorg → `config/myorg/config.json`） | 否 |
| `--config-file` | 配置文件路径（兼容旧参数名 `--credential-file`） | 否 |
| `--mcp-base-url` | MCP 接口地址（私有化环境；优先级：本参数 > 配置文件 `mcp_base_url` > 公有云默认） | 否 |
| `--dry-run` | 只检测打包不上传 | 否 |
| `--json` | stdout 只输出 JSON | 否 |

> **新建条目时的目标**：不传任何目标参数 → 用配置文件默认目标（`target_entry_id` 指定目录，不填 = `target_space_id` 知识库根目录）；命令行 `--target-url` / `--parent-entry-id` 可临时覆盖（优先级：命令行 > 配置文件）。`--entry-id` 原地更新时无需目标目录。

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
> ⚠️ **ZIP bundle 有 20MB 大小限制**（乐享后端限制）：超限上传会被服务端拒绝。脚本在打包后自动预检：`--dry-run` 会标记 `oversize: true` 并警告；实际上传超限直接本地报错并给出解决办法（压缩图片/视频、拆分文档、大文件单独上传后外链引用）。
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
- **配置极简**：一个 `config.json` 搞定，最简两行（`mcp_token` + `company_from`）即可跑通公有云；接口地址与默认上传目标均为可选配置，支持多账号子目录隔离。

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
