# 来源与使用范围

本目录中的 Agent 循环、工具、需求分批与启动验证为本次编写，遵循仓库根目录 MIT 许可证。
提交包中的 `evaluation/` 检查根据用户提供的公开需求自行编写，用于本地验证和反馈修复；它们不是官方隐藏测试。

`template/` 与 `arcbench-agent-runtime/` 来自用户于 2026-10-01 下载的官方
`agent-blank-based.zip`。`template-files.json` 记录每个文件的 SHA-256，打包只收录清单文件。
除前端 `package-lock.json` 根据原有 package.json 重新解析、并在 Windows npm 11 与 Linux npm 10 验证外，其余文件保持原样。旧锁文件在 Linux npm 10 安装时触发 `edgesOut` 错误。
它们是官方参赛模板及运行依赖，保留原有文件和版权声明；本目录的 MIT 声明不改变这些文件原有条款。

参考设计：

- [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent)：简洁的模型、工具、执行循环。
- [Aider repository map](https://aider.chat/docs/repomap.html)：先提供紧凑文件索引，再按需读取源码。
- [Deep Agents](https://github.com/langchain-ai/deepagents)：阶段计划、文件持久化、控制上下文增长。
- [ARC](https://github.com/code-philia/agentic-requirement-compiler)：需求树、依赖顺序和阶段进度。
- 官方 Codex / Claude Code 模板：按 ROOT 子模块实施，共用工程；本实现进一步限制批次长度。
- 官方 Octos 模板：对齐 frontend/backend 目录、构建及启动预演，使用独立测试端口。
- [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/)：保留工具调用中的 reasoning_content。

没有复制 Aider、Deep Agents、Codex、Claude Code 或 Octos 的实现代码，也不依赖这些 Agent 的账户。
