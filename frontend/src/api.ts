import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import type { ContentItem, ModuleInfo, UserInfo } from "./types";

// ponytail: 单常量够用，没有多环境切换需求前不上 .env 加载框架，VITE_API_BASE 走 vite 内建 import.meta.env。
export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8010";

// ---- token 管理（localStorage + 变更订阅） ---------------------------------
const TOKEN_KEY = "auth_token";
let tokenVersion = 0;
const tokenListeners = new Set<() => void>();

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
  tokenVersion += 1;
  tokenListeners.forEach((l) => l());
}

function subscribeToken(listener: () => void): () => void {
  tokenListeners.add(listener);
  return () => {
    tokenListeners.delete(listener);
  };
}

window.addEventListener("storage", (event) => {
  if (event.key === TOKEN_KEY || event.key === null) {
    tokenVersion += 1;
    tokenListeners.forEach((listener) => listener());
  }
});

export function useTokenVersion() {
  return useSyncExternalStore(subscribeToken, () => tokenVersion);
}

// ---- 请求基础 --------------------------------------------------------------
function authHeaders(): Record<string, string> {
  const token = getToken();
  return token
    ? { "Content-Type": "application/json", Authorization: `Bearer ${token}` }
    : { "Content-Type": "application/json" };
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const version = tokenVersion;
  const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders(), ...init, signal: init?.signal ?? AbortSignal.timeout(30000) });
  if (version !== tokenVersion) throw new DOMException("账户已切换，请重新操作", "AbortError");
  if (res.status === 401) throw new AuthRequiredError();
  if (res.status === 403) throw new Error("权限不足（403）：此操作需要所有者权限或目标不属于当前账户。");
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    const detail = typeof payload?.detail === "string" ? payload.detail : "请求未完成，请检查输入或稍后重试";
    throw new Error(`${detail}（${res.status}）`);
  }
  const data = await res.json() as T;
  if (version !== tokenVersion) throw new DOMException("账户已切换，请重新操作", "AbortError");
  return data;
}

/** 未登录错误：写操作收到 401 时抛出，页面捕获后弹登录框。 */
export class AuthRequiredError extends Error {
  constructor() {
    super("请先登录");
    this.name = "AuthRequiredError";
  }
}

export function postApi<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined, signal });
}

export function putApi<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body) });
}

// ---- 页面级数据加载 hook ----------------------------------------------------
/**
 * GET 一个平台端点，返回 {data, error, setData}。
 * path 变化（筛选条件）或 token 变化（登录/登出）都会自动重新请求；
 * setData 供写操作后本地更新（不用整页重新拉取）。
 */
export function useApi<T>(path: string | null): {
  data: T | null;
  error: string | null;
  setData: (updater: (prev: T) => T) => void;
  refresh: () => void;
} {
  const version = useTokenVersion();
  const [revision, setRevision] = useState(0);
  const identity = `${version}:${path}:${revision}`;
  const [result, setResult] = useState<{ identity: string; data: T | null; error: string | null }>({ identity: "", data: null, error: null });
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    request<T>(path, { signal: controller.signal })
      .then((d) => {
        if (!controller.signal.aborted) setResult({ identity, data: d, error: null });
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted) setResult({ identity, data: null, error: e instanceof Error ? e.message : String(e) });
      });
    return () => controller.abort();
  }, [path, identity]);

  function setData(updater: (prev: T) => T) {
    setResult((prev) => prev.identity !== identity || prev.data === null ? prev : { ...prev, data: updater(prev.data) });
  }

  return { data: result.identity === identity ? result.data : null, error: result.identity === identity ? result.error : null, setData, refresh };
}

// ---- auth API（注册登录） ---------------------------------------------------
export interface AuthEnvelope<T> {
  code: number;
  msg: string;
  data: T;
}

export const askCode = (email: string, type: string) =>
  request<AuthEnvelope<string> & { dev_code?: string }>(
    `/public/ask-code?email=${encodeURIComponent(email)}&type=${encodeURIComponent(type)}`,
  );

export const registerUser = (body: { username: string; password: string; code: string; email: string }) =>
  request<AuthEnvelope<null>>("/user/register", { method: "POST", body: JSON.stringify(body) });

export const loginUser = (body: { username: string; password: string }) =>
  request<AuthEnvelope<{ token: string; expire: number } & UserInfo>>("/user/login", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const fetchMe = () => request<AuthEnvelope<UserInfo>>("/user/auth/info");

export const logoutUser = () => postApi<AuthEnvelope<null>>("/user/logout");

// ---- 旧内容 API（内容列表/审批，暂未被平台页面使用） -----------------------
export const listModules = () => request<ModuleInfo[]>("/api/modules");

export const listItems = (module: string, status: string) => {
  const params = new URLSearchParams({ module });
  if (status) params.set("status", status);
  return request<ContentItem[]>(`/api/items?${params}`);
};

export const getItem = (id: number) => request<ContentItem>(`/api/items/${id}`);

export const approveItem = (id: number) =>
  request<ContentItem>(`/api/items/${id}/approve`, { method: "POST", body: "{}" });

export const rejectItem = (id: number) =>
  request<ContentItem>(`/api/items/${id}/reject`, { method: "POST", body: "{}" });
