# Local AI Bridge Telemetry 浏览器扩展

这是一个刻意保持简单的 Chrome / Edge Manifest V3 扩展，用于让本地 Dashboard 持续出现一个“AI 输出速度”数字。

它不是 OpenAI 官方 usage 计量器。扩展观察 ChatGPT 网页中最新 assistant 消息的文本增长，并用一个很粗的启发式估算 token：

- 中日韩字符约按 1 token / 字符；
- 其他非空白字符约按 4 字符 / token。

扩展约每 1.2 秒把新增的估算 token 和对应时长发送到 Local AI Bridge 的 localhost-only telemetry API，因此 Dashboard 可以实时显示近 1 min / 5 min 输出量和“约 TPS”。

## 安装

1. 确保 Local AI Bridge 正在运行，并能打开 `http://127.0.0.1:8766/`。
2. Edge 打开 `edge://extensions/`；Chrome 打开 `chrome://extensions/`。
3. 开启“开发人员模式 / Developer mode”。
4. 选择“加载解压缩的扩展 / Load unpacked”。
5. 选择本目录 `browser-telemetry`。
6. 刷新已经打开的 `https://chatgpt.com/` 页面。

扩展会自动尝试 Dashboard 端口 `8766` 到 `8776`，不需要手工填写 token。

## 边界

- 数据是估算值，主要用于提供“正在持续输出”的直观反馈，不用于计费或性能基准。
- ChatGPT 网页 DOM 改版后，`[data-message-author-role="assistant"]` 选择器可能需要同步调整。
- 扩展只向 `127.0.0.1/localhost` 发送遥测，不会把内容正文发给 Bridge，只发送估算增量 token、耗时、source/model 标签。
