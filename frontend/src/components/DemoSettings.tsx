import { useState } from "react";
import { CONNECTION_LABELS, OUTCOMES, TEMPLATES, type DemoConnection, type DemoOutcome, type DemoRule, type DemoRun } from "../demo";
import { DemoDialog } from "./DemoDialog";

function RuleForm({ rule, onSave, onClose }: { rule: DemoRule; onSave: (rule: DemoRule) => void; onClose: () => void }) {
  const [draft, setDraft] = useState(rule);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState("");
  return <DemoDialog title={rule.id ? "配置演示规则" : "新建演示规则"} onClose={onClose}>
    <p>仅记录在当前会话。时间是演示参数，不会自动调度、采集或发送消息。</p>
    <form onSubmit={(event) => {
      event.preventDefault();
      if (!draft.name.trim() || !draft.scope.trim() || !draft.time || !TEMPLATES.some((item) => item.id === draft.module)) { setError("请填写名称、关注范围、时间并选择可用模板。"); return; }
      if (!confirm) { setConfirm(true); return; }
      onSave({ ...draft, id: draft.id || crypto.randomUUID(), name: draft.name.trim(), scope: draft.scope.trim() });
    }}>
      {confirm ? <div className="demo-confirm"><h3>确认演示配置</h3><p>{draft.name}</p><p>模板：{TEMPLATES.find((item) => item.id === draft.module)?.title}</p><p>关注：{draft.scope}</p><p>每天 {draft.time} · {draft.enabled ? "已启用" : "已停用"}（模拟）</p></div> : <>
        <label className="demo-field">模板<select value={draft.module} onChange={(event) => setDraft({ ...draft, module: event.target.value })}>{TEMPLATES.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}<option value="" disabled>未知模块 · 不可用</option></select></label>
        <label className="demo-field">规则名称<input required maxLength={80} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
        <label className="demo-field">关注范围<input required maxLength={200} value={draft.scope} onChange={(event) => setDraft({ ...draft, scope: event.target.value })} /></label>
        <label className="demo-field">每天的演示时间<input type="time" required value={draft.time} onChange={(event) => setDraft({ ...draft, time: event.target.value })} /></label>
        <label className="demo-check"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />启用规则（不代表正在运行）</label>
      </>}
      {error && <p role="alert" className="demo-error">{error}</p>}
      <div className="demo-detail-actions"><button type="button" onClick={onClose}>取消</button>{confirm && <button type="button" onClick={() => setConfirm(false)}>返回修改</button>}<button className="demo-primary" type="submit">{confirm ? "保存演示规则" : "查看配置摘要"}</button></div>
    </form>
  </DemoDialog>;
}

export function DemoAutomation({ rules, runs, query, onSave, onToggle, onRun }: {
  rules: DemoRule[]; runs: DemoRun[]; query: string;
  onSave: (rule: DemoRule) => void; onToggle: (id: string) => void; onRun: (rule: DemoRule, outcome: DemoOutcome) => void;
}) {
  const [editing, setEditing] = useState<DemoRule | null>(null);
  const [outcome, setOutcome] = useState<DemoOutcome>("success");
  const [runId, setRunId] = useState<string | null>(null);
  const selectedRun = runs.find((item) => item.id === runId);
  const filtered = rules.filter((rule) => `${rule.name} ${rule.scope}`.toLowerCase().includes(query.trim().toLowerCase()));
  return <>
    <div className="demo-page-head compact"><div><p className="demo-eyebrow">自动化</p><h1>用模板减少重复动作</h1><p>配置、启停和运行记录均为本地模拟。不会执行真实任务。</p></div></div>
    <div className="demo-template-grid">{TEMPLATES.map((template) => <article className="demo-setting-card" key={template.id}><h3>{template.title}</h3><p>模板 · {template.scope}</p><button type="button" onClick={() => setEditing({ id: "", module: template.id, name: template.title, scope: template.scope, time: "09:00", enabled: true })}>使用模板</button></article>)}</div>
    <div className="demo-toolbar"><h2>我的演示规则（{filtered.length}）</h2><label className="demo-field">下次手动运行的模拟结果<select value={outcome} onChange={(event) => setOutcome(event.target.value as DemoOutcome)}>{Object.entries(OUTCOMES).map(([id, label]) => <option key={id} value={id}>{label}（模拟）</option>)}</select></label></div>
    <div className="demo-list">{filtered.map((rule) => {
      const running = runs.some((run) => run.ruleId === rule.id && run.status === "running");
      const known = TEMPLATES.some((template) => template.id === rule.module);
      return <article className="demo-setting-card" key={rule.id}><h3>{rule.name}</h3><p>{rule.scope} · 每天 {rule.time}（模拟时间）</p><p>规则：{rule.enabled ? "启用" : "停用"} · 执行：{running ? "正在模拟运行" : "空闲"}</p>{!known && <p className="demo-error">未知模块，不可配置或执行。</p>}<div className="demo-detail-actions"><button type="button" role="switch" aria-checked={rule.enabled} aria-label={`${rule.name}启用状态`} disabled={!known} onClick={() => onToggle(rule.id)}>{rule.enabled ? "停用" : "启用"}</button><button type="button" disabled={running || !known} onClick={() => setEditing(rule)}>配置</button><button type="button" disabled={running || !rule.enabled || !known} onClick={() => onRun(rule, outcome)}>{running ? "正在模拟运行…" : "模拟运行一次"}</button></div></article>;
    })}</div>
    {!filtered.length && <div className="demo-empty"><strong>{query ? "没有匹配规则" : "尚未配置规则"}</strong><span>{query ? "清空顶部搜索后重试。" : "选择上方模板，填写参数并确认保存。"}</span></div>}
    <section className="demo-section demo-run-section"><h2>运行记录 · 全部为模拟</h2>{runs.length ? <div className="demo-list">{runs.map((run) => <button className="demo-run" type="button" key={run.id} onClick={() => setRunId(run.id)}><strong>{run.name}</strong><span>{run.time} · {run.status === "running" ? "正在运行" : OUTCOMES[run.status]}（模拟）</span><span>查看结果 →</span></button>)}</div> : <p>尚无运行记录；保存配置和启用规则不会生成运行成功记录。</p>}</section>
    {editing && <RuleForm rule={editing} onClose={() => setEditing(null)} onSave={(rule) => { onSave(rule); setEditing(null); }} />}
    {selectedRun && <DemoDialog title="模拟运行详情" onClose={() => setRunId(null)}><h3>{selectedRun.name}</h3><p>运行时关注范围：{selectedRun.scope}</p><p>开始时间：{selectedRun.time}</p><div role="status"><strong>{selectedRun.status === "running" ? "正在模拟运行…" : `${OUTCOMES[selectedRun.status]}（模拟）`}</strong><p>{selectedRun.status === "running" ? "本地计时完成后显示所选样例结果。" : selectedRun.status === "success" ? "模拟处理 3 条，成功 3 条；未新增真实内容。" : selectedRun.status === "partial" ? "模拟处理 3 条，成功 2 条，失败 1 条：来源超时。可重新模拟运行。" : "模拟处理 3 条，成功 0 条：来源不可用。调整模拟结果后可重试。"}</p></div><p>未发出任何采集、认证或通知请求。停用规则不取消已开始的单次模拟运行。</p></DemoDialog>}
  </>;
}

function ConnectionForm({ connection, onSave, onClose }: { connection: DemoConnection; onSave: (connection: DemoConnection) => void; onClose: () => void }) {
  const [draft, setDraft] = useState(connection);
  const [error, setError] = useState("");
  return <DemoDialog title={connection.id ? "配置演示连接" : "添加演示来源"} onClose={onClose}><p>只填写演示名称和公开范围，无需 Cookie、密钥或账号。保存后状态为未知，需单独模拟验证。</p><form onSubmit={(event) => {
    event.preventDefault();
    if (!draft.name.trim() || !draft.scope.trim()) { setError("名称与关注范围不能为空或只有空格。"); return; }
    onSave({ ...draft, id: draft.id || crypto.randomUUID(), name: draft.name.trim(), scope: draft.scope.trim(), status: "unknown", lastSuccess: null, error: "" });
  }}>
    <label className="demo-field">来源名称<input required maxLength={80} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
    <label className="demo-field">来源类别<select value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })}>{["公开项目", "公开招聘", "公开资讯", "手动记录"].map((category) => <option key={category}>{category}</option>)}</select></label>
    <label className="demo-field">公开范围（演示值）<input required maxLength={200} value={draft.scope} placeholder="例如 jobs / TypeScript" onChange={(event) => setDraft({ ...draft, scope: event.target.value })} /></label>
    {error && <p role="alert" className="demo-error">{error}</p>}<div className="demo-detail-actions"><button type="button" onClick={onClose}>取消</button><button type="submit" className="demo-primary">保存演示配置</button></div>
  </form></DemoDialog>;
}

export function DemoConnections({ connections, query, validating, onSave, onValidate }: {
  connections: DemoConnection[]; query: string; validating: string | null;
  onSave: (connection: DemoConnection) => void; onValidate: (connection: DemoConnection, outcome: "success" | "failed") => void;
}) {
  const [editing, setEditing] = useState<DemoConnection | null>(null);
  const [outcome, setOutcome] = useState<"success" | "failed">("success");
  const filtered = connections.filter((item) => `${item.name} ${item.category} ${item.scope}`.toLowerCase().includes(query.trim().toLowerCase()));
  return <>
    <div className="demo-page-head compact"><div><p className="demo-eyebrow">连接设置</p><h1>知道信息从哪里来</h1><p>连接验证与采集分开记录；这里的来源状态全部为模拟。</p></div><button className="demo-primary" type="button" onClick={() => setEditing({ id: "", name: "", category: "公开资讯", scope: "", status: "unconfigured", lastSuccess: null, error: "" })}>添加演示来源</button></div>
    <div className="demo-toolbar"><span>当前会话共 {connections.length} 个来源</span><label className="demo-field">下次验证的模拟结果<select value={outcome} onChange={(event) => setOutcome(event.target.value as "success" | "failed")}><option value="success">成功（模拟）</option><option value="failed">失败（模拟）</option></select></label></div>
    <div className="demo-template-grid">{filtered.map((connection) => <article className="demo-setting-card" key={connection.id}><h3>{connection.name}</h3><p>{connection.category} · {connection.scope || "尚未填写公开范围"}</p><span className={`demo-status demo-status-${connection.status}`}>{validating === connection.id ? "正在模拟验证…" : CONNECTION_LABELS[connection.status]}</span><p>最近连接成功：{connection.lastSuccess || "无记录"}</p><p>最近采集成功：未知 · 本演示未采集</p>{connection.error && <p className="demo-error">{connection.error}</p>}<div className="demo-detail-actions"><button type="button" disabled={validating === connection.id} onClick={() => setEditing(connection)}>配置</button><button type="button" disabled={!connection.scope || validating !== null} onClick={() => onValidate(connection, outcome)}>{connection.status === "failed" ? "模拟重试" : "模拟验证连接"}</button></div>{!connection.scope && <p>请先配置公开范围。</p>}</article>)}</div>
    {!filtered.length && <div className="demo-empty"><strong>没有匹配来源</strong><span>清空顶部搜索，或添加一个演示来源。</span></div>}
    {editing && <ConnectionForm connection={editing} onClose={() => setEditing(null)} onSave={(connection) => { onSave(connection); setEditing(null); }} />}
  </>;
}
