import { useState, type ReactNode } from "react";

export type PreviewKind =
  | "job-detail" | "project-detail" | "news-detail"
  | "applications" | "job-create" | "filters" | "interview"
  | "favorites" | "watchlist"
  | "subscription" | "report-generate" | "date-picker" | "entities"
  | "automation-logs" | "automation-create" | "automation-edit" | "automation-pending"
  | "source-docs" | "source-create" | "source-manage"
  | "notifications" | "help" | "profile";

export type PreviewRequest = {
  kind: PreviewKind;
  title?: string;
  subtitle?: string;
  score?: string | null;
};

const META: Record<PreviewKind, { eyebrow: string; title: string; desc: string }> = {
  "job-detail": { eyebrow: "岗位详情 · 原型", title: "岗位详情", desc: "查看岗位信息、AI 匹配理由与投递准备项。" },
  "project-detail": { eyebrow: "项目详情 · 原型", title: "项目详情", desc: "查看仓库概览、技术标签与项目动态。" },
  "news-detail": { eyebrow: "资讯详情 · 原型", title: "资讯详情", desc: "查看文章摘要、AI 洞察与相关主题。" },
  applications: { eyebrow: "招聘工作台 · 原型", title: "投递记录预览", desc: "展示站内投递记录示例，不代表向外站提交。" },
  "job-create": { eyebrow: "招聘工作台 · 原型", title: "添加岗位", desc: "先用表单录入一个岗位，后续再接入保存接口。" },
  filters: { eyebrow: "招聘筛选 · 原型", title: "更多筛选", desc: "组合公司、薪资、来源和工作模式筛选条件。" },
  interview: { eyebrow: "求职准备 · 原型", title: "面试准备", desc: "围绕岗位要求生成准备清单和复习计划。" },
  favorites: { eyebrow: "项目工作台 · 原型", title: "我的收藏", desc: "查看收藏的项目，并按主题和更新时间整理。" },
  watchlist: { eyebrow: "关注管理 · 原型", title: "持续关注", desc: "管理岗位、项目和数据源的更新提醒。" },
  subscription: { eyebrow: "日报设置 · 原型", title: "订阅设置", desc: "配置日报内容、推送时间和通知渠道。" },
  "report-generate": { eyebrow: "日报任务 · 原型", title: "生成今日日报", desc: "预览日报生成流程和本次任务的输出范围。" },
  "date-picker": { eyebrow: "日报浏览 · 原型", title: "选择日期", desc: "按日期查看已经生成的日报。" },
  entities: { eyebrow: "日报设置 · 原型", title: "重点关注", desc: "管理公司、人物和产品的关注状态。" },
  "automation-logs": { eyebrow: "自动化工作台 · 原型", title: "执行日志", desc: "查看每次自动化任务的触发、结果和错误信息。" },
  "automation-create": { eyebrow: "自动化工作台 · 原型", title: "新建自动化", desc: "通过触发器、条件和动作搭建一条规则。" },
  "automation-edit": { eyebrow: "自动化工作台 · 原型", title: "编辑自动化", desc: "调整规则节点和通知策略。" },
  "automation-pending": { eyebrow: "自动化工作台 · 原型", title: "待确认操作", desc: "人工确认自动化准备执行的高风险动作。" },
  "source-docs": { eyebrow: "数据源工作台 · 原型", title: "接入文档", desc: "按平台类型查看接入要求、权限范围和采集说明。" },
  "source-create": { eyebrow: "数据源工作台 · 原型", title: "添加数据源", desc: "选择平台类型并配置连接信息。" },
  "source-manage": { eyebrow: "数据源工作台 · 原型", title: "数据源管理", desc: "查看连接状态、采集策略和最近错误。" },
  notifications: { eyebrow: "系统中心 · 原型", title: "通知中心", desc: "集中查看岗位、项目、日报和自动化通知。" },
  help: { eyebrow: "系统中心 · 原型", title: "帮助中心", desc: "通过常见问题和使用指引了解 Scaffold。" },
  profile: { eyebrow: "账户中心 · 原型", title: "个人资料", desc: "管理个人偏好、求职方向和通知设置。" },
};

function PreviewButton({ children, primary, onClick }: { children: ReactNode; primary?: boolean; onClick: () => void }) {
  return <button type="button" className={`btn${primary ? " primary" : ""}`} onClick={onClick}>{children}</button>;
}

function Section({ title, children, extra }: { title: string; children: ReactNode; extra?: ReactNode }) {
  return <section className="preview-section"><div className="preview-section-head"><h3>{title}</h3>{extra}</div>{children}</section>;
}

function DetailBody({ request, act }: { request: PreviewRequest; act: (message: string) => void }) {
  const isJob = request.kind === "job-detail";
  const isProject = request.kind === "project-detail";
  const title = request.title ?? (isJob ? "后端开发工程师" : isProject ? "hermes-agent" : "GPT 6 Astra 重构的我的博客首页");
  const subtitle = request.subtitle ?? (isJob ? "V2EX 酷工作 · 远程 · 社招" : isProject ? "NousResearch/hermes-agent · Python" : "V2EX 热门 · 刚刚");
  const demoScore = request.score === undefined;
  const score = demoScore ? (isJob ? "92%" : isProject ? "95" : "80") : request.score?.trim() || "未知";
  return <>
    <div className="preview-detail-hero">
      <div className={`preview-avatar ${isProject ? "dark" : isJob ? "blue" : "purple"}`}>{isProject ? "GH" : isJob ? "V" : "N"}</div>
      <div className="preview-detail-title"><h3>{title}</h3><p>{subtitle}</p><div className="preview-chips"><span>{isJob ? "招聘" : isProject ? "Trending" : "AI 资讯"}</span><span>{isJob ? "后端开发" : isProject ? "Agent" : "模型"}</span><span>本地原型数据</span></div></div>
      <div className="preview-score"><b>{score}</b><span>{isJob ? "匹配度" : isProject ? "推荐指数" : "重要度"}{demoScore ? "（演示）" : ""}</span></div>
    </div>
    <div className="preview-actions">
      <PreviewButton onClick={() => act("切换收藏")}>收藏</PreviewButton>
      <PreviewButton primary onClick={() => act(isJob ? "记录站内投递（非外站提交）" : isProject ? "关注更新" : "稍后阅读")}>{isJob ? "投递记录演示" : isProject ? "关注更新" : "稍后阅读"}</PreviewButton>
      <PreviewButton onClick={() => act("打开来源")}>打开来源</PreviewButton>
    </div>
    <Section title={isJob ? "AI 推荐理由" : isProject ? "项目判断" : "AI 洞察"}>
      <div className="preview-highlight">{score === "未知" ? "无可核验评分，请结合原始内容与来源判断。" : "示例判断，仅用于原型展示，非真实评估；请结合原始内容与来源判断。"}</div>
    </Section>
    <Section title={isJob ? "岗位信息" : isProject ? "仓库概览" : "内容摘要"}>
      <div className="preview-copy">{isJob ? "负责服务端接口、数据处理和业务系统稳定性建设；需要熟悉 Python / Java、数据库和常见缓存组件。" : isProject ? "一个面向 Agent 场景的开源项目，包含模型调用、工具编排和任务执行能力。" : "这是一条用于展示文章正文摘要的本地原型内容。接入 API 后，这里将替换为真实正文和来源链接。"}</div>
      <div className="preview-chip-list"><span>Python</span><span>Agent</span><span>RAG</span><span>远程协作</span></div>
    </Section>
    <Section title="下一步">
      <div className="preview-checklist"><div><i className="done" />查看完整要求</div><div><i />确认个人匹配项</div><div><i />准备下一步材料</div></div>
    </Section>
  </>;
}

function Rows({ rows, act }: { rows: { title: string; meta: string; status: string }[]; act: (message: string) => void }) {
  return <div className="preview-rows">{rows.map((row) => <button type="button" className="preview-row" key={row.title} onClick={() => act(`选择 ${row.title}`)}><span className="preview-row-icon">{row.title.slice(0, 1)}</span><span><b>{row.title}</b><small>{row.meta}</small></span><em>{row.status}</em></button>)}</div>;
}

function FormBody({ kind, act }: { kind: PreviewKind; act: (message: string) => void }) {
  const isSource = kind === "source-create";
  const isAutomation = kind === "automation-create" || kind === "automation-edit";
  return <>
    <div className="preview-form-grid">
      <label>{isSource ? "平台类型" : isAutomation ? "规则名称" : "岗位名称"}<input placeholder={isSource ? "选择数据源类型" : isAutomation ? "例如：每日岗位订阅" : "例如：后端开发工程师"} /></label>
      <label>{isSource ? "连接名称" : isAutomation ? "触发方式" : "公司名称"}<input placeholder={isSource ? "例如：GitHub Trending" : isAutomation ? "定时 / 实时 / 手动" : "例如：某科技公司"} /></label>
      <label>{isSource ? "采集频率" : isAutomation ? "筛选条件" : "工作城市"}<input placeholder={isSource ? "每小时 / 每天" : isAutomation ? "匹配度 ≥ 85%" : "北京 / 上海 / 远程"} /></label>
      <label>{isSource ? "认证方式" : isAutomation ? "执行动作" : "岗位链接"}<input placeholder={isSource ? "Token / OAuth / Cookie" : isAutomation ? "通知 / 收藏 / 采集" : "https://"} /></label>
    </div>
    <label className="preview-textarea-label">备注<textarea placeholder="补充说明、筛选偏好或使用限制……" /></label>
    <div className="preview-form-actions"><PreviewButton onClick={() => act("取消")}>取消</PreviewButton><PreviewButton primary onClick={() => act("保存原型配置")}>保存原型配置</PreviewButton></div>
  </>;
}

function PreviewBody({ request, act }: { request: PreviewRequest; act: (message: string) => void }) {
  switch (request.kind) {
    case "job-detail": case "project-detail": case "news-detail": return <DetailBody request={request} act={act} />;
    case "job-create": case "source-create": case "automation-create": case "automation-edit": return <FormBody kind={request.kind} act={act} />;
    case "applications": return <><div className="preview-stat-grid"><div><small>全部投递</small><b>18</b></div><div><small>待投递</small><b>7</b></div><div><small>面试中</small><b>3</b></div></div><Rows act={act} rows={[{ title: "后端开发工程师", meta: "字节跳动 · 北京 · 昨天", status: "待投递" }, { title: "AI 数据工程师", meta: "腾讯 · 深圳 · 2 天前", status: "面试中" }, { title: "Rust 工程师", meta: "远程 · 3 天前", status: "已投递" }]} /></>;
    case "favorites": return <><div className="preview-chip-list"><span className="selected">全部</span><span>Agent</span><span>RAG</span><span>今日更新</span></div><Rows act={act} rows={[{ title: "hermes-agent", meta: "NousResearch · Python · 95", status: "今日更新" }, { title: "browser-use", meta: "browser-use · Python · 95", status: "已收藏" }, { title: "ComfyUI", meta: "Comfy-Org · Python · 95", status: "已收藏" }]} /></>;
    case "watchlist": return <><div className="preview-stat-grid"><div><small>关注岗位</small><b>4</b></div><div><small>关注项目</small><b>15</b></div><div><small>今日更新</small><b>7</b></div></div><Rows act={act} rows={[{ title: "OpenHands", meta: "项目 · 5 次更新", status: "项目" }, { title: "字节跳动", meta: "公司 · 32 个在招岗位", status: "岗位" }, { title: "GitHub Trending", meta: "数据源 · 10 分钟前", status: "数据源" }]} /></>;
    case "filters": return <><div className="preview-filter-grid">{["公司：不限", "薪资：不限", "工作模式：不限", "来源：全部", "经验：不限", "技能：后端"].map((x) => <label key={x}><input type="checkbox" />{x}</label>)}</div><div className="preview-form-actions"><PreviewButton onClick={() => act("重置筛选")}>重置</PreviewButton><PreviewButton primary onClick={() => act("应用筛选")}>应用筛选</PreviewButton></div></>;
    case "interview": return <><div className="preview-progress"><span style={{ width: "42%" }} /></div><p className="preview-progress-text">准备进度 42% · 建议用 3 天完成</p><div className="preview-checklist large"><div><i className="done" />熟悉岗位职责和项目背景</div><div><i className="done" />复习 JVM、MySQL 索引、Redis 一致性</div><div><i />准备 2 个项目案例</div><div><i />完成一次模拟面试</div></div><PreviewButton primary onClick={() => act("保存准备计划")}>保存准备计划</PreviewButton></>;
    case "subscription": return <><div className="preview-option-list">{["每日 09:00 推送日报", "只接收重要度 ≥ 85 的资讯", "包含模型与开源项目动态", "邮件同步发送"].map((x, i) => <label key={x}><input type="checkbox" defaultChecked={i < 3} />{x}</label>)}</div><PreviewButton primary onClick={() => act("保存订阅设置")}>保存设置</PreviewButton></>;
    case "report-generate": return <><div className="preview-flow"><span className="active">采集</span><i>→</i><span>去重</span><i>→</i><span>摘要</span><i>→</i><span>推送</span></div><div className="preview-highlight">预计汇总 12 个来源、生成 5 条高价值资讯和 3 个重点主题。</div><PreviewButton primary onClick={() => act("生成日报")}>开始生成</PreviewButton></>;
    case "date-picker": return <><label className="preview-date-label">日报日期<input type="date" defaultValue="2026-09-08" /></label><Rows act={act} rows={[{ title: "2026-09-08", meta: "5 条资讯 · 最新", status: "今天" }, { title: "2026-09-07", meta: "8 条资讯 · 已归档", status: "历史" }]} /></>;
    case "entities": return <><Rows act={act} rows={[{ title: "OpenAI", meta: "公司 · 今日 8 条动态", status: "已关注" }, { title: "Anthropic", meta: "公司 · 今日 6 条动态", status: "已关注" }, { title: "Google DeepMind", meta: "公司 · 今日 5 条动态", status: "未关注" }]} /><PreviewButton primary onClick={() => act("保存关注设置")}>保存关注</PreviewButton></>;
    case "automation-logs": return <Rows act={act} rows={[{ title: "高匹配岗位提醒", meta: "2 分钟前 · 发现 3 个新岗位", status: "成功" }, { title: "GitHub Agent 项目监控", meta: "10 分钟前 · 更新 5 个项目", status: "成功" }, { title: "BOSS 消息同步", meta: "23 分钟前 · 登录状态过期", status: "失败" }]} />;
    case "automation-pending": return <><div className="preview-pending"><article><b>字节跳动 · 后端实习</b><p>示例投递材料，非外站提交。</p><PreviewButton onClick={() => act("拒绝操作")}>拒绝</PreviewButton><PreviewButton primary onClick={() => act("确认投递记录（非外站提交）")}>确认记录演示</PreviewButton></article><article><b>BOSS HR 回复</b><p>已生成回复草稿，等待发送。</p><PreviewButton onClick={() => act("查看回复草稿")}>查看草稿</PreviewButton></article></div></>;
    case "source-docs": return <Rows act={act} rows={[{ title: "招聘平台", meta: "岗位、消息与认证说明", status: "阅读" }, { title: "项目平台", meta: "GitHub Token 与采集范围", status: "阅读" }, { title: "新闻平台", meta: "RSS、Hacker News 与去重策略", status: "阅读" }]} />;
    case "source-manage": return <><div className="preview-connection"><span className="health-dot" />运行中 <small>最近采集：10 分钟前</small></div><div className="preview-option-list">{["启用增量采集", "发现异常时通知我", "保留 30 天采集日志"].map((x) => <label key={x}><input type="checkbox" defaultChecked />{x}</label>)}</div><PreviewButton primary onClick={() => act("保存数据源设置")}>保存设置</PreviewButton></>;
    case "notifications": return <Rows act={act} rows={[{ title: "Boss 直聘 · 新消息", meta: "字节跳动 · 2 轮技术面试邀请 · 10 分钟前", status: "未读" }, { title: "GitHub · 3 个项目更新", meta: "OpenHands、LangGraph 等 · 1 小时前", status: "未读" }, { title: "日报生成完成", meta: "今日 AI 日报已准备好 · 2 小时前", status: "已读" }]} />;
    case "help": return <div className="preview-faq">{["如何开始使用招聘筛选？", "为什么部分数据源需要重新认证？", "自动化任务什么时候会执行？", "如何配置日报推送？"].map((x) => <button type="button" key={x} onClick={() => act(`查看问题：${x}`)}><span>问</span>{x}<b>＋</b></button>)}</div>;
    case "profile": return <><div className="preview-profile"><div className="preview-avatar green">U</div><div><b>Demo User</b><span>demo@example.com</span></div></div><div className="preview-form-grid"><label>昵称<input defaultValue="Demo User" /></label><label>目标城市<input defaultValue="北京 / 远程" /></label><label>岗位方向<input defaultValue="后端开发 / Agent" /></label><label>通知邮箱<input defaultValue="demo@example.com" /></label></div><PreviewButton primary onClick={() => act("保存个人资料")}>保存资料</PreviewButton></>;
  }
}

export function DesignPreview({ request, onClose }: { request: PreviewRequest; onClose: () => void }) {
  const [notice, setNotice] = useState<string | null>(null);
  const meta = META[request.kind];
  const act = (message: string) => setNotice(`操作演示：${message}；未提交、未保存、未发送。`);
  return <div className="preview-overlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <aside className="preview-panel" onMouseDown={(event) => event.stopPropagation()}>
      <div className="preview-boundary">以下为演示内容，操作仅反馈，不保存/发送。</div>
      <header className="preview-header"><div><span className="preview-eyebrow">{meta.eyebrow}</span><h2>{request.title && request.kind.endsWith("detail") ? request.title : meta.title}</h2><p>{request.subtitle ?? meta.desc}</p></div><button type="button" className="preview-close" onClick={onClose} aria-label="关闭预览">×</button></header>
      {notice && <div className="preview-notice" role="status">{notice}</div>}
      <div className="preview-body"><PreviewBody request={request} act={act} /></div>
      <footer className="preview-footer"><span>本地页面设计预览</span><button type="button" onClick={onClose}>返回上一页</button></footer>
    </aside>
  </div>;
}
