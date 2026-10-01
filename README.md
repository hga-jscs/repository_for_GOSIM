# GOSIM 参赛 Agent

`agent-improvement` 是可提交分支，入口 `main.py`、依赖 `requirements.txt`、官方 SDK 和应用模板均在仓库根目录。原 mini-SWE-agent 实现保存在 [main 分支](https://github.com/hga-jscs/repository_for_GOSIM/tree/main)。

## 生成提交包

需要 Python 3.10+；运行 Agent 还需要 Git、Node.js 和 npm。

```bash
python -m pip install -r requirements.txt
python package.py ../参赛Agent.zip
```

上传生成的 `参赛Agent.zip`。打包器只收录运行所需文件，`main.py` 直接位于 ZIP 根目录；目标 ZIP 已存在时会拒绝覆盖。GitHub 的 Download ZIP 带有外层目录，请使用上述打包命令。

正式参赛选择 Python，勾选「使用比赛额度评测」。基础 URL 为 `https://api.arc-bench.com/v1`，模型为 `deepseek-v4-flash`，视觉模型保留 `deepseek-v4-flash-vision-exp`。勾选后平台隐藏个人 API 密钥输入框，运行时注入临时凭据。

逐格填写、个人 Key 调试与运行选项见 [提交说明](提交说明.md)。

## 官方启动方式

```bash
python3 main.py /path/to/requirements --output-dir /path/to/application --type web
```

平台注入的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`MODEL` 优先于默认配置。支持仅通过 `ARCBENCH_TASK_DIR`、`ARCBENCH_OUTPUT_DIR` 等官方环境变量启动。

输出目录直接包含 `frontend/` 和 `backend/`。Agent 按需求依赖分批实现，随后执行前端构建、HTTP、真实浏览器及适用的公开工作流检查，再修复发现的错误。模板、SDK 与来源说明见 [SOURCES.md](SOURCES.md)。

## 检查与结果

```bash
python -m pytest -q
```

测试覆盖需求依赖、路径和密钥保护、进程清理、异常恢复、官方入口与提交包。公开应用工作流需另行指定应用目录运行，命令见提交说明。

历史实验与限制见 [实验结果](实验结果.md)。公开用例通过不等于官方隐藏场景满分。密钥、运行日志、生成应用、缓存和依赖目录不进入提交包。
