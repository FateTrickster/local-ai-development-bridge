# Local MCP Bridge Roadmap

目标：在不依赖 MoonCode 授权体系的前提下，实现一套可长期维护的本地 AI Development Bridge，并吸收 MoonCode 在文件安全、IDE 语义、持久终端、任务状态、权限控制和可观察性方面的优点。

## P0：可靠性与安全底座（已完成）

1. 文件层
   - 稳定分页目录浏览
   - glob 文件查找与 exclude
   - literal/regex 文本搜索
   - 批量文件读取与行范围
   - SHA-256 文件版本
   - 隐藏文件、忽略规则、输出预算

2. 安全修改层
   - expected_versions 乐观并发控制
   - VERSION_CONFLICT
   - 多文件 unified diff patch
   - 事务式提交与失败回滚

3. 持久终端层
   - command_id
   - 后台进程
   - 增量 stdout/stderr
   - wait
   - stdin
   - 有界缓冲与超时

4. 公网安全层
   - Bearer Token / capability URL
   - read/write/command 分权
   - 审计日志
   - 默认 localhost 监听
   - DNS rebinding Host allowlist

## P1：VS Code Companion（基础能力已完成）

- diagnostics
- dirty editor buffer
- workspace/document symbols
- definition/references/implementation/hover
- provider ready/not_ready 状态

后续仍需细化空结果与 provider 不可用、language server loading 等状态语义。

## P1：任务状态（已完成）

- todos
- progress events
- 长任务 journal
- progress event 具体查询与 after_seq 增量读取

## P1.5：可观察性 / Activity / 本地控制台（当前实施）

目标：解决“ChatGPT / Bridge 正在做什么、做到哪里完全看不见”的黑匣子问题。

已落地：

- `ActivityService`
- `.runtime/activity.jsonl`
- tool started/completed/failed 结构化事件
- command started/completed/failed 结构化事件
- 常见 token / secret / Authorization / capability URL 脱敏
- `get_activity`
- `get_progress_events`
- `dashboard_info`
- Terminal command snapshot
- localhost-only read-only Dashboard
- 当前 todo / progress / activity / terminal / VS Code 状态可视化
- Dashboard 与公网 MCP Tunnel 分离

后续增强：

- 文件修改增删行统计与可展开 diff
- Activity log 轮转
- 更丰富的运行耗时/失败统计
- VS Code 内嵌 Status Bar / Tree View 或 Webview

## P2.0：一键启动与 Tunnel Manager（已完成）

- 自动检查 Python / cloudflared / VS Code Companion
- 启动 Quick Tunnel
- 捕获实际 `trycloudflare.com` hostname
- 自动写入精确 Host allowlist
- 启动 MCP Server
- initialize → tools/list smoke test
- 输出脱敏公网地址与本地 Dashboard 地址
- 启停与重连状态管理

## P2.1：GitHub 工程化与 Release（已完成）

- Windows + Linux Python unit test CI
- TypeScript compile CI
- 内置 secret scan（tracked + untracked non-ignored）
- CI VSIX artifact
- `VERSION` / extension version 一致性检查
- tag / changelog / GitHub Release
- VSIX + source ZIP + SHA256SUMS release 产物

## P2.2：VS Code LSP 可靠性强化（已完成）

已建立 semantic contract v2，并把不同维度拆开表达：

`semantic_state`：

- `READY_WITH_RESULTS`
- `READY_EMPTY`
- `PROVIDER_NOT_AVAILABLE`
- `TIMEOUT`
- `WORKSPACE_MISMATCH`
- `INVALID_REQUEST`
- `PROVIDER_ERROR`

辅助状态：

- `document_state=DOCUMENT_OPEN / DOCUMENT_NOT_OPEN`
- `initial_semantic_state=LANGUAGE_SERVER_LOADING`
- `warmup_retry_attempted`
- `semantic_result_inconclusive`
- legacy Companion 兼容规范化

## P2.3：稳定性与跨平台

- 处理 pywinpty `ResourceWarning: unclosed socket`
- 日志轮转
- 多工作区
- 权限确认 UI
- Linux/macOS PTY 实现或明确 Windows-first 支持策略

## E2E

持续完成 ChatGPT → HTTPS → MCP → 本地工作区的读取、修改、命令、任务、Activity、Dashboard、IDE 语义端到端验证，并逐项与目标功能矩阵复核。

完整审查与实施说明见 `OPTIMIZATION_PLAN.md`。
