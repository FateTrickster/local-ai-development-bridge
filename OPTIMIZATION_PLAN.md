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
