# ADR-0014: 评测侧接 MLflow tracking，不重新实现任务跑分逻辑

日期: 2026-08-11  状态: 已采纳

## 决策

- 新增 `evals/run_mlflow_eval.py`，直接 `from evals.run_eval import load_tasks,
  offline_results` 复用 M2 就有的任务跑分逻辑，只在外面套一层真实 `mlflow`
  tracking API（`start_run`/`log_param`/`log_metric`/`log_table`），把每个任务的
  pass/fail、步数、失败原因喂进 MLflow 的 run/metric/artifact 体系。不重新实现一遍
  "怎么跑一个 eval 任务"。
- Tracking backend 选 `sqlite:///runs/mlflow.db`，不是更直觉的 `file:./mlruns`——
  MLflow 3.x 的文件后端已进入 maintenance mode，直接用会抛
  `MlflowException`（真跑出来的真实报错，不是查文档预判的），官方建议迁移到数据库
  后端；本仓库已经在多处用 sqlite 做本地持久化（checkpoint、effect ledger），风格一致。
- promptfoo（PLAN 里"二选一"的另一个候选）没选：它是 Node.js CLI + YAML 配置驱动，
  要么让它 shell 出去调用一个 Python provider 脚本，要么整个任务集重新用 YAML 描述
  一遍——两条路都是"为了用 promptfoo 而 promptfoo"，不如 MLflow 的 Python 原生
  tracking API 跟本仓库现有 Python eval 脚本一行代码接起来。

## 原因

M12 的验收目标是"验证内核 eval 结果能否流进外部评测/追踪工具而不用改内核"，
不是"造一个新的任务集"。`run_eval.py` 的 `load_tasks`/`offline_results` 已经是
成熟、CI 里天天跑的逻辑，最省事也最诚实的验证方式就是原样复用它，只换输出目的地。

## 后果与迁移条件

- `runs/mlflow.db` 走 `.gitignore`（跟 `runs/` 下其它运行期产物一致），CI 离线门禁
  不跑这个脚本（避免给 CI 引入 mlflow 这个大依赖），真要接入 CI 时应作为独立可选步骤。
- `mlflow` extra 只在 `pyproject.toml` 声明，不是内核依赖；`evals/run_mlflow_eval.py`
  跟其它真实第三方验证脚本（`run_graph.py`/`run_worker_real.py`）同一职责边界：
  手动/CD 环境跑，不进离线门禁。
- 验收：真实跑通，30/30 离线任务全部记录进 MLflow sqlite 库，见
  [evals/baseline-m12-mlflow-eval-real.json](evals/baseline-m12-mlflow-eval-real.json)；
  `uv run mlflow ui --backend-store-uri sqlite:///runs/mlflow.db` 可视化查看。
- 内核零改动：只新增一个 eval 脚本 + 一个 extra，`run_eval.py` 本身未改一行。
