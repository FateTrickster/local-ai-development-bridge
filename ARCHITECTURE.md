# 架构

ChatGPT / 其他 MCP 客户端
        ↓ HTTPS
Cloudflare Tunnel（可替换）
        ↓ localhost
Python MCP Server（Streamable HTTP）
        ↓
Workspace Guard（路径限制）
        ↓
本地工作区文件 / 受控终端

## 第一阶段最小工具

1. list_directory：列目录
2. read_file：读取 UTF-8 文本
3. search_files：文本搜索
4. write_file：创建/覆盖 UTF-8 文本（可关闭）
5. run_command：在工作区内执行命令（可关闭）

## 安全边界

- 所有路径先 resolve，再确认仍位于 WORKSPACE_ROOT 内。
- 默认不跟随工作区外的路径。
- 写入能力由 ALLOW_WRITE 控制。
- 命令执行由 ALLOW_COMMANDS 控制。
- 服务默认只监听 127.0.0.1，由 Tunnel 对外暴露。
- 不复用、提取或修改 MoonCode 的授权逻辑。
