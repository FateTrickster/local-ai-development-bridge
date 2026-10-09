# Local AI Development Bridge 优化审查与实施计划

更新时间：2026-10-09

## 1. 当前基线

当前公开稳定基线：

- GitHub：`FateTrickster/local-ai-development-bridge`
- 默认分支：`main`
- 稳定提交：`96c260d docs: prepare public MIT release`
- Python 单元测试：23/23 通过
- VS Code Companion：TypeScript 编译通过，真机连接已验证
- 本地 MCP、Cloudflare Quick Tunnel、公网能力 URL、文件/补丁/PTY/任务/VS Code Companion 链路均已完成基础 E2E 验证

当前代码已经具备较完整的“执行能力”，但产品层、可观察性、启动体验、分发与工程治理明显滞后。下一阶段重点不应继续堆叠 MCP 工具数量，而应优先解决“用户看不见系统在做什么”的黑匣子问题，并把项目整理成普通用户能够安装、启动、诊断和维护的产品。

## 2. 本轮审查发现的主要问题

### 2.1 P0：可观察性不足，系统呈现明显黑匣子特征

当前已有 `TaskService`，会持久化：

- `.runtime/todos.json`
- `.runtime/progress.jsonl`

MCP 也已有：

- `set_todos`
- `report_progress`
- `get_task_state`

但目前 `get_task_state()` 只返回 todo 快照和 progress 事件数量，不返回具体进度事件。实际使用时无法直接看到：

- 当前正在执行哪一步
- 刚刚调用了什么工具
- 正在读取或修改哪些文件
- 当前命令运行到哪里
- 命令是否失败及失败原因
- 本轮修改了哪些文件、增删多少内容
- 测试是否正在运行、是否通过
- 当前 VS Code Companion / Tunnel / MCP 是否在线

现有 `.audit/requests.jsonl` 主要解决认证与访问审计，只记录 request allowed/denied、路径、认证模式、客户端等，不是面向用户的工作轨迹。

VS Code Companion 当前也没有 Status Bar、Tree View、Webview 或独立控制台，因此虽然后台能力已存在，用户仍然无法直观看到运行状态。

### 2.2 任务进度机制存在，但使用率和信息密度过低

当前 progress journal 记录能力已经实现，但事件写入依赖调用方主动执行 `report_progress`。实际开发过程中事件非常稀疏，无法形成完整工作轨迹。

需要把“任务进度”从人工补充信息升级为系统自动生成的结构化活动记录。

### 2.3 终端具备持久 PTY，但缺少可视状态汇总

`TerminalService` 已具备：

- command_id
- 后台运行
- 增量 stdout/stderr
- wait
- stdin
- terminate
- 有界缓冲

但没有一个面向用户的“当前命令列表”和“最近命令状态”视图。用户只能在知道 command_id 的情况下主动查询单个命令。

应新增只读命令快照能力，用于控制台展示：

- command_id
- 命令摘要
- cwd
- running/completed/failed
- exit code
- started_at / elapsed
- 最近输出尾部

### 2.4 一键启动能力不足

当前启动仍由多个脚本和环境变量组合完成：

1. 启动 MCP Server
2. 启动 cloudflared
3. 获取新的 Quick Tunnel 域名
4. 手动写入 `BRIDGE_ALLOWED_HOSTS`
5. 重启 Server
6. 检查 VS Code Companion
7. 拼接 capability URL

对项目作者可以接受，但对 GitHub 普通用户门槛过高。

### 2.5 Tunnel 生命周期仍然需要人工协调

Quick Tunnel 每次重建域名变化，当前 DNS rebinding Host 白名单必须同步更新并重启 MCP Server。

后续应实现 Tunnel Manager：

- 启动 cloudflared
- 捕获实际公网 hostname
- 自动设置精确 Host allowlist
- 再启动 MCP Server
- 执行 initialize → tools/list smoke test
- 输出脱敏公网状态

### 2.6 GitHub 工程化不足

当前没有 `.github/workflows`，缺少：

- Python 单元测试 CI
- TypeScript 编译 CI
- Secret scan
- Release 自动化
- VSIX 构建 artifact
- 版本 tag / changelog 流程

当前代码可以开源使用，但还不是成熟的可分发项目。

### 2.7 VS Code LSP 状态语义仍不够清晰

当前 Companion 可以返回 `provider_state=ready`，但 `document_symbols`、`hover` 等调用可能返回空数组。空数组目前不能区分：

- Provider 正常且确实无结果
- 对应语言 Provider 尚未注册
- Language Server 尚在加载
- 文档未打开/未激活语言服务
- Provider 调用异常但被归一化为空结果

后续应形成更明确的状态枚举，例如：

- `READY_WITH_RESULTS`
- `READY_EMPTY`
- `PROVIDER_NOT_AVAILABLE`
- `LANGUAGE_SERVER_LOADING`
- `DOCUMENT_NOT_OPEN`
- `WORKSPACE_MISMATCH`
- `TIMEOUT`

### 2.8 工程债：pywinpty ResourceWarning

当前 23 个 Python 测试全部通过，但终端相关测试仍会出现 `ResourceWarning: unclosed socket`。

目前不影响功能正确性，但属于需要清理的资源释放问题，尤其在长时间运行、频繁创建 PTY 时应继续观察。

### 2.9 当前平台能力以 Windows 为主

`TerminalService` 的非 Windows PTY 当前明确返回 `PTY_BACKEND_NOT_IMPLEMENTED_ON_THIS_PLATFORM`。

开源后若希望扩大用户面，需要后续实现 Unix PTY 或明确标注 Windows-first 支持策略。

## 3. 优化优先级

| 优先级 | 模块 | 目标 |
| --- | --- | --- |
| P0 | 可观察性 / Activity / 可视化工作控制台 | 解决“系统在做什么完全看不见”的核心痛点 |
| P1 | 一键启动 + Tunnel Manager | 降低 GitHub 用户部署与连接门槛 |
| P2 | GitHub CI / Release / VSIX | 从源码项目升级为可持续发布项目 |
| P3 | VS Code LSP 可靠性与状态语义 | 降低 IDE 语义层的黑匣子程度 |
| P4 | 工程稳定性与跨平台 | 清理资源泄漏警告、扩展 Windows 之外的平台 |

## 4. P0 目标：从黑匣子变成可观察系统

P0 不只是“画一个进度条”，而是建立统一 Observability 基础层。

### 4.1 统一 Activity/Event 模型

新增 `ActivityService`，将关键行为统一记录为结构化事件：

- `tool_started`
- `tool_completed`
- `tool_failed`
- `file_changed`
- `command_started`
- `command_completed`
- `task_updated`
- `progress_reported`
- `system_state`

建议事件最少包含：

- seq
- activity_id
- timestamp
- type
- status
- tool / component
- title
- details（必须经过脱敏）
- duration_ms（结束事件）

活动记录持久化到：

`\.runtime\activity.jsonl`

不记录 capability token、Authorization、完整文件内容、patch 正文等敏感信息。

### 4.2 任务进度可读化

增强 `TaskService`：

- 提供最近 progress event 查询
- 支持 `after_seq`
- Dashboard 可直接展示完整进度时间线

### 4.3 终端状态快照

增强 `TerminalService`，增加只读 snapshot：

- 最近命令
- 当前运行状态
- exit code
- elapsed
- 输出 tail

命令文本只保留经过脱敏的摘要，避免把 token/password/secret 写进可视日志。

### 4.4 本地只读可视化控制台

首版 Dashboard 只绑定：

`127.0.0.1`

不经过 Quick Tunnel，不开放写操作，不承担远程控制职责。

建议默认端口：`8766`，端口被占用时自动尝试后续端口。

首版页面包含：

1. 当前任务与 todo 状态
2. 最近 Activity 时间线
3. 当前/最近终端命令
4. MCP/VS Code/权限状态
5. 自动轮询刷新

### 4.5 P0 验收标准

P0 完成时必须满足：

1. 启动 Bridge 后终端明确打印 Dashboard 本地地址。
2. 浏览器访问 Dashboard 可以看到当前 task/todo。
3. ChatGPT 调用核心 MCP 工具时产生 Activity 事件。
4. 执行命令时 Dashboard 可以看到 running → completed/failed。
5. `report_progress` 后 Dashboard 可以看到具体 progress event，而不只是计数。
6. VS Code Companion ready/not_ready 在 Dashboard 中可见。
7. 事件日志不包含 capability token。
8. 单元测试全部通过。

## 5. P0 之后的建议实施顺序

### P1：一键启动与 Tunnel Manager

目标：一个入口完成环境检查、Tunnel、Host 白名单、MCP Server、VS Code Companion 检查和 smoke test。

### P2：GitHub CI / Release

目标：PR 自动测试；tag 自动产出源码包、VSIX 与 Release notes。

### P3：LSP 状态细化

目标：把空结果与 provider 未准备好明确区分，减少 IDE 语义黑匣子。

### P4：稳定性与跨平台

目标：清理 PTY socket ResourceWarning，设计 Linux/macOS PTY 实现。

## 6. 本轮立即实施范围

本轮直接进入 P0 实践优化，计划完成：

1. 新增 Activity/Event 基础服务。
2. 扩展 TaskService 的 progress event 查询。
3. 增加 TerminalService 命令状态快照。
4. 新增 localhost-only 只读 Dashboard。
5. 将核心 MCP 工具接入 Activity 自动记录。
6. 增加相关自动化测试。
7. 更新 README/ROADMAP。
8. 在独立 feature branch 上形成稳定提交，再决定是否合并 `main`。

## 7. 本轮 P0 实施结果（2026-10-09）

本轮已经完成第一版可观察性实践优化：

- 新增 `bridge/activity_service.py`，建立统一结构化 Activity/Event 日志与敏感信息脱敏。
- TaskService 新增具体 progress event 查询和 `after_seq` 增量读取。
- TerminalService 新增命令快照，并自动记录 command started/completed/failed。
- 新增 `bridge/dashboard_service.py` 与 dashboard/index.html，Dashboard 固定绑定 127.0.0.1、只读、与公网 MCP Tunnel 分离。
- server.py 将核心文件、搜索、修改、终端、任务和 VS Code 工具接入 Activity lifecycle。
- 新增 MCP 工具：get_activity、get_progress_events、dashboard_info。
- Dashboard 可以显示 todo、进度记录、Activity Timeline、终端命令快照、Bridge 权限与 VS Code Companion 状态。
- 新增 Activity 与 Dashboard 自动化测试，并扩展 Task/Terminal 测试。
- README 与 ROADMAP 已同步更新。

验证结果：

- Python tests：31/31 通过。
- compileall：通过。
- 独立临时端口 E2E smoke：23 个 MCP 工具可见；Activity 生命周期完整；Dashboard API 正常；真实终端命令能够在 Dashboard 中显示 completed 状态。
- OBSERVABILITY_SMOKE_OK。

仍保留的已知工程债：pywinpty 测试过程仍有 ResourceWarning: unclosed socket，不影响当前 31 项测试通过，但列入后续稳定性优化。



## 8. P1 一键启动与 Tunnel Manager 实施结果（2026-10-09）

P1 已完成第一版产品化启动链：

- 新增 `bridge/tunnel_manager.py`，自动发现 cloudflared、创建 Quick Tunnel、解析真实公网域名并监督进程生命周期。
- 修复 Quick Tunnel URL 解析边界：明确排除 `api.trycloudflare.com`，避免把 Cloudflare API 请求地址误判为实际 Tunnel。
- 新增 `launcher.py`：提供 `start`、`doctor`、`status` 三类入口。
- 新增 `start.cmd` 与 `start-readonly-all.cmd`，普通用户不再需要手工协调 server / cloudflared / Host allowlist。
- Launcher 只把当前真实 Tunnel hostname 写入 `BRIDGE_ALLOWED_HOSTS`，清除历史 stale Quick Tunnel host，继续保持 DNS rebinding protection。
- Launcher 自动启动 Bridge + Dashboard，执行本地与公网 `initialize -> tools/list` smoke test。
- 新增 `bridge/launcher_state.py`，把非敏感启动状态持久化到 `.runtime/launcher-state.json`。
- Dashboard 新增 Quick Tunnel 卡片和 Launcher / Tunnel 状态页，可查看启动阶段、public origin、进程和 local/public smoke 结果。
- Launcher 退出时统一回收 Bridge 与 cloudflared 子进程。

验证结果：

- Python tests：40/40 通过。
- Python compileall：通过。
- Local-only launcher E2E：通过。
- 真实 Quick Tunnel E2E：通过。
- Cloudflare edge 注册：通过。
- Local MCP initialize/tools-list：23 tools，通过。
- Public MCP initialize/tools-list：23 tools，通过。
- 精确 Tunnel Host allowlist：通过；未继续继承旧 Quick Tunnel hostname。

P1 验证过程中发现并修复了一个真实缺陷：最初的 URL 正则会从 cloudflared 错误日志中把 `https://api.trycloudflare.com` 误识别成 Quick Tunnel origin。修复后仅接受实际生成的 Quick Tunnel hostname，并重新完成真实公网 E2E。

下一阶段进入 P2：GitHub CI / Release / VSIX 分发自动化。


## 9. P2 GitHub CI / Release / VSIX 实施结果（2026-10-09）

P2 已完成第一版持续集成与可分发构建链：

- 新增 `.github/workflows/ci.yml`：Windows / Linux Python 3.12 test matrix、compileall、Secret Scan、VS Code compile 与 VSIX artifact。
- 新增 `.github/workflows/release.yml`：`v*` tag 触发 Windows 完整测试、版本一致性校验、VSIX / source ZIP / SHA256SUMS 构建并创建或更新 GitHub Release。
- 新增根 `VERSION`，当前为 `0.1.0`；测试会校验其与 `vscode-companion/package.json` version 一致。
- 新增 `CHANGELOG.md`。
- 新增 `scripts/secret_scan.py`，扫描 tracked 与 untracked non-ignored 文件；支持 capability URL、GitHub token、OpenAI-style key、AWS access key、Bearer token 与常见 secret assignment 规则；输出只报告文件、行号和规则，不回显凭据值。
- Secret Scan 会额外检查本机 `.runtime/access-token.txt` 中的真实 capability token 是否意外出现在待提交文件。
- VS Code Companion 加入 `@vscode/vsce`、repository/license/homepage metadata、`vscode:prepublish`、`.vscodeignore`、独立 README/LICENSE。
- 更新 `ARCHITECTURE.md`，使开源文档与当前真实架构一致。

本地验证结果：

- Python tests：47/47 通过。
- Secret Scan：通过。
- TypeScript compile：通过。
- `vsce package`：成功生成有效 VSIX；包内仅保留 package metadata、README、LICENSE 与编译后的 extension.js。
- Release metadata/version 一致性测试：通过。

当前 GitHub Actions workflow 文件已经具备执行条件；正式推送到 GitHub 后，下一步应通过实际 PR / Actions run 再做一次云端验证。

下一阶段进入 P2.2 / P3：VS Code LSP provider 状态语义细化，重点解决 `provider_state=ready + results=[]` 仍存在解释歧义的问题。


## 10. P3 VS Code LSP 语义状态实施结果（2026-10-09）

P3 已针对“`provider_state=ready` 但 `results=[]` 到底意味着什么”的黑匣子问题完成 semantic contract v2：

- 保留 `provider_state=ready/not_ready` 表示 Companion/请求层是否可用。
- 新增 `semantic_state`：`READY_WITH_RESULTS`、`READY_EMPTY`、`PROVIDER_NOT_AVAILABLE`、`TIMEOUT`、`WORKSPACE_MISMATCH`、`INVALID_REQUEST`、`PROVIDER_ERROR`。
- 新增 `document_state=DOCUMENT_OPEN/DOCUMENT_NOT_OPEN`，明确查询前文档是否已经打开。
- 对未打开文档自动 `openTextDocument`，等待 350 ms 再重试一次 provider；如果第一次没有 provider response，则记录 `initial_semantic_state=LANGUAGE_SERVER_LOADING` 与 `warmup_retry_attempted=true`。
- provider query 增加 3000 ms 上限，超时显式返回 `TIMEOUT`，不再无限等待。
- 新 Companion 在 `/health` 公布 `semantic_contract_version=2`、timeout、warmup retry 与状态字典，Dashboard 可直接看到当前 contract。
- Python `VSCodeService` 对新版 response 原样保留；对旧 Companion response 做兼容规范化。旧版非空结果映射 `READY_WITH_RESULTS`；旧版空结果映射 `READY_EMPTY` 但强制 `semantic_result_inconclusive=true`，避免把历史的 `undefined -> []` 行为误认为确定的空结果。
- LSP Activity completion 现在同时记录 `semantic_state`、document state、language ID 和 warmup retry，Dashboard 时间线可直接看到。

真机验证：

- 新 Companion `/health` 已返回 semantic contract v2。
- 对未打开的 TypeScript 文件执行 `document_symbols` 时，真实返回 `DOCUMENT_NOT_OPEN` → warmup retry → `PROVIDER_NOT_AVAILABLE`，同时保留 `initial_semantic_state=LANGUAGE_SERVER_LOADING`，证明原先的“空数组黑匣子”已被拆开。
- `workspace_symbols` 在 provider command 成功返回空列表时真实返回 `READY_EMPTY` 且 `semantic_result_inconclusive=false`。
- TypeScript compile 通过。
- Python VSCodeService 专项测试通过。

下一阶段进入稳定性优化：优先处理 pywinpty socket `ResourceWarning`、Activity/progress/audit 日志轮转，以及 Linux/macOS PTY 支持边界。


## 11. P4 稳定性与跨平台第一阶段实施结果（2026-10-09）

本阶段处理了三个长期工程问题：PTY 资源释放、运行日志无限增长和非 Windows 终端缺失。

### 11.1 pywinpty socket ResourceWarning 根因与修复

通过直接检查 pywinpty 2.0.x `PtyProcess` 实现确认根因：`wait()` 循环调用 `isalive()`；child 退出时 `isalive()` 会把 `self.closed=True`。随后 `PtyProcess.close()` 因 `if not self.closed` 条件不成立而直接跳过，因此 `_server` 与 `fileobj` 两个 localhost socket 没有关闭，直到 GC 才产生 `ResourceWarning`。

Bridge 现在在 PTY finalize 时：

1. 不依赖 pywinpty 的 `closed` 标志；
2. 无条件、幂等关闭 `fileobj` 和 `_server`；
3. 再调用库自身 `close()`；
4. 等待 pywinpty 内部 socket reader thread 退出；
5. `TerminalService.shutdown()` 统一终止剩余命令并等待 Bridge 自己的 reader threads。

Windows 专项终端测试开启 `ResourceWarning` 后，5/5 通过且不再输出 socket warning。

### 11.2 JSONL 有界轮转

新增 `bridge/jsonl_utils.py`，统一为以下日志执行轮转：

- `.runtime/activity.jsonl`；
- `.runtime/progress.jsonl`；
- `.audit/requests.jsonl`。

默认 active segment 上限 5 MiB、保留 3 个备份，可通过 `BRIDGE_LOG_MAX_BYTES` 与 `BRIDGE_LOG_BACKUPS` 调整。Activity/Progress 查询会跨当前保留的 rotated segments 读取；seq 在重启后仍从保留分段最大值继续。若调用方的 `after_seq` 已早于保留窗口，返回 `history_lost=true` 与 `earliest_seq`，避免日志轮转再次形成静默数据缺口。Dashboard 系统状态显示当前轮转策略。

### 11.3 Linux/macOS PTY

新增 `bridge/unix_pty.py`，使用标准库 `pty.openpty`、`subprocess.Popen`、`select` 和 process group signal 实现与 Windows pywinpty 对齐的接口：read/write/isalive/wait/terminate/kill/close。TerminalService 现在按平台选择后端，现有终端集成测试改为 Windows/Linux/macOS 共用。GitHub CI matrix 增加 `macos-latest`，Ubuntu/macOS 将真实执行终端测试而不再 skip。

本地 Windows 验证：

- Python tests：54/54 通过；
- `ResourceWarning` 显式开启：无 socket warning；
- log rotation 专项测试：通过；
- Secret Scan：通过。

Linux/macOS 后端最终以 GitHub Actions 云端 matrix 结果作为验收依据。

下一阶段：多工作区模型与权限确认 UI。


## 12. P5 本机权限确认 UI 实施结果（2026-10-09）

为了进一步降低“AI 已经开始执行，但用户不知道也无法介入”的黑匣子风险，本阶段增加可选的人类审批门控。

### 12.1 审批模型

新增 `bridge/approval_service.py`。每个请求具有 `pending -> approved/denied/expired -> consumed` 生命周期。批准不保存真实操作内容，而是对 `action + canonical payload` 计算 SHA-256 fingerprint；Dashboard 只展示脱敏后的 path、command preview、字节数等摘要。批准与完整参数绑定，如果客户端在批准后改变命令、文件内容、expected version 或其他参数，会返回 `APPROVAL_FINGERPRINT_MISMATCH`。批准只能使用一次，并受 TTL 约束。

### 12.2 工具门控

可通过 `BRIDGE_CONFIRM_WRITES=1` 与 `BRIDGE_CONFIRM_COMMANDS=1` 开启，也可在 launcher 使用 `--confirm-writes --confirm-commands`。当前覆盖：

- `write_file`；
- `apply_patch`；
- `run_command`。

第一次调用返回 `approval_required=true`；用户在本机 Dashboard 批准后，客户端以完全相同参数加 `approval_id=request_id` 重试才实际执行。默认关闭，因此不破坏现有自动化客户端。

### 12.3 Dashboard 安全边界

Dashboard 保持绑定 `127.0.0.1` 且不经过 Quick Tunnel。新增的 approve/deny POST：

- 要求 `X-Bridge-Dashboard-Token` 独立 ephemeral session token；
- session token 只能从 localhost Dashboard 同源 GET 获取；
- 校验 Host 只能是 `127.0.0.1/localhost`；
- 浏览器携带 Origin 时要求严格同源；
- mutation 仅限审批状态，不提供直接文件写入或命令执行 HTTP API。

### 12.4 可视化

Dashboard 增加“待审批”摘要卡和“审批”页，可以看到 pending/approved/denied/consumed 状态，并进行“批准一次/拒绝”。Activity Timeline 同时记录 `approval_requested`、`approval_decided`、`approval_consumed`。

本地验证：

- Python tests：61/61 通过；
- ApprovalService 生命周期、fingerprint、one-time、expiry、redaction 测试通过；
- Dashboard 缺少 session token 与 cross-origin POST 均返回 403；
- 真实 MCP E2E：`write_file -> approve -> retry`、`apply_patch -> approve -> retry`、`run_command -> approve -> retry` 全链路通过；
- Secret Scan、TypeScript compile、Dashboard JS syntax 均通过。

下一阶段：多工作区模型。建议不要把多个 root 硬塞进现有 path 字符串，而是引入显式 `workspace_id` / workspace registry，使权限、Activity、审批与 VS Code workspace 都能够按 workspace 归属。

## 13. P6 显式多工作区模型实施结果（2026-10-09）

本阶段把原先单一 `WORKSPACE_ROOT` 模型升级为显式 workspace registry，同时保留所有旧客户端默认行为。

### 13.1 WorkspaceRegistry

新增 `bridge/workspace_registry.py`：

- `WORKSPACE_ROOT` 固定注册为 `workspace_id=default`；
- 额外工作区通过 `BRIDGE_WORKSPACES_JSON` 注册；
- workspace id 有严格格式与唯一性检查；
- root 必须真实存在、不能重复，也不能与其他 workspace root 形成父子嵌套；
- 每个 workspace context 独立持有 WorkspaceGuard、FileService、PatchService、TerminalService 与 VSCodeService。

所有路径仍然是“相对当前 workspace root”的路径，不建立跨 root 的虚拟路径层，因此原有 path traversal / symlink escape 安全边界继续成立。

### 13.2 MCP 工具与审批/Activity

新增 `list_workspaces`。文件、搜索、Patch、`run_command`、diagnostics、LSP 与 editor buffer 工具新增可选 `workspace_id`；省略时使用 `default`。终端后续操作通过 `command_id` 自动回到创建命令的 workspace，不要求客户端重复传 workspace_id。

`write_file` / `apply_patch` / `run_command` 的 approval fingerprint 现在包含 workspace_id，批准不能在另一个 workspace 复用。Activity 与 Terminal snapshots 同步记录 workspace_id。

### 13.3 Dashboard 与 Launcher

Dashboard 新增 Workspaces 页与 `/api/workspaces`，并聚合所有 workspace 的 terminal commands 与 VS Code health。

Launcher 新增可重复参数：

```text
--extra-workspace ID=PATH
```

启动前校验 ID、目录存在性、root/ID 重复；然后只向 server 注入规范化后的 `BRIDGE_WORKSPACES_JSON`。旧环境未配置 extra workspace 时不设置该变量。

### 13.4 验证

- Python tests：70/70 通过；
- WorkspaceRegistry 专项：默认兼容、显式选择、跨 root 路径阻断、command_id workspace routing 全部通过；
- 真实 MCP E2E：24 tools；`list_workspaces` 返回 default/docs；同名文件按 workspace 隔离读取；docs workspace 写入不会落到 default；PTY 命令输出自动标记 docs；Activity 可见 workspace_id；
- Dashboard E2E：workspace_count=2，聚合命令包含 workspace_id；
- Launcher --no-tunnel --exit-after-ready + --extra-workspace docs=... 真机启动通过，24 tools smoke 通过；
- `P6_MULTI_WORKSPACE_SMOKE_OK`。

下一阶段建议：工作区级权限策略（例如某 workspace 只读、另一个允许命令）、进程树/CPU/内存统计，以及 Dashboard 的 workspace 过滤器。

## 14. P7 工作区级权限策略实施结果（2026-10-09）

P6 建立显式 workspace_id 后，本阶段继续把权限边界下沉到 workspace context，避免“一个全局 full 权限让所有挂载目录都拥有同样副作用能力”。

### 14.1 权限模型

全局 `ALLOW_WRITE` / `ALLOW_COMMANDS` 保持硬上限。每个 extra workspace 可声明 `allow_write` / `allow_commands`，最终有效权限为：

```text
effective = global_permission AND (workspace_requested_permission if explicitly set else true)
```

未声明时继承全局。Launcher 将用户友好的模式映射为策略：`inherit`、`readonly`、`write`、`command`、`full`。即使 workspace 请求 `full`，也不能把全局关闭的权限重新打开。

### 14.2 执行与审批顺序

`write_file`、`apply_patch`、`run_command` 在进入 approval gate 之前先检查 selected workspace 的 effective permission。被策略禁止时直接返回 `WRITES_DISABLED_FOR_WORKSPACE` 或 `COMMANDS_DISABLED_FOR_WORKSPACE`，并写入 `workspace_policy_denied` Activity。这样 Dashboard 不会出现一个实际上无法执行的无效审批请求。

Approval fingerprint 继续包含 workspace_id，因此同样参数在不同 workspace 也被视为不同副作用请求。

### 14.3 Launcher 与 Dashboard

Launcher 新增可重复参数：

```text
--workspace-policy ID=MODE
```

其中 MODE 为 `inherit|readonly|write|command|full`。未知 workspace、重复 policy 和非法 mode 会在 preflight 阶段失败。

Dashboard Workspaces 页显示 policy mode、effective write/command permissions 与 global ceiling。

### 14.4 验证

- Python tests：75/75 通过；
- Registry 测试覆盖 readonly/write 策略与“global readonly + workspace full 仍然不可提升”的硬上限；
- Launcher policy 解析、未知 ID、重复策略、非法 mode 测试通过；
- 真实 MCP policy smoke：3 个 workspace；readonly workspace 的 write/command 均在 approval 前被拒绝，write-only workspace 的 command 被拒绝而 write 正常进入 approval gate；仅产生 1 个真实可执行审批请求；
- Activity 出现 3 条 `workspace_policy_denied`；
- Launcher `--workspace-policy docs=readonly` 真机 local E2E 启动通过，24 tools initialize/tools-list smoke 通过。

下一阶段：进程树/CPU/内存可观察性，以及 Dashboard workspace/event 过滤与检索。

## 15. P8 聚焦型动态 Dashboard（2026-10-09）

根据实际使用反馈，原 Dashboard 虽然已经具备 Activity、Terminal、Workspace、Approval、Launcher、VS Code 等大量可观察信息，但主界面信息密度过高。P8 将默认界面收敛为四类真正需要持续关注的信息，其他能力继续记录并保留在高级页面，不再占据默认视图。

### 15.1 默认页面只展示四类信息

1. **AI 输出**：近 1 min / 5 min 输出 token 数，以及最近一次输出 TPS；
2. **阶段用时**：大目标、分目标、小目标的实时用时；
3. **任务规划与分级**：持久化任务树，当前正在处理的最细分支标绿；
4. **文件变更**：新增 / 修改 / 删除文件、workspace、相对路径与完整绝对地址。

默认页面为 `dashboard/index.html`，持续轮询 `/api/focus`。此前完整控制台被保留为 `dashboard/advanced.html`，审批等高级能力没有删除，只是不再出现在默认页面。

### 15.2 可视化仍由程序驱动，而不是 Prompt 驱动

- `TaskService` 将 `parent_id`、`created_at`、`started_at`、`completed_at` 持久化到 `.runtime/todos.json`；
- 一个活动分支可以由“总任务 -> 分任务 -> 当前叶子”多级 `in_progress` 组成，但不允许平行分支同时处于 `in_progress`；
- `get_state()` 自动计算 `active_path`、`active_leaf_id`、层级与 elapsed time；
- 文件变更来自结构化 `file_changed` Activity，并额外读取 workspace 内 Git worktree 状态，从而覆盖通过终端脚本造成的未提交修改；
- Dashboard 每 1.5 秒读取 `/api/focus`，不需要模型主动修改 HTML。

### 15.3 AI 输出量 / TPS 的边界

MCP Server 本身不会收到 ChatGPT 助手的 token streaming，因此不能从普通 MCP tool traffic 得到官方精确 TPS。当前策略调整为：**允许为了状态反馈提供近似值，但必须明确标记为估算，不能冒充官方 usage。**

`AITelemetryService` 与 `.runtime/ai-output.jsonl` 继续负责 1 min / 5 min 聚合。新增 `browser-telemetry/` Chrome / Edge 扩展，在 ChatGPT 网页侧观察最新 assistant 消息的文本增长，并采用非常简单的启发式：CJK 字符约按 1 token / 字符，其他非空白字符约按 4 字符 / token。扩展约每 1.2 秒发送新增估算 token 与时长，因此默认 Dashboard 可以持续出现一个“≈ TPS”数字。

浏览器扩展只发送数字增量与耗时，不发送 assistant 正文。Dashboard 的 `/api/session` 提供独立 ephemeral `telemetry_token`；扩展用该 token 向 localhost-only `/api/telemetry/ai-output` 上报，和审批用的 Dashboard token 分离。扩展自动探测 `8766`–`8776` 端口。

若未来宿主客户端能够提供官方 `output_tokens` / usage，则仍可向同一接口提交精确数据；Dashboard 会根据 source 区分“估算 / 实测”。因此这个方案的目的只是让用户看到持续变化的速度数字并确认系统仍在工作，而不是用于计费或性能基准。

### 15.4 文件变更投影

`write_file` / `apply_patch` 的 `file_changed` 事件现在包含 action、相对路径与绝对地址。Dashboard 同时探测 workspace 本身或其一级子目录中的 Git repository，通过 `git status --short` 自动补足终端命令造成的当前未提交变更。因此即使某次文件修改不是通过 File/Patch MCP tool 完成，也能在默认页面的“文件变更”区域看到。

### 15.5 当前验收

- 层级任务与阶段计时专项测试通过；
- AI telemetry 1 min / 5 min / TPS 统计测试通过；
- 默认 Dashboard `/api/focus`、高级页面与审批接口兼容测试通过；
- 默认页面 JavaScript 语法检查通过；
- 完整 Python 回归 84/84 通过；`compileall`、Dashboard JavaScript syntax check、Secret Scan、VS Code Companion compile 均通过。
