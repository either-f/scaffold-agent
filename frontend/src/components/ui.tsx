import type { ReactNode } from "react";

/** 页头：标题 + 描述 + 右侧操作按钮 */
export function PageHead({ title, desc, actions }: { title: string; desc: string; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1 className="page-title">{title}</h1>
        <div className="page-desc">{desc}</div>
      </div>
      {actions && <div className="head-actions">{actions}</div>}
    </div>
  );
}

/** 统计卡 */
export function SummaryCard({ label, num, note, mark }: { label: string; num: string; note: string; mark: string }) {
  return (
    <div className="summary-card">
      <div>
        <div className="summary-label">{label}</div>
        <div className="summary-num">{num}</div>
        <div className="summary-note">{note}</div>
      </div>
      <div className="summary-mark">{mark}</div>
    </div>
  );
}

/** 次级导航（tab 条） */
export function Subnav({
  tabs,
  active,
  onSelect,
  right,
}: {
  tabs: string[];
  active: string;
  onSelect: (tab: string) => void;
  right?: ReactNode;
}) {
  return (
    <div className="subnav">
      {tabs.map((tab) => (
        <button
          key={tab}
          className={`subtab${tab === active ? " active" : ""}`}
          onClick={() => onSelect(tab)}
        >
          {tab}
        </button>
      ))}
      <span className="push" />
      {right}
    </div>
  );
}

/** 右侧边栏卡片 */
export function SideBox({ title, extra, children }: { title: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <div className="side-box">
      <div className="row-between">
        <div className="section-title">{title}</div>
        {extra && <span className="mini-link">{extra}</span>}
      </div>
      {children}
    </div>
  );
}

/** 筛选面板里的一组勾选项 */
export function FilterGroup({
  name,
  options,
  active,
  onToggle,
}: {
  name: string;
  options: { label: string; count?: string }[];
  active: string[];
  onToggle: (label: string) => void;
}) {
  return (
    <div className="filter-group">
      <div className="filter-name">{name}</div>
      {options.map((opt) => (
        <button
          type="button"
          key={opt.label}
          className={`check-row${active.includes(opt.label) ? " active" : ""}`}
          aria-pressed={active.includes(opt.label)}
          onClick={() => onToggle(opt.label)}
        >
          <span className="check-box" />
          {opt.label}
          {opt.count && <span style={{ marginLeft: "auto" }}>{opt.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** 数据加载中/失败时的占位（复用 scroll-note 样式，居中提示）。 */
export function PageStatus({ data, error }: { data: unknown; error: string | null }) {
  if (data === null && !error) return <div className="scroll-note">加载中…</div>;
  if (data === null && error) return <div className="scroll-note" role="alert">{error.includes("登录") || error.includes("403") ? `权限提示：${error}` : `加载失败：${error}`}</div>;
  return null;
}

export function matchesQuery(query: string, ...values: unknown[]): boolean {
  const needle = query.trim().toLocaleLowerCase();
  return !needle || values.some((value) => String(value ?? "").toLocaleLowerCase().includes(needle));
}
