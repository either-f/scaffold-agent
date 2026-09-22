import { useState } from "react";
import { AuthRequiredError, postApi, putApi, request, useApi } from "../api";
import { useAuth } from "../auth";
import { STAGES } from "../demo";
import type { EntityRef, OpportunityStage } from "../types";
import { ENTITY_PATHS, entityBody, entityTitle, personalFlags, remoteDate, sourceLink, type ApplicationRecord, type PersonalFlags, type RemoteEntity } from "../workspace";
import { DemoDialog } from "./DemoDialog";

export function WorkspaceJobForm({ item, onClose, onSaved }: { item?: RemoteEntity; onClose: () => void; onSaved: (entity: RemoteEntity) => void }) {
  const { openAuth } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return <DemoDialog caption="真实工作台 · 手动岗位" title={item ? "编辑手动岗位" : "添加手动岗位"} onClose={() => { if (!busy) onClose(); }}>
    <p>保存到当前账户；填写完成并收到服务器确认后才会创建或修改。添加后可单独加入机会。</p>
    <form onSubmit={async (event) => {
      event.preventDefault();
      if (busy) return;
      const fields = new FormData(event.currentTarget);
      const title = String(fields.get("title") ?? "").trim();
      const url = String(fields.get("source_url") ?? "").trim();
      if (!title || (url && !sourceLink(url))) { setError("岗位名称不能为空；来源需为不含账号密码的 http/https 链接，或留空。"); return; }
      const body = { title, company: String(fields.get("company") ?? "").trim(), city: String(fields.get("city") ?? "").trim(), description: String(fields.get("description") ?? "").trim(), source_url: url || null };
      setBusy(true); setError("");
      try {
        const saved = item ? await request<RemoteEntity>(`/api/platform/jobs/${item.id}`, { method: "PATCH", body: JSON.stringify(body) }) : await postApi<RemoteEntity>("/api/platform/jobs", body);
        onSaved(saved);
      } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); if (cause instanceof AuthRequiredError) openAuth(); }
      finally { setBusy(false); }
    }}>
      <fieldset disabled={busy} className="workspace-fields">
        <label className="demo-field">岗位名称<input name="title" required maxLength={200} defaultValue={item?.title ?? ""} /></label>
        <label className="demo-field">公司（可选）<input name="company" maxLength={200} defaultValue={item?.company ?? ""} /></label>
        <label className="demo-field">城市（可选）<input name="city" maxLength={100} defaultValue={item?.city ?? ""} /></label>
        <label className="demo-field">岗位描述<textarea name="description" rows={5} maxLength={5000} defaultValue={item ? item.description || item.reason || "" : ""} /></label>
        <label className="demo-field">来源链接（可选）<input name="source_url" type="url" maxLength={2000} defaultValue={item?.source_url ?? ""} /></label>
        {error && <p role="alert" className="demo-error">{error} · 内容已保留，可重试保存。</p>}
        <div className="demo-detail-actions"><button type="button" onClick={onClose}>取消</button><button type="submit" className="demo-primary">{busy ? "保存中…" : error ? "重试保存岗位" : "保存岗位"}</button></div>
      </fieldset>
    </form>
  </DemoDialog>;
}

function ApplicationForm({ jobId, record, onSaved }: { jobId: number; record?: ApplicationRecord; onSaved: (record: ApplicationRecord) => void }) {
  const { openAuth } = useAuth();
  const [stage, setStage] = useState<OpportunityStage>(record?.stage ?? "saved");
  const [note, setNote] = useState(record?.note ?? "");
  const [nextStep, setNextStep] = useState(record?.next_step ?? "");
  const [done, setDone] = useState(record?.next_step_done ?? false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  function reset() { setStage(record?.stage ?? "saved"); setNote(record?.note ?? ""); setNextStep(record?.next_step ?? ""); setDone(record?.next_step_done ?? false); setError(""); setSaved(false); }
  return <form onSubmit={async (event) => {
    event.preventDefault(); if (busy) return; setBusy(true); setError(""); setSaved(false);
    try { const result = await putApi<ApplicationRecord>(`/api/platform/jobs/${jobId}/application`, { stage, note: note.trim(), next_step: nextStep.trim(), next_step_done: !!nextStep.trim() && done }); onSaved(result); setSaved(true); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); if (cause instanceof AuthRequiredError) openAuth(); }
    finally { setBusy(false); }
  }}>
    <h3>{record ? "我的机会" : "加入我的机会"}</h3><p>阶段表示你记录的申请进度，不会向外部网站提交简历。</p>
    <fieldset disabled={busy} className="workspace-fields">
      <label className="demo-field">申请阶段<select value={stage} onChange={(event) => { setStage(event.target.value as OpportunityStage); setSaved(false); }}>{Object.entries(STAGES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="demo-field">我的笔记<textarea maxLength={5000} rows={4} value={note} onChange={(event) => { setNote(event.target.value); setSaved(false); }} /></label>
      <label className="demo-field">下一步<input maxLength={500} value={nextStep} onChange={(event) => { setNextStep(event.target.value); setDone(false); setSaved(false); }} /></label>
      <label className="demo-check"><input type="checkbox" checked={done} disabled={!nextStep.trim()} onChange={(event) => { setDone(event.target.checked); setSaved(false); }} />下一步已完成</label>
      {record?.next_at && <p>已记录时间：{remoteDate(record.next_at)}</p>}
      {error && <p role="alert" className="demo-error">{error} · 表单保留，请重试。</p>}{saved && <p role="status">阶段与下一步已保存到当前账户。</p>}
      <div className="demo-detail-actions"><button type="button" onClick={reset}>取消修改</button><button type="submit" className="demo-primary">{busy ? "保存中…" : error ? "重试保存行动" : "保存阶段与下一步"}</button></div>
    </fieldset>
  </form>;
}

export function WorkspaceDetail({ target, onClose, onChanged, onEdit }: { target: EntityRef; onClose: () => void; onChanged: () => void; onEdit: (item: RemoteEntity) => void }) {
  const { user, openAuth } = useAuth();
  const detail = useApi<RemoteEntity>(`/api/platform/${ENTITY_PATHS[target.kind]}/${target.id}`);
  const applications = useApi<{ items: ApplicationRecord[] }>(target.kind === "job" && user ? "/api/platform/applications" : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const item = detail.data;
  const flags = item ? personalFlags(item) : null;
  const record = applications.data?.items.find((entry) => entry.job_id === target.id);
  async function update(patch: Partial<PersonalFlags>) {
    if (!user) { openAuth(); return; }
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { const result = await putApi<{ item: RemoteEntity }>(`/api/platform/my/items/${target.kind}/${target.id}/actions`, patch); detail.setData(() => result.item); onChanged(); setNotice("已保存到当前账户。"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); if (cause instanceof AuthRequiredError) openAuth(); }
    finally { setBusy(false); }
  }
  return <DemoDialog caption="真实工作台 · 服务器内容" title={item ? entityTitle(item) : "读取详情"} onClose={onClose}>
    {!item ? <div className="demo-empty" role={detail.error ? "alert" : "status"}><strong>{detail.error || "加载中…"}</strong>{detail.error && <button type="button" onClick={detail.refresh}>重试读取详情</button>}</div> : <>
      <p>{item.company || item.full_name || item.source || item.meta || "来源信息未提供"}</p><p>发布时间：{remoteDate(item.published_at)} · 抓取时间：{remoteDate(item.fetched_at)}</p>
      <p>来源类别：{item.origin === "manual" ? "手动记录" : item.origin === "demo" ? "后端演示数据" : item.origin === "real" ? "采集内容" : "未知"} · 评估：暂无可核验评分</p>
      <p className="workspace-body">{entityBody(item)}</p><div className="demo-tags">{item.tags?.map((tag) => <span key={tag}>{tag}</span>)}</div>
      {sourceLink(item.source_url) ? <p><a href={sourceLink(item.source_url)!} target="_blank" rel="noopener noreferrer">打开原始来源 ↗</a></p> : <p>来源链接未提供。</p>}
      <div className="demo-detail-actions">
        <button type="button" disabled={busy} aria-pressed={flags!.favorite} onClick={() => update({ favorite: !flags!.favorite })}>{flags!.favorite ? "取消收藏" : "收藏"}</button>
        <button type="button" disabled={busy} aria-pressed={flags!.watch} onClick={() => update({ watch: !flags!.watch })}>{flags!.watch ? "取消关注" : "关注变化"}</button>
        <button type="button" disabled={busy} aria-pressed={flags!.read_later} onClick={() => update({ read_later: !flags!.read_later, ...(!flags!.read_later ? { read: false } : {}) })}>{flags!.read_later ? "取消稍后阅读" : "稍后阅读"}</button>
        <button type="button" disabled={busy} aria-pressed={flags!.read} onClick={() => update({ read: !flags!.read, ...(!flags!.read ? { read_later: false } : {}) })}>{flags!.read ? "标为未读" : "完成阅读"}</button>
      </div>
      <p role="status">{busy ? "保存中…" : notice || (flags!.read ? "已读" : "未读")}</p>{error && <p role="alert" className="demo-error">{error} · 当前状态未改动，可再次点击重试。</p>}
      {target.kind === "job" && (user ? applications.data ? <ApplicationForm jobId={target.id} record={record} onSaved={(result) => { applications.setData((current) => ({ items: [...current.items.filter((entry) => entry.job_id !== target.id), result] })); onChanged(); }} /> : <div role="status"><p>{applications.error || "正在读取机会状态…"}</p>{applications.error && <button type="button" onClick={applications.refresh}>重试读取机会</button>}</div> : <button type="button" className="demo-primary" onClick={openAuth}>登录后管理机会</button>)}
      {target.kind === "job" && item.origin === "manual" && user?.id === item.created_by && <div className="demo-detail-actions"><button type="button" onClick={() => onEdit(item)}>编辑手动岗位</button></div>}
    </>}
  </DemoDialog>;
}
