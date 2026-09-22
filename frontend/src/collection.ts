import { useCallback, useEffect, useRef, useState } from "react";
import { postApi, request, useTokenVersion } from "./api";

export const RUN_LABELS: Record<string, string> = { queued: "已排队", running: "正在运行", succeeded: "成功", partial: "部分失败", failed: "失败", interrupted: "已中断" };
export const COLLECTION_MODULES = ["job-hunter", "github-trending", "ai-daily-news"];
export interface CollectionRun {
  id: string; module_id: string; status: string; reused?: boolean;
  inserted?: number; updated?: number; error?: string | null;
}

export function useCollectionRun(onComplete: () => void) {
  const identity = useTokenVersion();
  const [run, setRun] = useState<CollectionRun | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [retry, setRetry] = useState(0);
  const postController = useRef<AbortController | null>(null);
  const runIdentity = useRef(identity);
  const completed = useRef<string | null>(null);
  const completion = useRef(onComplete);
  const runId = run?.id;

  useEffect(() => { completion.current = onComplete; }, [onComplete]);

  useEffect(() => {
    setRun(null); setError(""); setSubmitting(false);
    return () => { postController.current?.abort(); postController.current = null; };
  }, [identity]);

  useEffect(() => {
    if (!runId || runIdentity.current !== identity || completed.current === runId) return;
    let disposed = false;
    let finished = false;
    let stopped = false;
    let attempts = 0;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    async function poll() {
      if (disposed || finished || stopped || document.hidden) return;
      if (++attempts > 120) { stopped = true; setError("自动查询已暂停，任务仍可能运行；请手动查询结果。"); return; }
      const current = new AbortController();
      controller = current;
      const timeout = window.setTimeout(() => current.abort(), 20000);
      try {
        const result = await request<CollectionRun>(`/api/platform/collection-runs/${encodeURIComponent(runId!)}`, { signal: current.signal });
        if (disposed || current.signal.aborted) return;
        if (!RUN_LABELS[result.status]) throw new Error("收到未知运行状态，已停止查询，请刷新确认。");
        setRun((previous) => ({ ...result, reused: previous?.reused }));
        setError("");
        if (result.status !== "queued" && result.status !== "running") { finished = true; completed.current = runId!; completion.current(); }
        else timer = window.setTimeout(poll, 1500);
      } catch (cause) {
        if (!disposed && !document.hidden && controller === current) { stopped = true; setError(current.signal.aborted ? "查询超时，运行结果尚未确认。" : cause instanceof Error ? cause.message : String(cause)); }
      } finally { window.clearTimeout(timeout); }
    }
    function visibility() {
      window.clearTimeout(timer); controller?.abort();
      if (!document.hidden) void poll();
    }
    document.addEventListener("visibilitychange", visibility);
    void poll();
    return () => { disposed = true; window.clearTimeout(timer); controller?.abort(); document.removeEventListener("visibilitychange", visibility); };
  }, [runId, identity, retry]);

  const start = useCallback(async (moduleId: string) => {
    if (!COLLECTION_MODULES.includes(moduleId) || postController.current) return;
    const controller = new AbortController();
    postController.current = controller;
    runIdentity.current = identity;
    completed.current = null;
    setSubmitting(true); setRun(null); setError("");
    try {
      const result = await postApi<{ run_id: string; status: string; reused: boolean }>("/api/platform/collection-runs", { module_id: moduleId }, controller.signal);
      if (!controller.signal.aborted) {
        if (!result.run_id || !RUN_LABELS[result.status]) throw new Error("未收到有效运行编号，请刷新运行记录确认，勿重复触发。");
        setRun({ id: result.run_id, status: result.status, module_id: moduleId, reused: result.reused });
      }
    } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (!controller.signal.aborted) setSubmitting(false); if (postController.current === controller) postController.current = null; }
  }, [identity]);
  return { run, error, submitting, busy: submitting || !!run && ["queued", "running"].includes(run.status), start, retry: () => { setError(""); setRetry((value) => value + 1); } };
}
