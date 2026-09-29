# ArcBench agent

This fork adds an ArcBench runner to mini-SWE-agent. It reads the GitHub platform or spreadsheet product requirements, runs atomic requirements in dependency order, and writes a trajectory and metrics for each task.

## Install

```powershell
python -m pip install -e .
```

Keep your model API key outside this repository. The default model is `deepseek/deepseek-chat`.

## Requirements

The ArcBench product specifications are separate from this repository. Place `arcbench-hackathon-requirements` next to this checkout, or pass its location with `--requirements-root`.

```powershell
arcbench-agent list --target github
arcbench-agent list --target sheet
arcbench-agent prepare --target github --id REQ-1-1-1
```

## Run

Point `--workspace` at the Git repository containing the application to build. The agent executes model-generated shell commands there, so use an isolated environment for untrusted runs.

```powershell
arcbench-agent probe --api-key-file C:\path\to\deepseek.key
arcbench-agent run --target github --workspace C:\path\to\app --id REQ-1-1-1 --variant requirements --api-key-file C:\path\to\deepseek.key
```

Use `--variant baseline` for the upstream prompt and `--variant requirements` for the ArcBench prompt. `--all` runs every atomic requirement. The runner includes prerequisite requirements by default; `--no-include-dependencies` limits the run to selected IDs. Use `--include-scenarios` to append scenario text to the authoritative atomic description.

Runs write `manifest.json`, `results.jsonl`, and per-task trajectories under `arcbench-runs/`. To summarize efficiency metrics, run:

```powershell
arcbench-agent report arcbench-runs\<run-directory>
```

Pass rate requires official or local evaluator outcomes in a JSON object mapping requirement IDs to booleans, passed with `--outcomes`. Agent submission alone does not establish a passing result.

This project retains the upstream [MIT license](LICENSE.md) and history. The ArcBench runner is not an official competition submission adapter.
