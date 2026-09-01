import type { ReactNode } from "react";
import type { JobStatus } from "./types";
import { STAGES, stageIndex } from "./labels";

/** Таймлайн из 6 точек: сценарий→охота→клипы→озвучка→черновик→мастер */
export function StatusTimeline({ status, labeled }: { status: JobStatus; labeled?: boolean }) {
  const idx = stageIndex(status);
  const isGate = status.startsWith("gate_");
  return (
    <div className={`timeline${labeled ? " timeline--labeled" : ""}`} title={STAGES.join(" → ")}>
      {STAGES.map((label, i) => {
        let cls = "timeline__dot";
        if (status === "done" || i < idx) cls += " timeline__dot--done";
        else if (i === idx) cls += isGate ? " timeline__dot--gate" : " timeline__dot--current";
        return (
          <div className="timeline__stage" key={label}>
            <span className={cls} title={label} />
            {labeled && <span className="timeline__label">{label}</span>}
            {i < STAGES.length - 1 && (
              <span
                className={`timeline__link${
                  status === "done" || i < idx ? " timeline__link--done" : ""
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

export function Skeleton({ height, width }: { height: number; width?: string }) {
  return <div className="skeleton" style={{ height, width: width ?? "100%" }} />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Spinner() {
  return <span className="spinner" aria-label="загрузка" />;
}
