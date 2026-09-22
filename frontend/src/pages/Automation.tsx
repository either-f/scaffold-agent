import { useState } from "react";
import { postApi, useApi } from "../api";
import { COLLECTION_MODULES, RUN_LABELS, useCollectionRun } from "../collection";
import { CollectionStatus } from "../components/CollectionStatus";
import type { PreviewRequest } from "../components/DesignPreview";
import { matchesQuery, PageHead, PageStatus, SideBox, Subnav, SummaryCard } from "../components/ui";
import { formatDateTime } from "../demo";
import type { AutomationRule, AutomationsData } from "../types";

const MODULE_TAB: Record<string, string> = { "job-hunter": "招聘", "github-trending": "项目", "ai-daily-news": "AI日报" };

export function Automation({ query: searchQuery }: { query: string; onNavigate: (id: string) => void; onPreview: (request: PreviewRequest) => void }) {
  const [subtab, setSubtab] = useState("全部");
  const [status, setStatus] = useState<"all" | "running" | "paused">("all");
  const [notice, setNotice] = useState<string | null>(null);
  const [toggling, setToggling] = useState<number | null>(null);
  const { data, error, setData, refresh } = useApi<AutomationsData>("/api/platform/automations");
  const collection = useCollectionRun(refresh);

  const rules = data?.rules.filter((rule) => {
    const tabMatch = subtab === "全部" || subtab === (MODULE_TAB[rule.module_id ?? ""] ?? "自定义");
    const statusMatch = status === "all" || (status === "running" ? rule.enabled : !rule.enabled);
    return tabMatch && statusMatch && matchesQuery(searchQuery, rule.title, rule.meta, rule.category, rule.flow);
  }) ?? [];

  async function toggleRule(rule: AutomationRule) {
    if (toggling !== null) return;
    setToggling(rule.id);
    setNotice(null);
    try {
      const res = await postApi<{ enabled: boolean }>(`/api/platform/automations/${rule.id}/toggle`);
      setData((d) => ({ ...d, rules: d.rules.map((r) => r.id === rule.id ? { ...r, enabled: res.enabled } : r) }));
      setNotice(`「${rule.title}」已${res.enabled ? "启用" : "停用"}；正在执行的任务不受影响。`);
    } catch (e) {
      setNotice(`开关更新失败：${e instanceof Error ? e.message : String(e)}，可重试。`);
    } finally {
      setToggling(null);
    }
  }

  const head = (
    <PageHead
      title="自动化"
      desc="把采集、筛选、分析、通知和执行串成可控的自动化流程。"
      actions={<><button className="small-btn" onClick={refresh}>刷新记录</button><a className="small-btn primary" href="?demo=1#/automation">配置模板（进入演示）</a></>}
    />
  );

  if (data === null) {
    return <main className="page">{head}<PageStatus data={data} error={error} />{error && <button className="small-btn" onClick={refresh}>重试读取</button>}</main>;
  }

  return (
    <main className="page">
      {head}

      <section className="summary-row">
        <SummaryCard label="已启用规则" num={String(data.rules.filter((r) => r.enabled).length)} note={data.scheduler_enabled === true ? "调度器已启用" : data.scheduler_enabled === false ? "调度器未启用" : "调度状态未知"} mark="运" />
        <SummaryCard label="最近记录" num={String(data.stats?.total ?? "未知")} note="最多最近 20 条，非今日统计" mark="次" />
        <SummaryCard label="最近成功" num={String(data.stats?.succeeded ?? "未知")} note="最近记录中的成功数" mark="✓" />
        <SummaryCard label="最近失败" num={String(data.stats?.failed ?? "未知")} note="含部分失败与中断" mark="!" />
      </section>

      <Subnav
        tabs={["全部", "招聘", "项目", "AI日报", "自定义"]}
        active={subtab}
        onSelect={setSubtab}
        right={<select className="dropdown" value={status} onChange={(event) => setStatus(event.target.value as typeof status)}><option value="all">规则：全部</option><option value="running">已启用</option><option value="paused">已停用</option></select>}
      />

      {notice && (
        <div className="auth-hint" style={{ marginBottom: 14 }}>{notice}</div>
      )}
      <CollectionStatus {...collection} />
      <p className="scroll-note">启用规则不代表正在运行。模板编辑尚未接入真实保存，请使用独立演示入口。</p>

      <section className="auto-grid">
        <section className="rule-panel">
          {rules.length === 0 && <div className="scroll-note">没有符合筛选条件的自动化</div>}
          {rules.map((r, i) => (
            <article className="rule-card" key={r.id} style={i === rules.length - 1 ? { borderBottom: 0 } : undefined}>
              <div className="rule-head">
                <div>
                  <div className="rule-title">{r.title}</div>
                  <div className="rule-meta">{r.meta}</div>
                </div>
                <button type="button" disabled={toggling !== null || !r.module_id || !COLLECTION_MODULES.includes(r.module_id)} className={`toggle-sm${r.enabled ? " on" : ""}`} aria-label={`${r.title}${r.enabled ? "已启用" : "已暂停"}`} aria-pressed={r.enabled} onClick={() => toggleRule(r)} />
              </div>
              <div className="flow">
                {r.flow.map((node, j) => (
                  <span key={`${node}-${j}`}>
                    {j > 0 && <span className="flow-arrow" style={{ margin: "0 6px" }}>→</span>}
                    <span className="flow-node">{node}</span>
                  </span>
                ))}
              </div>
              <div className="rule-foot">
                <span className="rule-last">{r.last}</span>
                <div className="rule-actions">
                  <a className="btn" href="?demo=1#/automation">编辑体验（演示）</a>
                  <button className="btn" disabled={collection.busy || !r.module_id || !COLLECTION_MODULES.includes(r.module_id)} onClick={() => r.module_id && collection.start(r.module_id)}>
                    {collection.busy && collection.run?.module_id === r.module_id ? "执行中…" : "手动运行一次"}
                  </button>
                </div>
              </div>
              {(!r.module_id || !COLLECTION_MODULES.includes(r.module_id)) && <p className="scroll-note">模块未知，无法执行。</p>}
            </article>
          ))}
        </section>

        <aside>
          <SideBox title="最近执行" extra="最多 20 条">
            {data.logs.length === 0 && <p className="scroll-note">尚无运行记录。</p>}
            {data.logs.map((l, index) => (
              <div className="log-row" key={l.id ?? `${l.name}-${index}`}>
                <span className={`log-dot${l.error ? " red" : l.status === "succeeded" ? "" : " unknown"}`} />
                <div><div className="log-name">{l.name} · {RUN_LABELS[l.status ?? ""] ?? "状态未知"}</div><div className="log-sub">{l.sub}</div></div>
                <span className="log-time">{formatDateTime(l.time)}</span>
              </div>
            ))}
          </SideBox>
          <SideBox title="待确认操作" extra={<span className="pill orange">{data.pending.length}</span>}>
            {data.pending.map((p) => (
              <div className="confirm-row" key={p.title}><b>{p.title}</b><br />{p.desc}</div>
            ))}
            {data.pending.length === 0 && <p className="scroll-note">暂无待确认记录。</p>}
          </SideBox>
          <SideBox title="通知渠道">
            {data.channels.length === 0 && <p className="scroll-note">未配置通知渠道。</p>}
            {data.channels.map((c) => (
              <div className="channel-row" key={c.name}><span>{c.icon}</span><span>{c.name}</span><span className={`pill${c.pill.variant === "green" ? " green" : ""}`}>{c.pill.text}</span></div>
            ))}
          </SideBox>
        </aside>
      </section>
    </main>
  );
}
