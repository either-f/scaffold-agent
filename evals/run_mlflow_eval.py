"""M12 评测侧：把 run_eval.py 的离线任务集喂给真实 MLflow（tracking API，本地
file:// store），验证内核 eval 结果能否流进第三方评测/追踪工具而不用改内核或
重新实现一遍任务跑分逻辑——直接复用 `run_eval.py` 的 `load_tasks`/`offline_results`。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evals.run_eval import load_tasks, offline_results  # noqa: E402


def main() -> int:
    import argparse
    import json

    import mlflow

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tracking-uri",
        default=f"sqlite:///{(PROJECT_ROOT / 'runs' / 'mlflow.db').as_posix()}",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment("agent-kernel-offline-eval")

    tasks = load_tasks()
    results = offline_results(tasks)
    passed = sum(r["passed"] for r in results)
    avg_steps = round(sum(r["steps"] for r in results) / len(results), 2)

    with mlflow.start_run(run_name=f"offline-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}") as run:
        mlflow.log_param("mode", "offline")
        mlflow.log_param("model", "fake-scripted")
        mlflow.log_param("n_tasks", len(tasks))
        mlflow.log_metric("passed", passed)
        mlflow.log_metric("pass_rate", passed / len(results))
        mlflow.log_metric("average_steps", avg_steps)
        for r in results:
            mlflow.log_metric(f"task_passed/{r['id']}", int(r["passed"]))
        mlflow.log_table(
            data={
                "id": [r["id"] for r in results],
                "passed": [r["passed"] for r in results],
                "steps": [r["steps"] for r in results],
                "reasons": [json.dumps(r["reasons"], ensure_ascii=False) for r in results],
            },
            artifact_file="task_results.json",
        )
        run_id = run.info.run_id

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "mlflow_eval_real",
        "tracking_uri": args.tracking_uri,
        "run_id": run_id,
        "passed": passed,
        "total": len(results),
        "average_steps": avg_steps,
        "ok": passed == len(results),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"查看：uv run mlflow ui --backend-store-uri {args.tracking_uri}")
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
