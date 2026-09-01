import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, NetworkError } from "../api";
import { errText } from "../App";
import { Empty, Skeleton } from "../components";
import { fmtTime } from "../labels";
import { useToast } from "../toast";
import type { Providers, SettingRow, User } from "../types";

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingRow[] | null>(null);
  const [providers, setProviders] = useState<Providers | null>(null);
  const [users, setUsers] = useState<User[] | null>(null);
  const [offline, setOffline] = useState(false);
  const toast = useToast();

  const load = useCallback(() => {
    api
      .get<SettingRow[]>("/api/settings")
      .then((s) => {
        setSettings(s);
        setOffline(false);
      })
      .catch((e) => {
        if (e instanceof NetworkError) setOffline(true);
      });
    api.get<Providers>("/api/settings/providers").then(setProviders).catch(() => {});
    api.get<User[]>("/api/users").then(setUsers).catch(() => {});
  }, []);

  useEffect(load, [load]);

  return (
    <main className="page">
      <div className="page-head">
        <h1>Настройки</h1>
      </div>

      {offline && <Empty>Нет соединения с сервером.</Empty>}

      <section className="panel">
        <div className="panel__head">
          <h2>ПРОВАЙДЕРЫ СЕЙЧАС</h2>
        </div>
        <div className="panel__body">
          {providers ? (
            <div className="providers">
              <ProviderBadge name="LLM" mode={providers.llm} />
              <ProviderBadge name="TTS" mode={providers.tts} />
              <ProviderBadge name="YouTube" mode={providers.youtube} />
            </div>
          ) : (
            <Skeleton height={24} width="50%" />
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel__head">
          <h2>КЛЮЧИ И ПАРАМЕТРЫ</h2>
        </div>
        <div className="panel__body">
          {settings === null && !offline && <Skeleton height={120} />}
          {settings !== null && settings.length === 0 && <Empty>Настроек пока нет.</Empty>}
          {settings?.map((s) => (
            <SettingRowView
              key={s.key}
              row={s}
              onSaved={() => {
                toast.push("ok", "СОХРАНЕНО", s.key);
                load();
              }}
            />
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel__head">
          <h2>ПОЛЬЗОВАТЕЛИ</h2>
        </div>
        <div className="panel__body">
          <UsersSection users={users} reload={load} />
        </div>
      </section>
    </main>
  );
}

function ProviderBadge({ name, mode }: { name: string; mode: "real" | "mock" }) {
  return (
    <div className="providers__item">
      <span>{name}</span>
      <span className={`badge ${mode === "real" ? "badge--ok" : "badge--warn"}`}>
        {mode === "real" ? "REAL" : "MOCK"}
      </span>
    </div>
  );
}

function SettingRowView({ row, onSaved }: { row: SettingRow; onSaved: () => void }) {
  const [value, setValue] = useState(row.secret ? "" : row.value_masked);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  // не-секретные значения обновляем при рефетче
  useEffect(() => {
    if (!row.secret) setValue(row.value_masked);
  }, [row.secret, row.value_masked]);

  const save = async () => {
    setBusy(true);
    try {
      await api.put(`/api/settings/${encodeURIComponent(row.key)}`, { value });
      if (row.secret) setValue("");
      onSaved();
    } catch (e) {
      toast.push("error", "ОШИБКА", errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-row">
      <div className="settings-row__key">
        {row.key}
        <small>{row.updated_at ? `обновлено ${fmtTime(row.updated_at)}` : "не задано"}</small>
      </div>
      <input
        type={row.secret ? "password" : "text"}
        placeholder={row.secret ? row.value_masked || "не задан" : ""}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        autoComplete="off"
      />
      <button className="btn btn--sm" onClick={save} disabled={busy || (row.secret && !value)}>
        {busy ? "…" : "Сохранить"}
      </button>
    </div>
  );
}

function UsersSection({ users, reload }: { users: User[] | null; reload: () => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/api/users", { login: login.trim(), password });
      setLogin("");
      setPassword("");
      toast.push("ok", "ПОЛЬЗОВАТЕЛЬ ДОБАВЛЕН", login.trim());
      reload();
    } catch (err) {
      toast.push("error", "ОШИБКА", errText(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (u: User) => {
    if (!window.confirm(`Удалить пользователя ${u.login}?`)) return;
    try {
      await api.del(`/api/users/${u.id}`);
      toast.push("ok", "УДАЛЁН", u.login);
      reload();
    } catch (err) {
      toast.push("error", "ОШИБКА", errText(err));
    }
  };

  return (
    <>
      {users === null && <Skeleton height={60} />}
      {users?.map((u) => (
        <div className="users-row" key={u.id}>
          <span>{u.login}</span>
          <button className="btn btn--sm btn--ghost" onClick={() => remove(u)}>
            Удалить
          </button>
        </div>
      ))}
      {users !== null && users.length === 0 && <Empty>Пользователей нет.</Empty>}
      <form onSubmit={add} style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <input
          type="text"
          placeholder="Логин"
          value={login}
          onChange={(e) => setLogin(e.target.value)}
          autoComplete="off"
        />
        <input
          type="password"
          placeholder="Пароль"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
        />
        <button className="btn" disabled={busy || !login.trim() || !password}>
          Добавить
        </button>
      </form>
    </>
  );
}
