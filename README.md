# Local AI Development Bridge

一个独立于 MoonCode 授权/套餐体系的本地 MCP Bridge，用于让 ChatGPT 等 MCP 客户端在明确权限边界内访问本地项目。

当前已完成 P0 核心底座：

- 工作区路径隔离，禁止 `..` 越界与工作区外符号链接；
- 稳定分页目录浏览；
- glob 文件查找、exclude、隐藏/ignored 过滤；
- literal/regex 文本搜索、上下文与结果预算；
- 1–20 文件批量读取、行范围、原始字节 SHA-256 版本；
- `write_file` 可选 expected_version，阻止陈旧覆盖；
- 事务式 unified-diff `apply_patch`，要求 expected_versions，支持 VERSION_CONFLICT；
- Windows 原生 PTY 持久终端：后台运行、command_id、增量输出、wait、stdin、终止；
- 能力 URL Token / Bearer Token 双模式保护 MCP 入口；
- HTTP 请求审计日志，不记录能力 Token；
- 只读、写入、命令执行可独立控制；
- 结构化 Activity/Event 事件记录与脱敏；
- localhost-only 只读可观察性 Dashboard，可查看任务、活动、进度、终端和 VS Code 状态。

## 安装

当前版本主要在 Windows + Python 3.12 环境下开发和验证。

```powershell
git clone https://github.com/FateTrickster/local-ai-development-bridge.git
cd local-ai-development-bridge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如需通过 Cloudflare Quick Tunnel 从公网连接，还需要提前安装 `cloudflared`。不使用公网 Tunnel 时，可直接在本机 MCP 客户端中使用 `127.0.0.1` 地址。

## 启动

只读模式：

```bat
start-readonly.cmd
```

完整模式（允许写文件和执行命令）：

```bat
start-full.cmd
```

启动后终端会输出：

```text
Local MCP endpoint: http://127.0.0.1:8000/<capability-token>/mcp
Observability dashboard: http://127.0.0.1:8766/ (localhost-only, read-only)
```

`<capability-token>` 是访问凭据，不要提交到 Git，也不要公开分享。Dashboard 默认使用单独的 localhost 端口，不通过 MCP Quick Tunnel 暴露。

## 公网连接

另开终端执行：

```bat
start-tunnel.cmd
```

Cloudflare Quick Tunnel 会给出：

```text
https://xxxx.trycloudflare.com
```

ChatGPT 中实际填写的服务器 URL 为：

```text
https://xxxx.trycloudflare.com/<capability-token>/mcp
```

身份验证可以选择“无身份验证”，因为 Token 已经包含在能力 URL 中。支持自定义 Authorization Header 的客户端也可以直接访问 `/mcp` 并发送 `Authorization: Bearer <capability-token>`。

### 隧道主机必须加入 Host 白名单

`mcp` 库在服务绑定到 `127.0.0.1` 时会自动启用 DNS rebinding 保护，默认白名单仅含
`127.0.0.1` / `localhost` / `[::1]`。经 Quick Tunnel 访问时 `Host` 头是
`xxxx.trycloudflare.com`，不在白名单内，请求会被拒绝为：

```text
HTTP 421 Misdirected Request
```

解决办法是把隧道主机名加入白名单（保护保持开启，不要关闭）：

```powershell
$env:BRIDGE_ALLOWED_HOSTS = "xxxx.trycloudflare.com"
```

启动时可同时指定多个主机与来源：

```powershell
$env:BRIDGE_ALLOWED_HOSTS   = "xxxx.trycloudflare.com,another.example.com"
$env:BRIDGE_ALLOWED_ORIGINS = "https://xxxx.trycloudflare.com"
```

未设置时行为与库默认一致（仅允许本机）。**Quick Tunnel 每次重建都会换域名，域名变化后需要重新设置该变量并重启服务。**

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WORKSPACE_ROOT` | 当前目录的上级 | 工作区根，所有路径不得越出 |
| `ALLOW_WRITE` | `0` | 是否允许 `write_file` / `apply_patch` |
| `ALLOW_COMMANDS` | `0` | 是否允许 `run_command` |
| `BRIDGE_HOST` | `127.0.0.1` | MCP 监听地址 |
| `BRIDGE_PORT` | `8000` | MCP 监听端口 |
| `BRIDGE_TOKEN` | 空 | 覆盖持久 capability Token（默认从 `.runtime/access-token.txt` 读取或生成） |
| `BRIDGE_ALLOWED_HOSTS` | 空 | 追加到 DNS rebinding 保护的 Host 白名单 |
| `BRIDGE_ALLOWED_ORIGINS` | 空 | 追加到 DNS rebinding 保护的 Origin 白名单 |
| `BRIDGE_DASHBOARD_ENABLED` | `1` | 是否启动只读本地可观察性 Dashboard |
| `BRIDGE_DASHBOARD_PORT` | `8766` | Dashboard 首选 localhost 端口；占用时自动尝试后续端口 |

## 权限

`start-readonly.cmd`：

- `ALLOW_WRITE=0`
- `ALLOW_COMMANDS=0`

`start-full.cmd`：

- `ALLOW_WRITE=1`
- `ALLOW_COMMANDS=1`

两种模式默认都只监听 `127.0.0.1`，公网访问必须经过 Tunnel。

## 安全修改机制

读取文件会返回：

```text
version: sha256:...
```

修改时把该版本作为 `expected_version` / `expected_versions` 传回。若文件在读取后被用户或其他程序修改，Bridge 返回 `VERSION_CONFLICT`，不会覆盖新内容。

## 运行数据

- `.runtime/access-token.txt`：本机持久能力 Token，已被 `.gitignore` 排除；
- `.runtime/todos.json`：当前持久任务快照；
- `.runtime/progress.jsonl`：用户可读的进度事件；
- `.runtime/activity.jsonl`：结构化工具、终端、文件和系统状态事件，写入前会执行敏感信息脱敏；
- `.audit/requests.jsonl`：HTTP 安全审计日志，已被 `.gitignore` 排除；
- PTY 单命令输出缓冲默认最多 4 MiB，并使用绝对 UTF-8 字节 offset 增量读取。

## 可观察性 Dashboard

Bridge 启动时默认同时启动本地只读 Dashboard：

```text
http://127.0.0.1:8766/
```

如果 8766 已被占用，会依次尝试后续端口，并在启动终端打印实际地址。

Dashboard 当前显示：

- 当前 todo、完成度和进行中任务；
- 结构化 Activity Timeline；
- `progress.jsonl` 中的具体进度事件；
- 当前/最近 PTY 命令、状态、耗时和脱敏后的输出尾部；
- Bridge 工作区与权限；
- VS Code Companion ready/not_ready 状态。

Dashboard 只提供 GET 只读接口，固定绑定 `127.0.0.1`，与公网 MCP Tunnel 分离。MCP 另外提供：

- `get_activity`
- `get_progress_events`
- `dashboard_info`

用于客户端直接查询可观察性状态。

## VS Code Companion

P1 Companion 已实现服务端桥接与 VS Code 扩展源码。Bridge 暴露 `vscode_health`、`get_diagnostics`、`lsp`、`read_editor_buffer` 四个 IDE 语义工具。

扩展位于 `vscode-companion/`。开发构建：

```bat
cd vscode-companion
npm install
npm run compile
```

Companion 只监听 `127.0.0.1`，并使用本机持久 Token 保护 Bridge → VS Code 的 HTTP 调用。若扩展未运行，相关 MCP 工具返回 `VSCODE_COMPANION_NOT_RUNNING`，不会把缺失的 IDE 语义伪装成空结果。

## 测试

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前路线见 `ROADMAP.md`，本轮完整优化审查见 `OPTIMIZATION_PLAN.md`。下一重点是把 server、Quick Tunnel、动态 Host 白名单、VS Code Companion 检查和 smoke test 收敛为一键启动流程，并补 GitHub CI / Release。

## License

本项目使用 MIT License，详见 `LICENSE`。
