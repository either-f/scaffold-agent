import { useEffect, useId, useRef, type ReactNode } from "react";

export function DemoDialog({ title, onClose, children, caption = "当前会话 · 本地演示", className = "" }: { title: string; onClose: () => void; children: ReactNode; caption?: string; className?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current!;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    return () => {
      dialog.close();
      if (trigger?.isConnected) trigger.focus();
      else document.querySelector<HTMLElement>(".demo-topbar a")?.focus();
    };
  }, []);
  return <dialog ref={ref} className={`demo-detail ${className}`} aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header className="demo-detail-head"><div><span className="demo-kind">{caption}</span><h2 id={titleId}>{title}</h2></div><button autoFocus className="demo-close" type="button" onClick={onClose} aria-label="关闭弹窗">×</button></header>
    <div className="demo-detail-body">{children}</div>
  </dialog>;
}
