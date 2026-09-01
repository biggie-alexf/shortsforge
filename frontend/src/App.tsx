import { useCallback, useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, NetworkError } from "./api";
import type { SfEvent, User } from "./types";
import { useEvents } from "./useEvents";
import { useToast } from "./toast";
import LoginPage from "./pages/Login";
import BoardPage from "./pages/Board";
import JobPage from "./pages/Job";
import SettingsPage from "./pages/Settings";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const navigate = useNavigate();
  const toast = useToast();
  // счётчик для рефетча страниц по SSE
  const [eventTick, setEventTick] = useState(0);
  const [lastEvent, setLastEvent] = useState<SfEvent | null>(null);

  useEffect(() => {
    api
      .get<{ user: User }>("/api/auth/me")
      .then((r) => setUser(r.user))
      .catch(() => {
        /* 401 → редирект в обёртке; NetworkError — грациозно молчим */
      });
  }, []);

  const onEvent = useCallback(
    (ev: SfEvent) => {
      setLastEvent(ev);
      setEventTick((t) => t + 1);
      const game = typeof ev.payload?.game === "string" ? (ev.payload.game as string) : ev.job_id ?? "";
      switch (ev.type) {
        case "gate_open":
          toast.push("gate", "ГЕЙТ ОТКРЫТ", game);
          break;
        case "render_ready":
          toast.push("ok", "РЕНДЕР ГОТОВ", game);
          break;
        case "job_status":
          toast.push("info", "СТАТУС", `${game}: ${String(ev.payload?.status ?? "")}`);
          break;
        case "chat":
          toast.push("info", "ЧАТ", game);
          break;
        case "needs_footage":
          toast.push("info", "НУЖНЫ КАДРЫ", game);
          break;
        case "error":
          toast.push("error", "ОШИБКА", `${game}: ${String(ev.payload?.detail ?? ev.payload?.error ?? "")}`);
          break;
      }
    },
    [toast]
  );

  useEvents(onEvent);

  const logout = async () => {
    try {
      await api.post("/api/auth/logout");
    } catch {
      /* игнор */
    }
    setUser(null);
    navigate("/login");
  };

  return (
    <>
      <header className="topbar">
        <NavLink to="/" className="logo">
          SHORT<em>FORGE</em>
        </NavLink>
        <nav>
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Доска
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => (isActive ? "active" : "")}>
            Настройки
          </NavLink>
        </nav>
        <div className="topbar__spacer" />
        {user && (
          <>
            <span className="topbar__user">{user.login}</span>
            <button className="btn btn--sm btn--ghost" onClick={logout}>
              Выйти
            </button>
          </>
        )}
      </header>
      <Routes>
        <Route path="/login" element={<LoginPage onLogin={setUser} />} />
        <Route path="/" element={<BoardPage eventTick={eventTick} />} />
        <Route path="/jobs/:id" element={<JobPage eventTick={eventTick} lastEvent={lastEvent} />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </>
  );
}

export function errText(e: unknown): string {
  if (e instanceof NetworkError) return "Нет соединения с сервером";
  if (e instanceof Error) return e.message;
  return "Неизвестная ошибка";
}
