import { useState } from "react";
import { askCode, loginUser, registerUser, setToken } from "../api";
import { useAuth } from "../auth";
import { Icon } from "./Icons";
import { DemoDialog } from "./DemoDialog";

export function AuthModal() {
  const { closeAuth, onLoggedIn } = useAuth();
  const [tab, setTab] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [devCode, setDevCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function submitLogin() {
    setBusy(true);
    setError(null);
    try {
      const res = await loginUser({ username, password });
      if (res.code !== 200) {
        setError(res.msg);
        return;
      }
      setToken(res.data.token);
      await onLoggedIn();
      closeAuth();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function sendCode() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await askCode(email, "register");
      if (res.code !== 200) {
        setError(res.msg);
        return;
      }
      setDevCode(res.dev_code ?? null);
      setNotice("验证码已发送（开发模式直接回显，请查收下方验证码）");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function submitRegister() {
    setBusy(true);
    setError(null);
    try {
      const res = await registerUser({ username, password, code, email });
      if (res.code !== 200) {
        setError(res.msg);
        return;
      }
      // 注册成功后自动登录
      const login = await loginUser({ username, password });
      setToken(login.data.token);
      await onLoggedIn();
      closeAuth();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <DemoDialog title={tab === "login" ? "登录" : "注册"} caption="当前账户" className="workspace-auth-dialog" onClose={closeAuth}>
      <form
        className="workspace-auth-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (busy) return;
          void (tab === "login" ? submitLogin() : submitRegister());
        }}
      >
        <div className="auth-tabs">
          <button type="button" className={`auth-tab${tab === "login" ? " active" : ""}`} onClick={() => { setTab("login"); setError(null); }}>登录</button>
          <button type="button" className={`auth-tab${tab === "register" ? " active" : ""}`} onClick={() => { setTab("register"); setError(null); }}>注册</button>
        </div>

        {tab === "login" ? (
          <div className="auth-form">
            <label className="auth-label" htmlFor="login-username">用户名 / 邮箱</label>
            <input id="login-username" className="auth-input" required autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="用户名或邮箱" />
            <label className="auth-label" htmlFor="login-password">密码</label>
            <input id="login-password" className="auth-input" required autoComplete="current-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="密码" />
          </div>
        ) : (
          <div className="auth-form">
            <label className="auth-label" htmlFor="register-username">用户名</label>
            <input id="register-username" className="auth-input" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="1-10 位字母/数字/中文" />
            <label className="auth-label" htmlFor="register-email">邮箱</label>
            <div className="auth-row">
              <input id="register-email" className="auth-input" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="用于接收验证码" />
              <button type="button" className="small-btn" onClick={sendCode} disabled={busy}>发送验证码</button>
            </div>
            {devCode && <div className="auth-hint"><Icon name="check" /> 开发模式验证码：<b>{devCode}</b></div>}
            <label className="auth-label" htmlFor="register-code">验证码</label>
            <input id="register-code" className="auth-input" value={code} onChange={(e) => setCode(e.target.value)} placeholder="6 位验证码" />
            <label className="auth-label" htmlFor="register-password">密码</label>
            <input id="register-password" className="auth-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="6-20 位" />
          </div>
        )}

        {error && <div className="auth-error" role="alert">{error}</div>}
        {notice && !error && <div className="auth-hint">{notice}</div>}

        <button
          className="btn primary"
          style={{ width: "100%", marginTop: 14 }}
          type="submit"
          disabled={busy}
        >
          {busy ? "请稍候…" : tab === "login" ? "登录" : "注册并登录"}
        </button>
      </form>
    </DemoDialog>
  );
}
