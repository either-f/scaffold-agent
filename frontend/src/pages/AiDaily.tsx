import { useState } from "react";
import { postApi, useApi } from "../api";
import { useAuthAction } from "../auth";
import { NewsCard } from "../components/cards";
import type { PreviewRequest } from "../components/DesignPreview";
import { matchesQuery, PageHead, PageStatus, SideBox, Subnav, SummaryCard } from "../components/ui";
import type { NewsData, NewsItem } from "../types";

export function AiDaily({ query: searchQuery, onPreview }: { query: string; onNavigate: (id: string) => void; onPreview: (request: PreviewRequest) => void }) {
  const [subtab, setSubtab] = useState("推荐");
  const { data, error, setData } = useApi<NewsData>("/api/platform/news");
  const { user, run } = useAuthAction();

  async function toggleReadLater(news: NewsItem) {
    const res = await run(() => postApi<{ read_later: boolean }>(`/api/platform/news/${news.id}/read_later`));
    if (res === null) return;
    setData((d) => ({
      ...d,
      items: d.items.map((i) => (i.id === news.id ? { ...i, read_later: res.read_later } : i)),
    }));
  }

  const items = data?.items.filter((news) => {
    const text = [news.title, news.meta, news.desc, news.insight].join(" ");
    const lowerText = text.toLocaleLowerCase();
    const tabTerms: Record<string, string[]> = {
      模型: ["模型", "gpt", "claude", "gemini", "llm", "astra"],
      公司: ["公司", "openai", "anthropic", "google", "microsoft", "meta"],
      人物: ["人物", "作者", "研究员"],
      产品: ["产品", "工具", "应用", "android", "mac"],
      开源: ["开源", "github", "open source"],
    };
    const tabMatch = subtab === "推荐" || (tabTerms[subtab] ?? []).some((term) => lowerText.includes(term));
    return tabMatch && matchesQuery(searchQuery, text);
  }) ?? [];
  const scores = items.map((news) => Number.parseInt(news.score ?? "", 10)).filter(Number.isFinite);
  const today = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" })
    .format(new Date())
    .replace(/\//g, "-");

  const head = (
    <PageHead
      title="AI 日报"
      desc="仅展示已有资讯内容；未执行真实摘要、日报生成或推送。"
      actions={<><button className="small-btn" onClick={() => onPreview({ kind: "subscription" })}>订阅配置预览</button><button className="small-btn primary" onClick={() => onPreview({ kind: "report-generate" })}>日报生成预览</button></>}
    />
  );

  if (data === null) {
    return <main className="page">{head}<PageStatus data={data} error={error} /></main>;
  }

  return (
    <main className="page">
      {head}

      <section className="summary-row">
        <SummaryCard label="当前资讯" num={String(items.length)} note="当前筛选结果" mark="讯" />
        <SummaryCard label="重要事件" num={scores.length ? String(scores.filter((score) => score >= 85).length) : "未知"} note={scores.length ? `重要度 ≥ 85 · ${items.length - scores.length} 项未知，未计入` : "无可核验评分"} mark="重" />
        <SummaryCard label="稍后阅读" num={user ? String(items.filter((news) => news.read_later).length) : "需登录"} note={user ? "仅当前筛选结果 · 非全账户总量" : "登录后查看当前筛选结果中的阅读记录"} mark="读" />
        <SummaryCard label="重点关注" num={String(data.entities.length)} note="公司 / 人物 / 产品" mark="关" />
      </section>

      <Subnav
        tabs={["推荐", "模型", "公司", "人物", "产品", "开源"]}
        active={subtab}
        onSelect={setSubtab}
        right={<button className="small-btn" style={{ height: 34 }} onClick={() => onPreview({ kind: "date-picker" })}>{today}⌄</button>}
      />

      <section className="ai-grid">
        <section className="news-panel">
          {items.length === 0 && <div className="scroll-note">没有符合筛选条件的资讯</div>}
          {items.map((n) => <NewsCard key={n.id} news={n} onReadLater={toggleReadLater} onOpen={() => onPreview({ kind: "news-detail", title: n.title, subtitle: n.meta, score: n.score })} onAction={() => onPreview({ kind: "news-detail", title: n.title, subtitle: n.meta, score: n.score })} />)}
          <div className="scroll-note">已加载 {items.length} 条资讯</div>
        </section>

        <aside>
          <SideBox title="重点关注" extra={<button type="button" className="mini-link-button" onClick={() => onPreview({ kind: "entities" })}>管理 ›</button>}>
            {data.entities.map((e) => (
              <div className="entity-row" key={e.name}>
                <div className={`entity-icon ${e.logo_class}`} style={e.logo_style ? { background: e.logo_style } : undefined}>{e.logo}</div>
                <div><div className="entity-name">{e.name}</div><div className="entity-sub">{e.sub}</div></div>
                <span className="star-btn">{e.star ? "★" : "☆"}</span>
              </div>
            ))}
          </SideBox>
          <SideBox title="热门主题">
            {data.topics.map((t) => (
              <div className="topic-row" key={t.tag}><span>{t.tag}</span><span>{t.count}</span></div>
            ))}
          </SideBox>
          <SideBox title="日报推送" extra={<button type="button" className="mini-link-button" onClick={() => onPreview({ kind: "subscription" })}>配置预览 ›</button>}>
            <div className="daily-box">
              <div style={{ fontSize: 12.5, fontWeight: 700 }}>未接入</div>
              <div style={{ fontSize: 11, color: "#96a1ad", marginTop: 4 }}>不会发送站内消息或邮件；配置仅供预览。</div>
            </div>
          </SideBox>
        </aside>
      </section>
    </main>
  );
}
