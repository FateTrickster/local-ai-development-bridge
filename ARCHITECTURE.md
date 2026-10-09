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
        ├── FileService / WorkspaceGuard
        ├── PatchService
        ├── TerminalService (persistent PTY)
        ├── TaskService
        ├── VSCodeService ──► VS Code Companion :8765
        └── ActivityService

Launcher / Tunnel Manager
        ├── cloudflared lifecycle
        ├── exact Host allowlist injection
        ├── Bridge process supervision
        ├── local/public MCP smoke tests
        └── .runtime/launcher-state.json

Local read-only Dashboard :8766
        ├── Activity timeline
        ├── Todo / progress journal
        ├── Terminal snapshots
        ├── VS Code readiness
        └── Launcher / Tunnel / smoke state
```

Dashboard 固定绑定 `127.0.0.1`，不会通过 MCP Quick Tunnel 暴露。

## 2. 服务层

### WorkspaceGuard

所有工作区路径先 `resolve`，再检查目标仍位于 `WORKSPACE_ROOT` 内。禁止 `..` 越界和工作区外符号链接逃逸。

### FileService

提供稳定目录分页、glob 查找、literal/regex 搜索、批量读取、SHA-256 文件版本和可选 `expected_version` 写入保护。

### PatchService

使用 `expected_versions` 做乐观并发控制。统一 diff 在 `git apply --check` 通过后才执行；失败时尝试恢复受影响文件快照。

### TerminalService

Windows 使用 `pywinpty` 提供持久 PTY：后台命令、增量输出、stdin、wait、terminate、有界缓冲和只读命令快照。非 Windows PTY 仍属于后续工作。

### TaskService

持久化 todo 快照和 progress journal，用于长任务阶段与用户可见进度。

### ActivityService

把核心 MCP 工具、文件修改、终端和系统状态统一记录为结构化事件，并在写盘前执行敏感信息脱敏。

### VSCodeService / Companion

Python Bridge 通过 localhost-only Companion API 获取 VS Code diagnostics、dirty buffer 与 LSP provider 结果。Companion 使用本机 capability token 保护调用。

## 3. 安全边界

- MCP Server 默认只监听 `127.0.0.1`。
- 公网入口必须经过显式 Tunnel。
- Quick Tunnel hostname 由 Launcher 动态解析并精确加入 DNS rebinding Host allowlist，不使用 `*.trycloudflare.com` 通配符。
- capability URL 与 Bearer token 均可认证；未授权路径返回 404。
- `.runtime/` 与 `.audit/` 不进入 Git。
- Activity / Launcher 状态不保存 capability token 或完整 capability URL。
- `ALLOW_WRITE` 与 `ALLOW_COMMANDS` 独立控制。
- Dashboard 仅 GET、localhost-only，不提供写文件或执行命令接口。

## 4. 运行时数据

```text
.runtime/access-token.txt      capability token
.runtime/todos.json            todo snapshot
.runtime/progress.jsonl        progress journal
.runtime/activity.jsonl        structured activity log
.runtime/launcher-state.json   launcher/tunnel/smoke state
.audit/requests.jsonl          HTTP security audit
```

以上目录均被 `.gitignore` 排除。

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
