import { useMemo, useState } from "react";
import { postApi, useApi } from "../api";
import { useAuthAction } from "../auth";
import { ProjectCard } from "../components/cards";
import type { PreviewRequest } from "../components/DesignPreview";
import { FilterGroup, matchesQuery, PageHead, PageStatus, SideBox, Subnav, SummaryCard } from "../components/ui";
import type { ProjectItem, ProjectsData } from "../types";

function toggle(list: string[], label: string): string[] {
  return list.includes(label) ? list.filter((x) => x !== label) : [...list, label];
}

export function Projects({ query: searchQuery, onPreview }: { query: string; onNavigate: (id: string) => void; onPreview: (request: PreviewRequest) => void }) {
  const [subtab, setSubtab] = useState("推荐");
  const [tech, setTech] = useState<string[]>([]);
  const [lang, setLang] = useState<string[]>([]);
  const [star, setStar] = useState<string[]>([]);

  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (tech.length) params.set("tech", tech.join(","));
    if (lang.length) params.set("language", lang.join(","));
    const starMap: Record<string, string> = { "1k 以上": "1k", "10k 以上": "10k", "今日高增长": "hot" };
    const starVal = star.map((s) => starMap[s]).filter(Boolean)[0];
    if (starVal) params.set("star", starVal);
    const q = params.toString();
    return q ? `/api/platform/projects?${q}` : "/api/platform/projects";
  }, [tech, lang, star]);

  const { data, error, setData } = useApi<ProjectsData>(query);
  const { user, run } = useAuthAction();

  const items = data?.items.filter((project) => {
    const text = [project.name, project.full_name, project.meta, project.desc, ...project.tags].join(" ");
    const tabMatch = subtab === "推荐" || subtab === "Trending"
      || text.toLocaleLowerCase().includes(subtab.toLocaleLowerCase());
    return tabMatch && matchesQuery(searchQuery, text);
  }) ?? [];
  const scores = items.map((project) => Number.parseInt(project.score ?? "", 10)).filter(Number.isFinite);

  async function toggleFavorite(project: ProjectItem) {
    const res = await run(() => postApi<{ favorited: boolean }>(`/api/platform/projects/${project.id}/favorite`));
    if (res === null) return;
    setData((d) => ({
      ...d,
      items: d.items.map((i) => (i.id === project.id ? { ...i, favorited: res.favorited } : i)),
    }));
  }

  async function toggleWatch(project: ProjectItem) {
    const res = await run(() => postApi<{ watched: boolean }>(`/api/platform/projects/${project.id}/watch`));
    if (res === null) return;
    setData((d) => ({
      ...d,
      items: d.items.map((i) => (i.id === project.id ? { ...i, watched: res.watched } : i)),
    }));
  }

  function openRepo(project: ProjectItem) {
    onPreview({ kind: "project-detail", title: project.name, subtitle: project.meta, score: project.score });
  }

  const head = (
    <PageHead
      title="项目雷达"
      desc="聚合 GitHub 榜单与技术社区信号，筛出真正值得研究的项目。"
      actions={<><button className="small-btn" onClick={() => onPreview({ kind: "favorites" })}>我的收藏</button><button className="small-btn primary" onClick={() => onPreview({ kind: "watchlist" })}>＋ 关注仓库</button></>}
    />
  );

  if (data === null) {
    return <main className="page">{head}<PageStatus data={data} error={error} /></main>;
  }

  return (
    <main className="page">
      {head}

      <section className="summary-row">
        <SummaryCard label="当前项目" num={String(items.length)} note="当前筛选结果" mark="新" />
        <SummaryCard label="强推荐" num={scores.length ? String(scores.filter((score) => score >= 85).length) : "未知"} note={scores.length ? `推荐度 ≥ 85 · ${items.length - scores.length} 项未知，未计入` : "无可核验评分"} mark="荐" />
        <SummaryCard label="已收藏" num={user ? String(items.filter((project) => project.favorited).length) : "需登录"} note={user ? "仅当前筛选结果 · 非全账户总量" : "登录后查看当前筛选结果中的收藏"} mark="藏" />
        <SummaryCard label="已关注" num={user ? String(items.filter((project) => project.watched).length) : "需登录"} note={user ? "仅当前筛选结果 · 非全账户总量" : "登录后查看当前筛选结果中的关注"} mark="跟" />
      </section>

      <Subnav
        tabs={["推荐", "Trending", "Agent", "RAG", "Infra", "数据工程"]}
        active={subtab}
        onSelect={setSubtab}
        right={<select className="dropdown"><option>综合排序</option></select>}
      />

      <section className="project-grid">
        <aside className="filter-panel">
          <FilterGroup name="技术方向" options={["Agent", "RAG", "LLM Infra", "Vector DB"].map((label) => ({ label }))} active={tech} onToggle={(l) => setTech((p) => toggle(p, l))} />
          <FilterGroup name="语言" options={["Python", "Java", "TypeScript", "Go"].map((label) => ({ label }))} active={lang} onToggle={(l) => setLang((p) => toggle(p, l))} />
          <FilterGroup name="Star" options={["1k 以上", "10k 以上", "今日高增长"].map((l) => ({ label: l }))} active={star} onToggle={(l) => setStar((p) => toggle(p, l))} />
        </aside>

        <section className="project-panel">
          <div className="job-toolbar">
            <select className="dropdown"><option>全部来源</option></select>
            <select className="dropdown"><option>最近 24 小时</option></select>
            <span style={{ marginLeft: "auto", fontSize: 12, color: "#9aa4b1" }}>当前筛选结果 {items.length} 个</span>
          </div>

          {items.length === 0 && <div className="scroll-note">没有符合筛选条件的项目</div>}
          {items.map((p) => (
            <ProjectCard key={p.id} project={p} onFavorite={toggleFavorite} onWatch={toggleWatch} onOpen={openRepo} />
          ))}
          <div className="scroll-note">已加载 {items.length} 个项目</div>
        </section>

        <aside>
          <SideBox title="项目趋势" extra="7 天 ›">
            <div className="spark">
              {data.trend.map((t, i) => <i key={i} style={{ height: `${t.height}%` }} />)}
            </div>
          </SideBox>
          <SideBox title="热门技术">
            <div className="topic-cloud">{data.topics.map((t) => <span key={t}>{t}</span>)}</div>
          </SideBox>
          <SideBox title="持续关注" extra={<button type="button" className="mini-link-button" onClick={() => onPreview({ kind: "watchlist" })}>管理 ›</button>}>
            {data.watches.map((w) => (
              <div className="watch-row" key={w.name}><span className="dot" />{w.name} <span className="watch-count">{w.count}</span></div>
            ))}
          </SideBox>
        </aside>
      </section>
    </main>
  );
}
