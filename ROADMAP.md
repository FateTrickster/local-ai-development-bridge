# Local MCP Bridge Roadmap

目标：在不依赖 MoonCode 授权体系的前提下，实现一套可长期维护的本地 AI Development Bridge，并吸收 MoonCode 在文件安全、IDE 语义、持久终端、任务状态和权限控制方面的优点。

## P0：可靠性与安全底座

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
   - Bearer Token
   - read/write/command 分权
   - 审计日志
   - 默认 localhost 监听

## P1：VS Code Companion

- diagnostics
- dirty editor buffer
- workspace/document symbols
- definition/references/implementation/hover
- provider ready/not_ready 状态

## P1：任务状态

- todos
- progress events
- 长任务 journal

## P2：产品化

- 配置文件与工作区管理
- 启动/停止/状态页
- 固定 Cloudflare Tunnel 或自有域名
- 自动重连
- 多工作区
- 日志轮转
- 权限确认 UI

## E2E

完成 ChatGPT → HTTPS → MCP → 本地工作区的读取、修改、命令、IDE 语义端到端验证，并逐项与 MoonCode 功能矩阵复核。
