# ima-note-cli

感谢 [Linux.do 社区](https://linux.do/t/topic/1773167) 的支持。

`ima-note-cli` 是一个仅使用 Python 标准库的 IMA OpenAPI 命令行工具，支持 Notes 与 Knowledge base 的搜索、读取、写入、URL 导入和流式上传。正式入口是 `ima`；`ima-note` 仅保留为 legacy note-only 兼容入口。

## 功能

- 检查凭证配置，不显示凭证值；
- 搜索、列出、读取、创建和追加 Notes；
- 搜索/浏览单个或多个 Knowledge base，添加笔记、网页、远程文件和本地文件；
- 安全读取或导出原始媒体；
- 通过精确名称或账号绑定的本地 alias 解析 Note、文件夹、知识库和媒体；
- 为列表/搜索提供 `--all --max-pages` 有界分页；
- 为文件冲突提供 `--on-conflict error|rename`；
- 为远程下载和 COS 上传提供 `--download-timeout`、`--upload-timeout`；
- 提供稳定的单文档 JSON、逐项 stage/summary 与 exit code 9 partial 语义。

## CLI 与 skill 分发

仓库只有一个 active agent skill：[skills/ima-note-cli](skills/ima-note-cli/SKILL.md)。其 distribution 是 `repository-only`。

`uv tool install` 只安装 Python CLI；it does not install the agent skill。要使用 skill，请从源码 checkout 单独复制或链接 `skills/ima-note-cli`。wheel 不包含 `skills/`、`third_party/` 或归档 CJS。完整裁决见 [skill distribution policy](docs/SKILL_DISTRIBUTION_POLICY.md)。

## 安装 CLI

从 GitHub 安装：

```bash
uv tool install git+https://github.com/Aimer779/ima-note-cli
ima --help
```

更新或卸载：

```bash
uv tool install --reinstall git+https://github.com/Aimer779/ima-note-cli
uv tool uninstall ima-note-cli
```

本地开发：

```bash
git clone https://github.com/Aimer779/ima-note-cli
cd ima-note-cli
uv venv
uv pip install -e .
uv run python -m ima_note_cli --help
```

## 凭证

需要 `IMA_OPENAPI_CLIENTID` 和 `IMA_OPENAPI_APIKEY`。CLI 对每个字段独立按以下优先级解析：

1. 进程环境变量；
2. 当前工作目录 `.env`；
3. `~/.config/ima/client_id` 与 `~/.config/ima/api_key`。

全局安装后推荐系统环境变量，因为 `.env` 只从当前目录读取。检查时使用：

```bash
ima auth
ima auth --json
```

`ima auth` 只显示设置状态和来源，不显示值。不要把真实凭证放进命令行参数、日志或问题报告。

PowerShell 当前会话示例：

```powershell
$env:IMA_OPENAPI_CLIENTID="your_client_id"
$env:IMA_OPENAPI_APIKEY="your_api_key"
```

macOS/Linux 当前会话示例：

```bash
export IMA_OPENAPI_CLIENTID="your_client_id"
export IMA_OPENAPI_APIKEY="your_api_key"
```

Windows 若遇到终端编码错误，可设置 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8` 后重试。

## 资源引用与本地 alias

新的通用参数 `--kb`、`--note`、`--folder`、`--media` 接受三种显式引用：

```text
id:kb_123
alias:research
name:AI Research
```

通用参数中的无前缀值仍按 ID 解释。现有位置 ID、`--kb-id`、`--note-id`、`--folder-id`、`--media-id` 继续作为纯 ID 兼容入口，不会在失败后自动改按名称搜索。`name:` 只进行去除输入首尾空白后的区分大小写精确匹配；解析最多读取 100 个候选页，零匹配、多个同名匹配或分页未完整扫描都会失败，绝不静默选择第一项。JSON 错误会返回稳定 code 和脱敏候选项。

使用 `ima resolve` 显式查找 canonical ID；仅在该命令中，无前缀值表示精确名称：

```bash
ima resolve kb "AI Research" --json
ima resolve note "Weekly Plan" --json
ima resolve note-folder "Work" --json
ima resolve kb-folder "Sources" --kb alias:research --json
ima resolve media "paper.pdf" --kb alias:research --json
```

KB 文件夹和媒体名称必须带知识库作用域。本地 alias 按资源类型和当前账号隔离，存储于 `~/.config/ima/aliases.json`；alias 名称为 1–64 个 ASCII 字母、数字、点、下划线或连字符且必须以字母或数字开头。账号指纹不包含 secret，KB 文件夹和媒体 alias 还会绑定 KB，跨账号或跨 KB 使用会失败：

```bash
ima alias set kb.research id:kb_123
ima alias set media.paper id:media_456 --kb alias:research
ima alias list --type kb --json
ima alias unset kb.research
ima kb browse --kb alias:research
ima note get --note "name:Weekly Plan"
```

重复设置已有 alias 默认失败；只有确认替换目标后才使用 `ima alias set kb.research id:kb_456 --force`。所有远程写命令会先完成全部引用解析，再开始导入、追加或上传。

## 常见工作流

Notes：

```bash
ima note search "meeting"
ima note folders
ima note list --folder-id "folder_id" --all --max-pages 20
ima note get "note_id"
ima note create --title "Title" --content "Body"
ima note append "note_id" --file update.md
```

`note_id` 是 canonical identifier。`ima kb add-note --doc-id` 与 JSON `doc_id` 只是 deprecated compatibility，正式用法是 `--note-id`/`note_id`。

Knowledge：

```bash
ima kb search-base "project"
ima kb show-base --kb-id "kb_id"
ima kb browse --kb-id "kb_id" --all --max-pages 20
ima kb search "schedule" --kb-id "kb_id"
ima kb search "schedule" --kb-id "kb_id_1" --kb-id "kb_id_2" --all --max-pages 20
ima kb search "schedule" --all-bases --max-bases 20
ima kb addable
ima kb add-note --kb-id "kb_id" --note-id "note_id" --title "Title"
ima kb add-url --kb-id "kb_id" --url "https://example.com/article" --download-timeout 30 --upload-timeout 60
ima kb add-file --kb-id "kb_id" --file report.pdf --file notes.md --on-conflict error --upload-timeout 60
ima kb media-info --media-id "media_id"
ima kb read --media-id "media_id"
ima kb export --media-id "media_id" --output original.bin
```

重复 `--kb-id` 可搜索 1–20 个指定知识库；`--all-bases` 与 `--kb-id` 互斥，通过空关键词发现知识库，并由 `--max-bases`（默认 20，范围 1–100）限制扫描规模。`--cursor` 是单库游标，只能与一个 `--kb-id` 一起使用。跨库结果按知识库分组，不生成没有服务端分数依据的全局相关性排序。单库失败时保留其他结果并返回 exit code 9。

所有准确参数、default、choices 与 required 状态见 parser 生成的 [CLI reference](docs/CLI_REFERENCE.md)，也可运行 `ima ... --help`。

## 安全边界

Notes 写入前验证 UTF-8，并移除 Markdown/HTML 中的本地路径、data URI 与非 HTTP(S) 图片引用。写入和上传前应确认目标。

`add-url` 对用户 URL 实施 SSRF 防护：限制 scheme/port、拒绝 userinfo/IP/localhost/非公网 DNS、逐跳验证重定向并绑定已验证公网 IP；不发送 IMA/COS 凭证、cookie 或环境代理。HTML/微信页面使用网页导入，支持的远程文件有界下载后进入与本地文件相同的上传 gate。

上传使用 64 KiB 流式读取、固定 Content-Length、文件身份前后检查和官方 COS host 校验。支持本地 HTML（10 MiB）和 EPUB（50 MiB）；远程 `text/html` 仍按网页导入。默认冲突策略是失败；只有显式 `--on-conflict rename` 才自动改名。

`media-info` 只输出脱敏元数据，知识库封面 URL 也会移除 query 和 fragment。`read` 只读最大 4 MiB 的明确文本 MIME；二进制使用 `export`。导出最大 200 MiB，默认不覆盖，`--force` 仍通过临时文件原子替换。完整签名 URL、临时 header、IMA 凭证和 COS secret 不应出现在输出中。

## JSON 与退出码

在命令末尾加 `--json` 获取一个 stdout JSON 文档；JSON failure 保持 stderr 为空。公共字段包括 `schema_version`、`ok`、`status`、`command` 与 `warnings`，批量结果还包含 `summary`、`results` 和单项 `stage`。

| 退出码 | 含义 |
| --- | --- |
| 0 | success/empty |
| 2 | input error |
| 3 | configuration error |
| 4 | network error |
| 5 | IMA business error |
| 6 | protocol error |
| 7 | original-content/local I/O error |
| 8 | upload error |
| 9 | partial or itemized batch failure |
| 70 | internal error |
| 75 | temporary failure; retry with bounded backoff |
| 130 | interrupted |

`error.retryable=true` 的单项错误使用退出码 75。批处理可能把可重试的单项错误聚合为整体退出码 9，此时只应重试失败项。完整处理规则见 [exit-code guidance](docs/guidance/EXIT_CODES.md)。

## 开发与验证

```bash
uv run python -m unittest discover -s tests -v
uv run python tools/render_cli_reference.py --check
uv run python tools/check_repository_docs.py
uv run python -m compileall -q src tests tools
```

项目没有新增生产依赖。仓库一致性检查离线运行，不读取真实凭证或访问网络。

## 文档

- [CLI reference（generated）](docs/CLI_REFERENCE.md)
- [IMA OpenAPI 1.1.9 唯一契约](docs/IMA_OPENAPI_CONTRACT_1_1_9.md)
- [Skill distribution policy](docs/SKILL_DISTRIBUTION_POLICY.md)
- [Skill migration matrix](docs/SKILL_MIGRATION_1_1_9.md)
- [Exit-code guidance](docs/guidance/EXIT_CODES.md)
- [Implementation plans](docs/plans/OPTIMIZATION_PLAN.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## Third-party provenance

当前契约参考官方 `ima-skills` 1.1.9 下载包；原始 bytes、ZIP/逐文件 SHA-256 与来源保存在 [third_party/ima-skills/1.1.9](third_party/ima-skills/1.1.9)。该 ZIP 未附许可证，归档标记为 `NOASSERTION`。历史 1.1.7 快照及其 MIT-0 证据继续保存在 [third_party/ima-skills/1.1.7](third_party/ima-skills/1.1.7)。两者均为 evidence-only，不是 active skill 或运行时，也不进入 wheel。
