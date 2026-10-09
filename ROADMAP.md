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
- Activity log 轮转（已完成，并同步覆盖 Progress/Audit）
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

## P2.3：稳定性与跨平台（第一阶段已完成）

已完成：

- 修复 pywinpty 2.0.x `wait() -> closed=True -> close() no-op` 导致的 socket `ResourceWarning`；
- `TerminalService.shutdown()` 与 reader-thread 回收；
- Activity / Progress / Audit JSONL 有界轮转；
- 轮转后 seq 连续性与 `history_lost` 可见性；
- Linux/macOS 标准库 PTY 后端；
- CI Python matrix 扩展为 Windows / Ubuntu / macOS。

后续：

- 多工作区（已完成：显式 `workspace_id` / `WorkspaceRegistry`）；
- 权限确认 UI（已完成：localhost-only 一次性审批门控）；
- 更细的进程树/资源使用统计。


## P2.4：本机权限确认 UI（已完成）

- `ApprovalService`：pending / approved / denied / expired / consumed 生命周期；
- 动作 + 完整参数 SHA-256 fingerprint 绑定；
- 单次消费 + TTL；
- `write_file` / `apply_patch` / `run_command` 可选审批门控；
- Dashboard 待审批队列、批准一次/拒绝；
- localhost-only POST + 独立 Dashboard session token + Origin 校验；
- Launcher `--confirm-writes` / `--confirm-commands`；
- Activity 显示 approval_requested / approval_decided / approval_consumed；
- 默认关闭，保持已有自动化客户端兼容。

## P2.5：显式多工作区模型（已完成）

- `WorkspaceRegistry` 与显式 `workspace_id`；
- `default` 工作区保持原 `WORKSPACE_ROOT` 向后兼容；
- 文件、搜索、Patch、PTY、VS Code 工具按 workspace context 执行；
- 每个工作区独立 `WorkspaceGuard`，禁止跨 root 路径逃逸；
- PTY command_id 自动路由到所属 workspace；
- Activity / Dashboard / approvals 携带 workspace 归属；
- Dashboard 工作区页与聚合终端/VS Code 状态；
- Launcher `--extra-workspace ID=PATH` 与 `BRIDGE_WORKSPACES_JSON`；
- 多工作区 MCP + Dashboard E2E smoke。

## P2.6：工作区级权限策略（已完成）

- 全局 `ALLOW_WRITE` / `ALLOW_COMMANDS` 作为不可突破的权限上限；
- extra workspace 支持 `inherit / readonly / write / command / full`；
- `WorkspaceRegistry` 计算 effective permissions 并给每个 service context 注入；
- 禁止操作在 approval gate 之前返回 workspace-specific denial；
- approval fingerprint 已包含 workspace_id，不可跨 workspace 复用；
- Dashboard 显示 policy mode、有效权限与全局上限；
- Launcher `--workspace-policy ID=MODE`；
- 单元测试与真实 MCP/Launcher E2E 验证。

下一步：进程树/CPU/内存统计，以及更细的 Dashboard workspace/event 过滤与检索。

## E2E

持续完成 ChatGPT → HTTPS → MCP → 本地工作区的读取、修改、命令、任务、Activity、Dashboard、IDE 语义端到端验证，并逐项与目标功能矩阵复核。

完整审查与实施说明见 `OPTIMIZATION_PLAN.md`。
