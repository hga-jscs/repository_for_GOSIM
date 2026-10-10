# GOSIM 复赛最终提交

本目录完整保留最终提交包 `656be2857792.zip` 中的 116 个文件，源码内容未改动。入口是 `main.py`，用于根据 GitHub / Sheet 的任务需求执行应用开发和复赛增量修改。

## 文件说明

| 文件或目录 | 用途 |
| --- | --- |
| `main.py`、`agent.py` 及其他根目录 Python 文件 | 启动入口、模型调用、工具、需求调度与验证 |
| `evolution.py`、`evolution_github.md`、`evolution_sheet.md` | 复赛增量任务流程与实现指导 |
| `arcbench-agent-runtime/` | 随包提供的比赛运行依赖 |
| `template/`、`template-files.json` | 应用初始模板及其文件校验清单 |
| `evaluation/` | 根据公开需求编写的本地检查，供验证及反馈修复使用 |
| `requirements.txt` | Python 依赖 |
| `LICENSE.md`、`SOURCES.md` | 原有许可证、代码来源及使用范围 |

`evaluation/` 属于智能体运行链的一部分，其中的检查不是官方隐藏测试。

## 安装

需要 Python 3.10 或以上、Git、Node.js 22.12 或以上的 22.x 版本及 npm。在本目录中执行：

```bash
python -m venv .venv
```

激活环境：Windows PowerShell 使用 `.\.venv\Scripts\Activate.ps1`；Linux / macOS 使用 `source .venv/bin/activate`。

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python main.py --help
```

本目录使用自己的 `requirements.txt`，无需安装仓库根目录的旧版 Agent。

## 运行

任务需求和复赛应用基线由比赛另行提供，不包含在提交包中：

- 输入为 `requirements.yaml` 或其所在目录；若任务提供 `prerequisites.md` 和参考资源，应保留其目录结构。
- 复赛增量任务的输出目录必须是已有的基线应用，包含 `frontend/package.json` 和 `backend/package.json`。`template/` 是初始模板，不能代替复赛基线。
- 应用目录应位于本仓库之外，与需求目录、密钥文件分开。

以下命令中的路径需要替换为实际路径。先预览需求分批；该命令不调用模型，也不修改应用：

```bash
python main.py /path/to/task/requirements.yaml --output-dir /path/to/application --plan-only
```

实际运行时，将 API 密钥放在仓库和应用目录之外的文件中，再执行：

```bash
python main.py /path/to/task/requirements.yaml --output-dir /path/to/application --api-key-file /path/to/api-key.txt
```

也可通过 `OPENAI_API_KEY` 或 `ARC_BENCH_API_KEY` 提供密钥。默认使用代码中的比赛网关及 Flash 模型；如需指定接口，使用 `--base-url`、`OPENAI_BASE_URL` 或 `ARC_BENCH_API_BASE_URL`。模型可通过 `--model` 指定，复赛任务仅接受代码允许的 Flash 模型标识。实际执行需要有效的接口权限，并会调用模型、修改应用及运行检查。

## 提交一致性

原始 ZIP 的 SHA-256：

```text
dfc5f34f178815c342908207a66a2e530c3b77943c21aaf52d4c5d5481efdf16
```

[提交校验.json](提交校验.json) 记录原包全部 116 个文件的相对路径和 SHA-256。本目录新增的 `README.md`、`.gitignore`、`.gitattributes` 和 `提交校验.json` 为仓库整理文件，不属于原始提交。`.gitattributes` 禁用本目录的自动换行转换，以保留源码字节。

安装、入口和文件一致性检查仅验证开源包可加载及来源一致，不代表重新完成比赛端评分或完整应用验收。许可证和第三方来源说明以 [LICENSE.md](LICENSE.md) 与 [SOURCES.md](SOURCES.md) 为准。
