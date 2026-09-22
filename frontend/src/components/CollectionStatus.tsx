import { RUN_LABELS, type CollectionRun } from "../collection";

export function CollectionStatus({ run, error, submitting, retry }: { run: CollectionRun | null; error: string; submitting: boolean; retry: () => void }) {
  if (!run && !error && !submitting) return null;
  return <div className="auth-hint workspace-run-status" role={error || run?.error ? "alert" : "status"}>
    {submitting && <p>正在提交采集请求…</p>}
    {run && <><strong>{run.module_id} · {RUN_LABELS[run.status] ?? "未知状态"}</strong><p>运行编号：{run.id}{run.reused ? " · 复用已有运行" : ""}</p>{!["queued", "running"].includes(run.status) && <p>新增 {run.inserted ?? "未知"} / 更新 {run.updated ?? "未知"}{run.error ? ` · ${run.error}` : ""}</p>}<p>离开页面或隐藏标签会停止查询，不会取消后端任务。</p></>}
    {error && <p>{error} {run ? <button type="button" className="small-btn" onClick={retry}>重试查询运行结果</button> : "可重新操作；不表示采集成功。"}</p>}
  </div>;
}
