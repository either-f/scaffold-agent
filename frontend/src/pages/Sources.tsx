import { useState } from "react";
import { useApi } from "../api";
import { COLLECTION_MODULES, useCollectionRun } from "../collection";
import { CollectionStatus } from "../components/CollectionStatus";
import type { PreviewRequest } from "../components/DesignPreview";
import { matchesQuery, PageHead, PageStatus, SideBox, Subnav, SummaryCard } from "../components/ui";
import { formatDateTime } from "../demo";
import type { SourcesData } from "../types";

const sourceStatus = (status: string) => ["ok", "succeeded"].includes(status) ? "succeeded" : ["warn", "failed", "partial", "interrupted"].includes(status) ? "failed" : ["running", "queued"].includes(status) ? "running" : "unknown";
const STATUS_LABELS: Record<string, string> = { succeeded: "最近成功", failed: "最近失败", running: "等待 / 运行中", unknown: "未知 · 尚未验证" };

export function Sources({ query: searchQuery, onNavigate }: { query: string; onNavigate: (id: string) => void; onPreview: (request: PreviewRequest) => void }) {
  const [subtab, setSubtab] = useState("全部");
  const [status, setStatus] = useState("all");
  const { data, error, refresh } = useApi<SourcesData>("/api/platform/sources");
  const collection = useCollectionRun(refresh);

  const items = data?.items.filter((source) => {
    const categoryMatch = subtab === "全部" || source.category === subtab;
    const statusMatch = status === "all" || sourceStatus(source.status) === status;
    return categoryMatch && statusMatch && matchesQuery(searchQuery, source.name, source.sub, source.category, source.status_text);
  }) ?? [];

  const head = (
    <PageHead
      title="数据源"
      desc="统一管理招聘、GitHub、新闻、社交平台与企业官网采集连接。"
      actions={<>
        <button className="small-btn" onClick={refresh}>刷新来源状态</button>
        <button className="small-btn" onClick={() => onNavigate("automation")}>查看运行记录</button>
        <a className="small-btn primary" href="?demo=1#/connections">配置来源（进入演示）</a>
      </>}
    />
  );

  if (data === null) {
    return <main className="page">{head}<PageStatus data={data} error={error} />{error && <button className="small-btn" onClick={refresh}>重试读取</button>}</main>;
  }

  const warnCount = data.items.filter((s) => sourceStatus(s.status) === "failed").length;
  const succeededCount = data.items.filter((s) => sourceStatus(s.status) === "succeeded").length;
  const unknownCount = data.items.filter((s) => sourceStatus(s.status) === "unknown").length;
  const collectedToday = data.metrics.find((metric) => metric.label === "新增条目")?.value ?? "未知";

  return (
    <main className="page">
      {head}

      <section className="summary-row">
        <SummaryCard label="来源数量" num={String(data.items.length)} note={`${new Set(data.items.map((item) => item.category)).size} 类来源`} mark="源" />
        <SummaryCard label="最近成功" num={String(succeededCount)} note={`${unknownCount} 个状态未知，不代表健康`} mark="✓" />
        <SummaryCard label="今日采集" num={collectedToday} note="未计量时显示未知" mark="采" />
        <SummaryCard label="失败 / 中断" num={String(warnCount)} note="查看具体错误与运行记录" mark="!" />
      </section>

      <Subnav
        tabs={["全部", ...new Set(data.items.map((item) => item.category))]}
        active={subtab}
        onSelect={setSubtab}
        right={<select className="dropdown" aria-label="来源状态" value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">状态：全部</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>}
      />

      <CollectionStatus {...collection} />

      <section className="sources-grid">
        <section className="source-panel">
          <table className="source-table workspace-source-table">
            <thead>
              <tr><th>数据源</th><th>类型</th><th>状态</th><th>最近采集</th><th>今日条目</th><th>操作</th></tr>
            </thead>
            <tbody>
              {items.map((s) => (
                <tr key={s.id}>
                  <td>
                    <div className="source-cell">
                      <div className={`source-dot ${s.logo_class}`} style={s.logo_style ? { background: s.logo_style } : undefined}>{s.logo}</div>
                      <div><b>{s.name}</b><div style={{ fontSize: 11, color: "#9aa4b1", marginTop: 3 }}>{s.sub}</div></div>
                    </div>
                  </td>
                  <td data-label="类型">{s.category}</td>
                  <td data-label="状态"><span className={`health ${sourceStatus(s.status)}`}><i />{sourceStatus(s.status) === "unknown" ? STATUS_LABELS.unknown : s.status_text || STATUS_LABELS[sourceStatus(s.status)]}</span></td>
                  <td data-label="最近采集">{formatDateTime(s.last)}</td>
                  <td data-label="今日条目">{s.today ?? "未知"}</td>
                  <td className="action-icon">
                    <button type="button" className="table-action" disabled={collection.busy || !s.module_id || !COLLECTION_MODULES.includes(s.module_id)} onClick={() => s.module_id && collection.start(s.module_id)}>{sourceStatus(s.status) === "failed" ? "重试采集" : "手动采集"}</button>
                    <button type="button" className="table-action" onClick={() => onNavigate("automation")}>查看记录</button>
                    {!s.module_id && <span>模块未配置</span>}
                  </td>
                </tr>
              ))}
              {items.length === 0 && <tr><td colSpan={6} className="scroll-note">没有符合筛选条件的数据源</td></tr>}
            </tbody>
          </table>
        </section>

        <aside>
          <div className="side-box">
            <div className="section-title">添加数据源</div>
            <div className="section-sub">来源配置尚未接入真实保存。可在独立演示中体验表单和连接状态。</div>
            <a className="add-card" href="?demo=1#/connections">进入来源配置演示 →</a>
          </div>
          <SideBox title="采集概览">
            {data.metrics.map((m) => (
              <div className="metric-row" key={m.label}><span>{m.label}</span><b style={m.error ? { color: "#ef6262" } : undefined}>{m.value}</b></div>
            ))}
            {data.metrics.length === 0 && <p className="scroll-note">暂无计量数据，健康率未知。</p>}
          </SideBox>
          <SideBox title="异常提醒">
            {data.alerts.length === 0 && <p className="scroll-note">暂无异常记录；未知来源仍待验证。</p>}
            {data.alerts.map((a) => (
              <div className="confirm-row" key={a.title}><b>{a.title}</b><br />{a.desc}</div>
            ))}
          </SideBox>
        </aside>
      </section>
    </main>
  );
}
