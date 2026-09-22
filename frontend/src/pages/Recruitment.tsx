import { useMemo, useState } from "react";
import { postApi, useApi } from "../api";
import { useAuthAction } from "../auth";
import { JobCard } from "../components/cards";
import type { PreviewRequest } from "../components/DesignPreview";
import { FilterGroup, matchesQuery, PageHead, PageStatus, SideBox, Subnav, SummaryCard } from "../components/ui";
import type { JobItem, JobsData } from "../types";

const CITIES = ["北京", "上海", "深圳", "杭州", "武汉"].map((label) => ({ label }));
const DIRECTIONS = ["后端开发", "算法", "前端开发", "测试开发"].map((label) => ({ label }));
const YEARS = ["2027届", "2026届", "不限"].map((l) => ({ label: l }));
const MATCH = ["85% 以上", "70% 以上"].map((l) => ({ label: l }));

function toggle(list: string[], label: string): string[] {
  return list.includes(label) ? list.filter((x) => x !== label) : [...list, label];
}

export function Recruitment({ query: searchQuery, onPreview }: { query: string; onNavigate: (id: string) => void; onPreview: (request: PreviewRequest) => void }) {
  const [subtab, setSubtab] = useState("推荐");
  const [city, setCity] = useState<string[]>([]);
  const [direction, setDirection] = useState<string[]>([]);
  const [year, setYear] = useState<string[]>([]);
  const [match, setMatch] = useState<string[]>([]);

  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (city.length) params.set("city", city.join(","));
    if (direction.length) params.set("direction", direction.join(","));
    if (year.length) params.set("grad_year", year.join(","));
    const matchMin = match.includes("85% 以上") ? 85 : match.includes("70% 以上") ? 70 : 0;
    if (matchMin) params.set("match_min", String(matchMin));
    if (subtab === "内推") params.set("referral", "true");
    const q = params.toString();
    return q ? `/api/platform/jobs?${q}` : "/api/platform/jobs";
  }, [city, direction, year, match, subtab]);

  const { data, error, setData } = useApi<JobsData>(query);
  const { user, run } = useAuthAction();

  const items = data?.items.filter((job) => {
    const tabMatch = subtab === "推荐" || subtab === "最新"
      || (subtab === "内推" && job.referral)
      || (subtab === "校招" && job.meta.includes("校招"))
      || (subtab === "实习" && job.meta.includes("实习"));
    return tabMatch && matchesQuery(searchQuery, job.title, job.meta, job.salary, job.reason, ...job.tags);
  }) ?? [];
  const scores = items.map((job) => Number.parseInt(job.match ?? "", 10)).filter(Number.isFinite);
  const favoriteCount = items.filter((job) => job.favorited).length;
  const appliedCount = items.filter((job) => job.applied).length;

  async function toggleFavorite(job: JobItem) {
    const res = await run(() => postApi<{ favorited: boolean }>(`/api/platform/jobs/${job.id}/favorite`));
    if (res === null) return;
    setData((d) => ({
      ...d,
      items: d.items.map((i) => (i.id === job.id ? { ...i, favorited: res.favorited } : i)),
    }));
  }

  async function applyJob(job: JobItem) {
    const res = await run(() => postApi<{ applied: boolean }>(`/api/platform/jobs/${job.id}/apply`));
    if (res === null) return;
    setData((d) => ({
      ...d,
      items: d.items.map((i) => (i.id === job.id ? { ...i, applied: res.applied } : i)),
    }));
  }

  const head = (
    <PageHead
      title="招聘"
      desc="按偏好筛选已有岗位；投递仅保存站内记录，不会向外站提交。"
      actions={<>
        <button className="small-btn" onClick={() => onPreview({ kind: "applications" })}>投递记录预览</button>
        <button className="small-btn primary" onClick={() => onPreview({ kind: "job-create" })}>＋ 添加岗位</button>
      </>}
    />
  );

  if (data === null) {
    return <main className="page">{head}<PageStatus data={data} error={error} /></main>;
  }

  return (
    <main className="page">
      {head}

      <section className="summary-row">
        <SummaryCard label="当前岗位" num={String(items.length)} note="当前筛选结果" mark="新" />
        <SummaryCard label="高匹配岗位" num={scores.length ? String(scores.filter((score) => score >= 85).length) : "未知"} note={scores.length ? `匹配度 ≥ 85% · ${items.length - scores.length} 项未知，未计入` : "无可核验评分"} mark="匹" />
        <SummaryCard label="已收藏" num={user ? String(favoriteCount) : "需登录"} note={user ? "仅当前筛选结果 · 非全账户总量" : "登录后查看当前筛选结果中的收藏"} mark="藏" />
        <SummaryCard label="已记录投递" num={user ? String(appliedCount) : "需登录"} note={user ? "仅当前筛选结果 · 非外站提交" : "登录后查看站内记录 · 非外站提交"} mark="投" />
      </section>

      <Subnav
        tabs={["推荐", "最新", "校招", "实习", "内推"]}
        active={subtab}
        onSelect={setSubtab}
        right={<select className="dropdown"><option>默认排序</option></select>}
      />

      <section className="recruit-grid">
        <aside className="filter-panel">
          <FilterGroup name="城市" options={CITIES} active={city} onToggle={(l) => setCity((p) => toggle(p, l))} />
          <FilterGroup name="岗位方向" options={DIRECTIONS} active={direction} onToggle={(l) => setDirection((p) => toggle(p, l))} />
          <FilterGroup name="毕业年份" options={YEARS} active={year} onToggle={(l) => setYear((p) => toggle(p, l))} />
          <FilterGroup name="匹配度" options={MATCH} active={match} onToggle={(l) => setMatch((p) => toggle(p, l))} />
        </aside>

        <section className="job-panel">
          <div className="job-toolbar">
            <select className="dropdown"><option>全部来源</option></select>
            <select className="dropdown"><option>全部公司</option></select>
            <select className="dropdown"><option>薪资不限</option></select>
          <button className="small-btn" style={{ height: 34 }} onClick={() => onPreview({ kind: "filters" })}>更多筛选</button>
          <span style={{ marginLeft: "auto", fontSize: 12, color: "#9aa4b1" }}>共 {items.length} 个岗位</span>
          </div>

          {items.length === 0 && <div className="scroll-note">没有符合筛选条件的岗位</div>}
          {items.map((job) => <JobCard key={job.id} job={job} onFavorite={toggleFavorite} onApply={applyJob} onAction={(action) => onPreview({ kind: "job-detail", title: job.title, subtitle: action === "查看详情" ? job.meta : action, score: job.match })} />)}
          <div className="scroll-note">已加载 {items.length} 个岗位</div>
        </section>

        <aside>
          <SideBox title="我的关注" extra={<button type="button" className="mini-link-button" onClick={() => onPreview({ kind: "watchlist" })}>管理 ›</button>}>
            <div className="follow-list">
              {data.follows.map((f) => (
                <div className="follow-row" key={f.name}>
                  <div className={`follow-logo ${f.logo_class}`}>{f.logo}</div>
                  <div><div className="follow-name">{f.name}</div><div className="follow-sub">{f.sub}</div></div>
                  <span className={`pill${f.pill.variant === "green" ? " green" : ""}`}>{f.pill.text}</span>
                </div>
              ))}
            </div>
          </SideBox>
          <SideBox title="当前结果状态" extra={<button type="button" className="mini-link-button" onClick={() => onPreview({ kind: "applications" })}>记录预览 ›</button>}>
            {user ? <div className="status-list">
              <div className="status-row"><span className="dot" />已收藏 <span className="count">{favoriteCount}</span></div>
              <div className="status-row"><span className="dot" />已记录投递 <span className="count">{appliedCount}</span></div>
              <div className="section-sub">仅当前筛选结果 · 非全账户总量，投递记录不向外站提交。</div>
            </div> : <div className="side-empty">登录后查看当前筛选结果中的收藏与站内投递记录。</div>}
          </SideBox>
          <div className="side-box">
            <div className="section-title">下一场面试</div>
            <div className="section-sub">{data.interview.time}</div>
            <div className="soft-panel" style={{ padding: 12, marginTop: 12, fontSize: 12, lineHeight: 1.6, color: "#60706c" }}>{data.interview.note}</div>
            <button className="btn primary" style={{ width: "100%", marginTop: 12 }} onClick={() => onPreview({ kind: "interview" })}>开始准备</button>
          </div>
        </aside>
      </section>
    </main>
  );
}
