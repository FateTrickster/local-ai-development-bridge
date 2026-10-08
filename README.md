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
- 只读、写入、命令执行可独立控制。

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
```

`<capability-token>` 是访问凭据，不要提交到 Git，也不要公开分享。

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
- `.audit/requests.jsonl`：HTTP 审计日志，已被 `.gitignore` 排除；
- PTY 单命令输出缓冲默认最多 4 MiB，并使用绝对 UTF-8 字节 offset 增量读取。

## 测试

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前路线见 `ROADMAP.md`。下一阶段是 VS Code Companion（diagnostics、dirty buffer、LSP 语义）和任务进度系统。
