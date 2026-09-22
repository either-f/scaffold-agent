import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { AuthRequiredError, fetchMe, getToken, logoutUser, setToken, useTokenVersion } from "./api";
import type { UserInfo } from "./types";

interface AuthContextValue {
  user: UserInfo | null;
  showAuth: boolean;
  openAuth: () => void;
  closeAuth: () => void;
  /** 登录/注册成功后刷新用户信息 */
  onLoggedIn: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<{ token: string | null; user: UserInfo | null }>({ token: null, user: null });
  const [showAuth, setShowAuth] = useState(false);
  const tokenVersion = useTokenVersion();
  const user = account.token === getToken() ? account.user : null;

  // 启动时若本地有 token，则拉取当前用户
  useEffect(() => {
    const token = getToken();
    setAccount({ token, user: null });
    if (!token) return;
    let cancelled = false;
    fetchMe()
      .then((r) => {
        if (!cancelled && r.code === 200) setAccount({ token, user: r.data });
      })
      .catch((error: unknown) => {
        if (!cancelled && error instanceof AuthRequiredError) setToken(null);
      });
    return () => {
      cancelled = true;
    };
  }, [tokenVersion]);

  async function onLoggedIn() {
    const token = getToken();
    try {
      const r = await fetchMe();
      if (r.code === 200) setAccount({ token, user: r.data });
    } catch {
      // 忽略：登录本身已成功，用户信息下次再拉
    }
  }

  async function logout() {
    const pending = logoutUser();
    setToken(null);
    setAccount({ token: null, user: null });
    try {
      await pending;
    } catch {
      // 忽略：即使后端登出失败，本地也清 token
    }
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        showAuth,
        openAuth: () => setShowAuth(true),
        closeAuth: () => setShowAuth(false),
        onLoggedIn,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

/**
 * 写操作辅助：未登录时自动弹登录框；收到 401（token 失效）同样弹框。
 * 返回的 run() 在失败时 resolve null，页面据此决定是否更新本地状态。
 */
export function useAuthAction() {
  const { user, openAuth } = useAuth();

  async function run<T>(fn: () => Promise<T>): Promise<T | null> {
    if (!user) {
      openAuth();
      return null;
    }
    try {
      return await fn();
    } catch (e) {
      if (e instanceof AuthRequiredError) openAuth();
      return null;
    }
  }

  return { user, run };
}
