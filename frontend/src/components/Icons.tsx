import type { CSSProperties, ReactNode } from "react";

/**
 * SVG 图标系统 — 转录自 scaffold-platform-redesign/*.html 的 <symbol> 定义。
 * 统一 24x24 viewBox，颜色跟随 currentColor（通过 CSS 的 color 控制）。
 */
const ICONS: Record<string, ReactNode> = {
  home: (
    <>
      <path d="M3.8 10.8 12 4l8.2 6.8v8.5a1.7 1.7 0 0 1-1.7 1.7h-13a1.7 1.7 0 0 1-1.7-1.7z" fill="currentColor" />
      <path d="M9.2 21v-6.8h5.6V21" fill="#fff" opacity=".95" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8" r="3.2" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M3.5 19c.6-4 2.6-6 5.5-6s4.9 2 5.5 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="17.3" cy="9.2" r="2.4" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M15.4 14.1c2.8-.3 4.7 1.3 5.2 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </>
  ),
  radar: (
    <>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <circle cx="12" cy="12" r="4.8" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path d="M12 12 18.5 7.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="12" cy="12" r="1.6" fill="currentColor" />
    </>
  ),
  news: (
    <>
      <rect x="4" y="3" width="16" height="18" rx="2" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path d="M8 8h8M8 12h8M8 16h5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </>
  ),
  bot: (
    <>
      <rect x="4" y="7" width="16" height="12" rx="4" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 3v4M8.5 12h.01M15.5 12h.01M9 16h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="12" cy="3" r="1.4" fill="currentColor" />
    </>
  ),
  db: (
    <>
      <ellipse cx="12" cy="5.5" rx="7.5" ry="3.2" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path d="M4.5 5.5v6c0 1.8 3.4 3.2 7.5 3.2s7.5-1.4 7.5-3.2v-6M4.5 11.5v6c0 1.8 3.4 3.2 7.5 3.2s7.5-1.4 7.5-3.2v-6" fill="none" stroke="currentColor" strokeWidth="1.7" />
    </>
  ),
  search: (
    <>
      <circle cx="10.7" cy="10.7" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="m15.3 15.3 4.4 4.4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </>
  ),
  bell: (
    <>
      <path d="M6.5 9.5c0-3.5 2.1-5.5 5.5-5.5s5.5 2 5.5 5.5v4.1l1.8 2.6H4.7l1.8-2.6z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M9.6 19c.5 1.1 1.3 1.7 2.4 1.7s1.9-.6 2.4-1.7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </>
  ),
  help: (
    <>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M9.8 9.2c.2-1.6 1.3-2.6 3-2.6 1.8 0 3 1 3 2.6 0 1.4-.8 2.1-2.1 2.9-1.1.7-1.5 1.2-1.5 2.2M12.2 17.6h.01" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </>
  ),
  refresh: (
    <>
      <path d="M19 8a8 8 0 1 0 1 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M19 4v4h-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),
  bookmark: <path d="M7 4.5h10v15L12 16l-5 3.5z" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path d="M12 7v5l3.2 2" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </>
  ),
  briefcase: (
    <>
      <rect x="3.5" y="7" width="17" height="12.5" rx="2.5" fill="currentColor" />
      <rect x="8.4" y="4.1" width="7.2" height="4" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M3.5 11.2h17" stroke="#fff" strokeWidth="1.5" opacity=".9" />
      <rect x="10.1" y="10.5" width="3.8" height="2.2" rx=".7" fill="#fff" />
    </>
  ),
  fire: (
    <>
      <path d="M13.8 2.8c.6 3.1-1.6 4.3-2.7 6.4-.8-1.1-1.1-2.5-.6-4.1C6.6 7.5 4.8 10 4.8 13.4A7.2 7.2 0 0 0 12 20.7a7.2 7.2 0 0 0 7.2-7.3c0-4.1-2.4-7.4-5.4-10.6Z" fill="currentColor" />
      <path d="M12 11.1c1.9 2.1 2.4 3.7 1.6 5.1-.5.9-1.1 1.4-1.8 1.7-1.5-.5-2.5-1.6-2.5-3.1 0-1.4.8-2.7 2.7-3.7Z" fill="#fff" opacity=".82" />
    </>
  ),
  folder: (
    <path d="M3.5 7.2c0-1.3 1-2.2 2.3-2.2h4l1.8 2H18c1.4 0 2.5 1.1 2.5 2.5v7.8c0 1.4-1.1 2.5-2.5 2.5H6c-1.4 0-2.5-1.1-2.5-2.5z" fill="currentColor" />
  ),
  filter: (
    <>
      <path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <circle cx="15" cy="6" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="9" cy="12" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="17" cy="18" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </>
  ),
  more: (
    <>
      <circle cx="5" cy="12" r="1.5" fill="currentColor" />
      <circle cx="12" cy="12" r="1.5" fill="currentColor" />
      <circle cx="19" cy="12" r="1.5" fill="currentColor" />
    </>
  ),
  task: (
    <>
      <rect x="5" y="4.2" width="14" height="16" rx="2.2" fill="currentColor" />
      <path d="M8.5 9.2h7M8.5 13h7M8.5 16.8h4" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
    </>
  ),
  check: <path d="m5 12.8 4.2 4.2L19 7.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />,
  link: (
    <path d="M9.8 14.2 14.2 9.8M7.2 16.8l-1.1 1.1a3.2 3.2 0 0 1-4.5-4.5l4.1-4.1a3.2 3.2 0 0 1 4.5 0M16.8 7.2l1.1-1.1a3.2 3.2 0 1 1 4.5 4.5l-4.1 4.1a3.2 3.2 0 0 1-4.5 0" transform="translate(-.5 -.5)" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
  ),
};

export type IconName = keyof typeof ICONS;

export function Icon({ name, className, style }: { name: IconName; className?: string; style?: CSSProperties }) {
  return (
    <svg className={className} style={style} viewBox="0 0 24 24" aria-hidden="true">
      {ICONS[name]}
    </svg>
  );
}

/** FAB 用的星点图标 */
export function SparkleIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      <path d="m12 2 1.5 5 5 1.5-5 1.5-1.5 5-1.5-5-5-1.5 5-1.5zM18.5 14l.8 2.7 2.7.8-2.7.8-.8 2.7-.8-2.7-2.7-.8 2.7-.8z" fill="currentColor" />
    </svg>
  );
}

/** 顶栏品牌 Logo（渐变六边形 S 形） */
export function BrandLogo() {
  return (
    <svg className="brand-logo" viewBox="0 0 32 32" aria-label="Scaffold logo">
      <defs>
        <linearGradient id="sg" x1="5" y1="3" x2="27" y2="29" gradientUnits="userSpaceOnUse">
          <stop stopColor="#22c99d" />
          <stop offset="1" stopColor="#08a77e" />
        </linearGradient>
      </defs>
      <path d="M15.9 2.5 26 8.3v6.1l-4.8 2.8-5.3-3.1 4.7-2.7-4.7-2.7-5.2 3v6.1L5.9 15V8.3l10-5.8Z" fill="url(#sg)" />
      <path d="m16.1 29.5-10.2-5.8v-6.1l4.8-2.8 5.4 3.1-4.8 2.7 4.8 2.7 5.2-3v-6.1l4.8 2.8v6.7l-10 5.8Z" fill="url(#sg)" />
    </svg>
  );
}

/** 顶栏用户头像 */
export function Avatar() {
  return (
    <div className="avatar">
      <svg viewBox="0 0 36 36">
        <defs>
          <linearGradient id="avbg" x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#c7dfe8" />
            <stop offset="1" stopColor="#eff5f8" />
          </linearGradient>
        </defs>
        <rect width="36" height="36" fill="url(#avbg)" />
        <circle cx="18" cy="14" r="7" fill="#f1c7aa" />
        <path d="M10 13c1-7 4-9 8-9 5 0 8 3 8 9-3-3-6-5-10-5-2 2-4 3-6 5Z" fill="#182532" />
        <path d="M7 36c1-9 5-13 11-13 7 0 11 4 12 13Z" fill="#273b4a" />
        <circle cx="16" cy="14" r=".8" fill="#2f3640" />
        <circle cx="21" cy="14" r=".8" fill="#2f3640" />
        <path d="M16 18c1.3.8 2.7.8 4 0" fill="none" stroke="#b76f5b" strokeWidth="1" strokeLinecap="round" />
      </svg>
    </div>
  );
}

/** 下拉小箭头 */
export function Chevron() {
  return (
    <svg className="chev" viewBox="0 0 24 24" aria-hidden="true">
      <path d="m7 9 5 5 5-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

/** 首页聚合流里的平台 Source Logo（多色品牌标） */
export function SourceLogo({ type }: { type: "mi" | "git" | "openai" }) {
  if (type === "mi") {
    return (
      <div className="source-logo mi">
        <svg viewBox="0 0 64 64">
          <rect x="7" y="12" width="50" height="40" rx="9" fill="#fff" />
          <path d="M17 24h17c8 0 12 4 12 12v10h-8V35c0-3-1-4-4-4h-9v15h-8z" fill="#ff6b00" />
          <rect x="48" y="24" width="7" height="22" rx="2" fill="#ff6b00" />
        </svg>
      </div>
    );
  }
  if (type === "git") {
    return (
      <div className="source-logo git">
        <svg viewBox="0 0 64 64">
          <circle cx="32" cy="32" r="23" fill="#fff" />
          <path d="M32 15c-10 0-18 7.5-18 17 0 7.5 5 13.8 12 16 .8.2 1-.3 1-.7v-3c-5 .9-6-2-6-2-.8-2-2-2.5-2-2.5-1.6-1 .1-1 .1-1 1.8.1 2.7 1.8 2.7 1.8 1.6 2.6 4.3 1.9 5.3 1.4.2-1.1.6-1.9 1.2-2.4-4-.5-8.3-1.9-8.3-8.4 0-1.9.7-3.4 1.8-4.6-.2-.5-.8-2.3.2-4.7 0 0 1.5-.5 4.9 1.8a17.7 17.7 0 0 1 9 0c3.4-2.3 4.9-1.8 4.9-1.8 1 2.4.4 4.2.2 4.7 1.1 1.2 1.8 2.7 1.8 4.6 0 6.5-4.3 7.9-8.3 8.4.7.5 1.3 1.6 1.3 3.2v4.7c0 .4.2.9 1 .7 7-2.2 12-8.5 12-16 0-9.5-8-17-18-17Z" fill="#17191e" />
        </svg>
      </div>
    );
  }
  return (
    <div className="source-logo openai">
      <svg viewBox="0 0 64 64">
        <g fill="none" stroke="#fff" strokeWidth="4.5" strokeLinecap="round">
          <path d="M32 15c6-5 15-.4 15 7.3 7 .6 10.5 9.5 5.2 14.8 3.2 7.2-4.4 14.7-11.3 11.2-4.3 6.4-14.8 4.7-16.1-2.8-7.7.3-11.6-8.2-6.4-13.7-4.1-6.8 2.3-14.3 9.7-12.2C28.7 17.8 30 16.2 32 15Z" />
          <path d="m26 19 12 7v14l-12 7M20 32l12-7 12 7M26 46V32l12-7M38 39 26 32l12-7" />
        </g>
      </svg>
    </div>
  );
}
