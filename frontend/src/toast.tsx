import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

export interface Toast {
  id: number;
  kind: "info" | "gate" | "ok" | "error";
  title: string;
  text: string;
}

interface ToastCtx {
  push: (kind: Toast["kind"], title: string, text: string) => void;
}

const Ctx = createContext<ToastCtx>({ push: () => {} });

export function useToast() {
  return useContext(Ctx);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);

  const push = useCallback((kind: Toast["kind"], title: string, text: string) => {
    const id = ++seq.current;
    setToasts((t) => [...t.slice(-4), { id, kind, title, text }]);
    window.setTimeout(() => {
      setToasts((t) => t.filter((x) => x.id !== id));
    }, 5000);
  }, []);

  return (
    <Ctx.Provider value={{ push }}>
      {children}
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast--${t.kind}`}>
            <span className="toast__title">{t.title}</span>
            <span className="toast__text">{t.text}</span>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
