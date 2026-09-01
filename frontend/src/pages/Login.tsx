import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { errText } from "../App";
import type { User } from "../types";

export default function LoginPage({ onLogin }: { onLogin: (u: User) => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const r = await api.post<{ user: User }>("/api/auth/login", { login, password });
      onLogin(r.user);
      navigate("/");
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <span className="logo">
          SHORT<em>FORGE</em>
        </span>
        <label className="field">
          <span>Логин</span>
          <input
            type="text"
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            autoFocus
            autoComplete="username"
          />
        </label>
        <label className="field">
          <span>Пароль</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        <button className="btn btn--accent" style={{ width: "100%" }} disabled={busy || !login || !password}>
          {busy ? "Вход…" : "Войти"}
        </button>
        {error && <div className="error-inline">{error}</div>}
      </form>
    </div>
  );
}
