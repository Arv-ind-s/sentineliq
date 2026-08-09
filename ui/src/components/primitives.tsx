import { Circle, CircleSlash2, Octagon, Square, Triangle } from "lucide-react";
import type { ReactNode } from "react";
import type { Band } from "../types/api";

type Status = Band;

const statusIcons = {
  LOW: Circle,
  MEDIUM: Triangle,
  HIGH: Square,
  CRITICAL: Octagon,
  NODATA: CircleSlash2,
} satisfies Record<Status, typeof Circle>;

export function Skeleton({ width = "100%", height = "16px", className = "" }: { width?: string; height?: string; className?: string }) {
  return <span className={`skeleton ${className}`} style={{ width, height }} aria-hidden="true" />;
}

export function Panel({ children, className = "", title, action }: { children: ReactNode; className?: string; title?: string; action?: ReactNode }) {
  return (
    <section className={`panel ${className}`}>
      {(title || action) && <div className="panel-header">{title && <h2>{title}</h2>}{action}</div>}
      {children}
    </section>
  );
}

export function StatusPill({ status, label }: { status: Status; label?: string }) {
  const Icon = statusIcons[status];
  return <span className={`status-pill status-pill--${status.toLowerCase()}`}><Icon size={14} strokeWidth={2} aria-hidden="true" /><span>{label ?? status}</span></span>;
}

export function MonoNumber({ children }: { children: ReactNode }) {
  return <span className="mono-number">{children}</span>;
}
