import { useEffect, useRef, useState } from "react";
import type { UserInfo } from "../types";
import type { PreviewRequest } from "./DesignPreview";
import { Avatar, BrandLogo, Chevron, Icon, type IconName } from "./Icons";

export interface NavItem {
  id: string;
  label: string;
  icon: IconName;
}

export function Topbar({
  items,
  active,
  searchPlaceholder,
  query,
  onQueryChange,
  user,
  onNavigate,
  onPreview,
  onOpenAuth,
  onLogout,
}: {
  items: NavItem[];
  active: string;
  searchPlaceholder: string;
  query: string;
  onQueryChange: (query: string) => void;
  user: UserInfo | null;
  onNavigate: (id: string) => void;
  onPreview: (request: PreviewRequest) => void;
  onOpenAuth: () => void;
  onLogout: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const searchInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchInput.current?.focus();
      }
    };
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, []);

  return (
    <header className="topbar">
      <div className="top-inner">
        <div className="brand">
          <BrandLogo />
          <span>Scaffold Platform</span>
        </div>
        <nav className="nav">
          {items.map((item) => (
            <a
              key={item.id}
              href={`#/${item.id}`}
              className={item.id === active ? "active" : ""}
              onClick={(e) => {
                e.preventDefault();
                onNavigate(item.id);
              }}
            >
              <Icon name={item.icon} className="nav-icon" />
              {item.label}
            </a>
          ))}
        </nav>
        <div className="header-spacer" />
        <a className="demo-entry" href="?workspace=1#/today">个人工作台</a>
        <a className="demo-entry" href="?demo=1#/today">体验演示</a>
        <div className="search">
          <Icon name="search" />
          <input
            ref={searchInput}
            className="search-input"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder={searchPlaceholder}
            aria-label="搜索"
          />
          <span className="shortcut">{navigator.platform.includes("Mac") ? "⌘K" : "Ctrl K"}</span>
        </div>
        <button className="top-icon" aria-label="通知预览" title="通知预览" onClick={() => onPreview({ kind: "notifications" })}>
          <Icon name="bell" />
        </button>
        <button className="top-icon" aria-label="帮助" onClick={() => onPreview({ kind: "help" })}>
          <Icon name="help" />
        </button>
        {user === null ? (
          <button className="small-btn primary" style={{ marginLeft: 14 }} onClick={onOpenAuth}>
            登录 / 注册
          </button>
        ) : (
          <div className="user" onClick={() => setMenuOpen((v) => !v)}>
            <Avatar />
            <span>{user.nickname || user.username}</span>
            <Chevron />
            {menuOpen && (
              <div className="user-menu" onClick={(e) => e.stopPropagation()}>
                <button onClick={() => { setMenuOpen(false); onPreview({ kind: "profile" }); }}>👤 {user.username}</button>
                <button
                  className="logout"
                  onClick={() => {
                    setMenuOpen(false);
                    onLogout();
                  }}
                >
                  ⏻ 退出登录
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
