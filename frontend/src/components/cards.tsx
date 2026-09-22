import type { JobItem, NewsItem, ProjectItem } from "../types";

export function JobCard({
  job,
  onFavorite,
  onApply,
  onAction,
}: {
  job: JobItem;
  onFavorite?: (job: JobItem) => void;
  onApply?: (job: JobItem) => void;
  onAction?: (action: string) => void;
}) {
  const restActions = job.actions.filter((a) => a !== "收藏");
  return (
    <article className="job-card">
      <div className="job-top">
        <div className={`logo-box ${job.logo_class}`}>{job.logo}</div>
        <div>
          <div className="job-title">
            {job.title}
            {job.referral && <span className="badge-soft" style={{ marginLeft: 8, verticalAlign: "middle" }}>内推</span>}
            {job.applied && <span className="badge-soft blue" style={{ marginLeft: 8, verticalAlign: "middle" }}>已记录投递</span>}
          </div>
          <div className="job-meta">{job.meta}</div>
          <div className="job-tags">{job.tags.map((t) => <span className="tag" key={t}>{t}</span>)}</div>
        </div>
        <div className="match-card">
          <div className="match-num">{job.match?.trim() || "未知"}</div>
          <div className="match-label">匹配度</div>
          <div style={{ marginTop: 8, fontSize: 12, fontWeight: 700 }}>{job.salary}</div>
        </div>
      </div>
      {job.reason?.trim() && <div className="job-reason"><b>AI 推荐理由</b>：{job.reason}</div>}
      <div className="job-actions">
        {onFavorite && (
          <button className={`btn${job.favorited ? " primary" : ""}`} onClick={() => onFavorite(job)}>
            {job.favorited ? "已收藏 ✓" : "收藏"}
          </button>
        )}
        {onApply && (
          <button className={`btn${job.applied ? "" : " primary"}`} disabled={job.applied} title="仅保存站内投递记录，不会向外站提交" onClick={() => onApply(job)}>
            {job.applied ? "已记录投递 ✓" : "记录投递（非外站）"}
          </button>
        )}
        {restActions.map((a) => (
          <button key={a} className={`btn${a === "查看详情" ? " primary" : ""}`} onClick={() => onAction?.(a)}>{a}</button>
        ))}
      </div>
    </article>
  );
}

export function ProjectCard({
  project,
  onFavorite,
  onWatch,
  onOpen,
  onAction,
}: {
  project: ProjectItem;
  onFavorite?: (project: ProjectItem) => void;
  onWatch?: (project: ProjectItem) => void;
  onOpen?: (project: ProjectItem) => void;
  onAction?: (action: string) => void;
}) {
  return (
    <article className="project-card">
      <div className={`logo-box ${project.logo_class}`}>{project.logo}</div>
      <div>
        <div className="repo-name">{project.name}</div>
        <div className="repo-meta">{project.meta}</div>
        <div className="job-tags">{project.tags.map((t) => <span className="tag" key={t}>{t}</span>)}</div>
        <div className="repo-desc">{project.desc}</div>
        {project.reason?.trim() && <div className="job-reason"><b>AI 推荐理由</b>：{project.reason}</div>}
      </div>
      <div className="repo-score">
        <b>{project.score?.trim() || "未知"}</b><div>推荐指数</div>
        <div style={{ marginTop: 13 }}>{project.rank}</div>
      </div>
      <div className="repo-actions">
        {onFavorite && (
          <button className={`btn${project.favorited ? " primary" : ""}`} onClick={() => onFavorite(project)}>
            {project.favorited ? "已收藏 ✓" : "收藏"}
          </button>
        )}
        {onWatch && (
          <button className={`btn${project.watched ? " primary" : ""}`} onClick={() => onWatch(project)}>
            {project.watched ? "关注中 ✓" : "关注更新"}
          </button>
        )}
        <button className="btn primary" onClick={() => onOpen ? onOpen(project) : onAction?.("查看项目")}>查看项目</button>
      </div>
    </article>
  );
}

export function NewsCard({
  news,
  onReadLater,
  onOpen,
  onAction,
}: {
  news: NewsItem;
  onReadLater?: (news: NewsItem) => void;
  onOpen?: (news: NewsItem) => void;
  onAction?: (action: string) => void;
}) {
  const url = news.desc.startsWith("http") ? news.desc : null;
  return (
    <article className="news-card">
      <div className={`logo-box ${news.logo_class}`}>{news.logo}</div>
      <div>
        <div className="news-title">{news.title}</div>
        <div className="news-meta">{news.meta}</div>
        <div className="news-desc">{news.desc}</div>
      </div>
      <div className="news-score"><b>{news.score?.trim() || "未知"}</b><div>重要度</div></div>
      {news.insight?.trim() && <div className="news-insight"><b style={{ color: "#7056cf" }}>AI 洞察：</b>{news.insight}</div>}
      <div className="news-actions">
        {onReadLater && (
          <button className={`btn${news.read_later ? " primary" : ""}`} onClick={() => onReadLater(news)}>
            {news.read_later ? "已加入 ✓" : "稍后阅读"}
          </button>
        )}
        <button
          className="btn primary"
          onClick={() => {
            if (url && onOpen) onOpen(news);
            else if (url) window.open(url, "_blank", "noopener,noreferrer");
            else onAction?.("阅读全文");
          }}
        >
          阅读全文
        </button>
      </div>
    </article>
  );
}
