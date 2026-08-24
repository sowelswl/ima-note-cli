# 退出码与错误处理指南

本文档描述 `ima` 和兼容入口 `ima-note` 的当前进程退出码契约。机器调用方应同时读取退出码和 JSON 中可用的 `status`、`error.code`、`error.retryable`；不要只根据错误文本编排恢复逻辑。

实现事实源是 `src/ima_note_cli/errors.py`、`src/ima_note_cli/command_result.py` 和 `src/ima_note_cli/output.py`。

## 退出码表

| 退出码 | 名称 | 含义 | 调用方动作 |
| ---: | --- | --- | --- |
| `0` | success | 命令成功或查询结果为空 | 正常继续；通过 `status` 区分 `success` 和 `empty` |
| `2` | input | 参数、输入内容或本地预检不合法 | 修正参数或输入后重试 |
| `3` | config | 凭证缺失或配置文件无效 | 修复本地配置；不要自动重复请求 |
| `4` | transport | 不建议盲目重试的网络/HTTP 失败 | 检查目标、网络策略或 HTTP 状态 |
| `5` | business | IMA 拒绝请求或认证失败 | 检查 `error.code`，修正凭证或业务条件 |
| `6` | protocol | IMA 响应不符合当前契约 | 保留脱敏诊断并检查客户端/服务端版本 |
| `7` | local I/O | 原文不可用、编码无效或本地读写失败 | 检查媒体能力、文件系统和输出路径 |
| `8` | upload | 不可重试的上传、COS 或文件快照错误 | 检查文件状态、签名和上传条件 |
| `9` | partial | 分页被截断或逐项批处理未完整成功 | 检查 `summary`、`results` 和各项 `stage` |
| `70` | internal | 未预期、未分类的客户端错误 | 保留脱敏输出并报告缺陷 |
| `75` | temporary | 明确可重试的临时故障 | 使用有上限的指数退避重试 |
| `130` | interrupted | 用户通过 Ctrl-C 中断 | 按用户取消处理，不要自动重试写操作 |

项目不定义退出码 `1`、`66`、`69`、`77` 或 `78`。空结果是成功状态，不使用 `EX_NOINPUT`：它返回 `0` 和 `status=empty`，避免正常搜索在 Shell、CI 或管道中被当成失败。

## 状态与退出码约束

命令结果有四种 `status`：

- `success` 和 `empty` 必须返回 `0`，且不携带错误；
- `partial` 和 `failed` 必须返回非零退出码；
- 达到 `--max-pages` 会保留已取得结果并返回 `9`；
- 批处理中存在 `failed` 或 `not_attempted` 项时整体返回 `9`；
- 即使所有批处理项都失败，整体也可能是 `status=failed`、退出码 `9`，因为逐项结果仍是主要诊断入口。

跨知识库检索会把单库错误聚合到 `knowledge_bases`。只要有知识库失败或不完整，整体返回 `9`；可用的其他知识库结果不会丢失。

## 临时故障与重试

`error.retryable=true` 与退出码 `75` 具有统一含义。除非调用方显式处理逐项批结果，否则不应出现“可重试但退出码不是 75”的单项错误。

当前临时 HTTP 状态为：

```text
408, 429, 500, 502, 503, 504
```

超时、连接中断、响应体中断、可重试状态码和有限重试耗尽也会归入 `75`。HTTP 400、404 等永久失败保持各自领域的非临时退出码，并设置 `retryable=false`。

批量上传、URL 导入或跨知识库检索可能把一个内部 `75` 聚合为整体 `9`。此时应读取各项 `error.retryable`，只重试失败项，不能无条件重放已经成功的写操作。

推荐的只读命令重试策略：

1. 只在退出码为 `75`，或批结果中的目标项明确设置 `retryable=true` 时重试；
2. 使用指数退避和随机抖动，并设置最大次数；
3. 尊重服务端限流，不并发放大 429；
4. 写命令发生不确定结果时先读回状态，不自动重放。

## 凭证与认证错误

凭证错误通过退出码和稳定 `error.code` 共同分类：

| 条件 | 退出码 | `error.code` | 处理方式 |
| --- | ---: | --- | --- |
| Client ID 或 API Key 缺失 | `3` | `credentials_missing` | 配置环境变量、项目 `.env` 或用户配置 |
| 凭证配置路径/UTF-8 无效 | `3` | `credentials_config_invalid` | 修复配置文件 |
| IMA 通过 HTTP 401/403 拒绝凭证 | `5` | `authentication_rejected` | 更新或重新签发凭证 |

IMA 在 HTTP 200 业务响应中拒绝请求时仍可能返回通用 `api_business_error`。除非官方提供稳定的业务错误码，否则客户端不会根据自然语言错误消息猜测认证状态。

## JSON 与普通输出

使用 `--json` 时，成功和失败都只向 stdout 写一个 JSON 文档，stderr 保持为空。命令处理器产生的结果包含 `status`；参数、配置或顶层异常直接产生的错误文档没有 `status`，应读取 `ok=false` 和 `error`。失败示例结构：

```json
{
  "schema_version": 1,
  "ok": false,
  "command": "kb.media-info",
  "warnings": [],
  "error": {
    "code": "api_transport_error",
    "message": "The request to the IMA API timed out.",
    "exit_code": 75,
    "retryable": true
  }
}
```

不使用 `--json` 时，正常内容写 stdout，warning 和 error 写 stderr；进程退出码保持相同。

## PowerShell 调用示例

```powershell
ima kb search "agent" --kb-id "kb_id" --json
$imaExitCode = $LASTEXITCODE

if ($imaExitCode -eq 75) {
    Write-Warning "IMA temporary failure; retry with bounded backoff."
} elseif ($imaExitCode -eq 9) {
    Write-Warning "Inspect itemized results before retrying failed items."
} elseif ($imaExitCode -ne 0) {
    throw "IMA command failed with exit code $imaExitCode"
}
```

退出码属于 CLI 自动化契约。新增分类可以向后扩展，但已有退出码不应在没有明确兼容计划的情况下重编号。
