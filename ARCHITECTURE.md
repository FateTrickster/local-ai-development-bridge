# Local AI Development Bridge 架构

## 1. 总体结构

```text
ChatGPT / 其他 MCP 客户端
        │
        │ HTTPS + capability path / Bearer token
        ▼
Cloudflare Quick Tunnel（可选，可替换）
        │
        ▼
127.0.0.1:8000
CapabilityAuthApp
        │
        ▼
FastMCP / Streamable HTTP
        │
        ├── WorkspaceRegistry
        │     ├── default → WorkspaceGuard / File / Patch / PTY / VS Code
        │     └── extra-* → WorkspaceGuard / File / Patch / PTY / VS Code
        ├── TaskService
        ├── ApprovalService
        └── ActivityService

Launcher / Tunnel Manager
        ├── cloudflared lifecycle
        ├── exact Host allowlist injection
        ├── Bridge process supervision
        ├── local/public MCP smoke tests
        └── .runtime/launcher-state.json

Local Dashboard :8766
        ├── Activity timeline
        ├── Todo / progress journal
        ├── Terminal snapshots
        ├── VS Code readiness
        ├── Launcher / Tunnel / smoke state
        └── localhost-only approval decisions
```

Dashboard 固定绑定 `127.0.0.1`，不会通过 MCP Quick Tunnel 暴露。

## 2. 服务层

### WorkspaceRegistry

`WORKSPACE_ROOT` 始终注册为 `workspace_id=default`，保持单工作区客户端向后兼容。额外根目录通过 `BRIDGE_WORKSPACES_JSON` 或 Launcher `--extra-workspace ID=PATH` 显式注册。workspace root 之间禁止重复或父子嵌套；每个 context 独立创建 WorkspaceGuard、FileService、PatchService、TerminalService 与 VSCodeService；工具只能通过显式 workspace_id 选择 context，不允许把多个 root 拼进一个 path 命名空间。

终端 command_id 由 Registry 反向定位所属 workspace；Dashboard 对命令和 VS Code health 做跨 workspace 聚合。审批 fingerprint 和 Activity details 同样携带 workspace_id，防止批准或审计语义跨工作区混淆。

每个 extra workspace 还可以声明 `allow_write` / `allow_commands`。有效权限计算遵循 `global_permission AND workspace_policy`：全局权限是硬上限，workspace 只能继续收紧，不能反向提升。`inherit` 不做额外限制；`readonly/write/command/full` 由 Launcher 规范化为两项布尔策略。

### WorkspaceGuard

所有工作区路径先 `resolve`，再检查目标仍位于 `WORKSPACE_ROOT` 内。禁止 `..` 越界和工作区外符号链接逃逸。

### FileService

提供稳定目录分页、glob 查找、literal/regex 搜索、批量读取、SHA-256 文件版本和可选 `expected_version` 写入保护。

### PatchService

使用 `expected_versions` 做乐观并发控制。统一 diff 在 `git apply --check` 通过后才执行；失败时尝试恢复受影响文件快照。

### TerminalService

Windows 使用 `pywinpty` 提供持久 PTY；Linux/macOS 使用标准库 `pty` + `subprocess` 适配器。统一提供后台命令、增量输出、stdin、wait、terminate、kill、有界缓冲和只读命令快照。Windows 端额外处理 pywinpty 的 socket 生命周期缺陷，Bridge 停止时统一执行 TerminalService shutdown。

### TaskService

持久化 todo 快照和 progress journal，用于长任务阶段与用户可见进度。

### ActivityService

把核心 MCP 工具、文件修改、终端和系统状态统一记录为结构化事件，并在写盘前执行敏感信息脱敏。

### VSCodeService / Companion

Python Bridge 通过 localhost-only Companion API 获取 VS Code diagnostics、dirty buffer 与 LSP provider 结果。Companion 使用本机 capability token 保护调用。

LSP 层使用 semantic contract v2：传输层 `provider_state` 与语义查询结果 `semantic_state` 分离；同时返回文档打开状态、语言 ID、warmup retry 与 timeout 信息。这样 `[]` 不再等同于“provider 正常但无内容”，可以区分真正空结果、provider 不可用和超时。旧 Companion response 由 Python Bridge 做兼容规范化。


### ApprovalService / Dashboard control boundary

审批门控默认关闭。开启后，敏感工具第一次调用只创建内存中的 approval request，不执行副作用。批准对象通过 `action + canonical payload` SHA-256 fingerprint 绑定，并且 one-time consume。Dashboard 的批准/拒绝是当前唯一写接口；它不通过 MCP Tunnel 暴露，要求独立 ephemeral dashboard token、自定义请求头和同源 Origin。

## 3. 安全边界

- MCP Server 默认只监听 `127.0.0.1`。
- 公网入口必须经过显式 Tunnel。
- Quick Tunnel hostname 由 Launcher 动态解析并精确加入 DNS rebinding Host allowlist，不使用 `*.trycloudflare.com` 通配符。
- capability URL 与 Bearer token 均可认证；未授权路径返回 404。
- `.runtime/` 与 `.audit/` 不进入 Git。
- Activity / Launcher 状态不保存 capability token 或完整 capability URL。
- `ALLOW_WRITE` 与 `ALLOW_COMMANDS` 独立控制，并作为所有 workspace 的硬上限；workspace policy 只能收紧，不能提升。
- 每个 workspace 使用独立 root guard；跨 workspace 访问必须显式切换 `workspace_id`，不能通过路径穿越。
- Dashboard localhost-only；唯一 mutation 是 approval approve/deny，不提供直接写文件或执行命令接口。

## 4. 运行时数据

```text
.runtime/access-token.txt      capability token
.runtime/todos.json            todo snapshot
.runtime/progress.jsonl        progress journal
.runtime/activity.jsonl        structured activity log
.runtime/launcher-state.json   launcher/tunnel/smoke state
.audit/requests.jsonl          HTTP security audit
```

以上目录均被 `.gitignore` 排除。Activity / Progress / Audit JSONL 使用统一轮转策略，默认每个 active segment 5 MiB、保留 3 个备份；读取接口跨保留分段聚合，并暴露 `earliest_seq/history_lost`。

## 5. 启动路径

推荐入口：

```text
start.cmd
  ↓
launcher.py start
  ↓
preflight
  ↓
Quick Tunnel
  ↓
extract exact hostname
  ↓
start server.py with exact allowlist
  ↓
wait Bridge / Dashboard
  ↓
local initialize → tools/list
  ↓
public initialize → tools/list
  ↓
ready / supervise children
```

手动 `start-full.cmd`、`start-readonly.cmd`、`start-tunnel.cmd` 保留用于调试与高级使用场景。

## 6. 构建与发布

- `.github/workflows/ci.yml`：Windows + Linux Python tests、secret scan、VS Code compile/VSIX artifact。
- `.github/workflows/release.yml`：`v*` tag 触发 Windows 验证、版本一致性检查、VSIX/source ZIP/SHA256SUMS 构建与 GitHub Release。
- 根目录 `VERSION` 与 `vscode-companion/package.json` version 必须保持一致。

## 7. Focused Dashboard projection

默认 Dashboard 不是 Activity 原始日志浏览器，而是一个只读投影层：`AITelemetryService + TaskService + Activity/Git file changes -> DashboardService.focus_snapshot() -> /api/focus -> dashboard/index.html`。完整 observability/control plane 保留在 `/advanced.html`。AI token telemetry 只有在兼容本地 client 自动提交 exact usage 时才显示，MCP Bridge 不做 Prompt 驱动的估算。


## Desktop completion notification

`set_todos` writes the durable task snapshot first, then `DesktopNotificationService` evaluates whether every task is `completed`. The service computes a stable completion signature, persists the last notified signature under `.runtime/desktop-notification-state.json`, and launches the platform notification backend asynchronously. This keeps completion alerts independent of prompt wording or chat continuity and avoids duplicate popups after retries or Bridge restarts.
