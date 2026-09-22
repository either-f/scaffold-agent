import { useEffect, useState, type ReactElement } from "react";
import { AuthProvider, useAuth } from "./auth";
import { AuthModal } from "./components/AuthModal";
import { DesignPreview, type PreviewRequest } from "./components/DesignPreview";
import { DemoWorkspace } from "./components/DemoWorkspace";
import { Workspace } from "./components/Workspace";
import { SparkleIcon } from "./components/Icons";
import { Topbar, type NavItem } from "./components/Topbar";
import { AiDaily } from "./pages/AiDaily";
import { Automation } from "./pages/Automation";
import { Home } from "./pages/Home";
import { Projects } from "./pages/Projects";
import { Recruitment } from "./pages/Recruitment";
import { Sources } from "./pages/Sources";

const NAV: NavItem[] = [
  { id: "home", label: "首页", icon: "home" },
  { id: "recruitment", label: "招聘", icon: "users" },
  { id: "projects", label: "项目雷达", icon: "radar" },
  { id: "ai-daily", label: "AI日报", icon: "news" },
  { id: "automation", label: "自动化", icon: "bot" },
  { id: "sources", label: "数据源", icon: "db" },
];

const SEARCH: Record<string, string> = {
  home: "搜索岗位 / 公司 / 项目 / 技术 / 资讯",
  recruitment: "搜索岗位 / 公司 / 城市 / 技能",
  projects: "搜索项目 / GitHub 仓库 / 技术方向 / 作者",
  "ai-daily": "搜索 AI 公司 / 模型 / 资讯 / 人物 / 事件",
  automation: "搜索自动化规则 / 任务 / 触发器",
  sources: "搜索数据源 / 连接 / 平台",
};

type PageProps = { onNavigate: (id: string) => void; query: string; onPreview: (request: PreviewRequest) => void };
const PAGES: Record<string, (props: PageProps) => ReactElement> = {
  home: Home,
  recruitment: Recruitment,
  projects: Projects,
  "ai-daily": AiDaily,
  automation: Automation,
  sources: Sources,
};

function getRoute(): string {
  const h = window.location.hash.replace(/^#\/?/, "");
  return PAGES[h] ? h : "home";
}

function Shell() {
  const { user, showAuth, openAuth, logout } = useAuth();
  const [route, setRoute] = useState<string>(getRoute);
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState<PreviewRequest | null>(null);

  useEffect(() => {
    const onHash = () => {
      setRoute(getRoute());
      setPreview(null);
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function navigate(id: string) {
    window.location.hash = `/${id}`;
    setRoute(id);
    setPreview(null);
    window.scrollTo(0, 0);
  }

  const Page = PAGES[route];

  return (
    <>
      <Topbar
        items={NAV}
        active={route}
        searchPlaceholder={SEARCH[route]}
        query={query}
        onQueryChange={setQuery}
        user={user}
        onNavigate={navigate}
        onPreview={setPreview}
        onOpenAuth={openAuth}
        onLogout={logout}
      />
      <div className="legacy-boundary">旧版兼容视图：列表上的收藏、关注、阅读及投递记录真实保存；详情及配置仍为原型，不生成日报或向外站投递。</div>
      <Page onNavigate={navigate} query={query} onPreview={setPreview} />
      <button
        className="fab"
        aria-label="快捷操作"
        onClick={() => document.querySelector<HTMLInputElement>(".search-input")?.focus()}
      >
        <SparkleIcon />
      </button>
      {showAuth && <AuthModal />}
      {preview && <DesignPreview request={preview} onClose={() => setPreview(null)} />}
    </>
  );
}

export default function App() {
  if (new URLSearchParams(window.location.search).get("demo") === "1") {
    return <DemoWorkspace />;
  }
  return (
    <AuthProvider>
      {new URLSearchParams(window.location.search).get("legacy") === "1" ? <Shell /> : <Workspace />}
    </AuthProvider>
  );
}
