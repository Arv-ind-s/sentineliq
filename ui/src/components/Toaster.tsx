import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { CheckCircle2, CircleAlert, Info, TriangleAlert, X } from "lucide-react";
import { ToastContext, type Toast, type ToastContextValue, type ToastTone } from "../lib/toast-context";

const TONE_ICONS: Record<ToastTone, typeof Info> = {
  info: Info,
  success: CheckCircle2,
  warning: TriangleAlert,
  error: CircleAlert,
};

const DEFAULT_TIMEOUT_MS = 6_000;
const MAX_VISIBLE = 4;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, number>());

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      window.clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback<ToastContextValue["push"]>((message, options) => {
    const id = nextId.current++;
    const toast: Toast = { id, message, tone: options?.tone ?? "info", detail: options?.detail };
    setToasts((current) => [...current, toast].slice(-MAX_VISIBLE));
    // Errors stay until dismissed; a failure that vanishes on its own is a
    // failure the operator never saw.
    const timeout = options?.timeoutMs ?? (toast.tone === "error" ? 0 : DEFAULT_TIMEOUT_MS);
    if (timeout > 0) timers.current.set(id, window.setTimeout(() => dismiss(id), timeout));
    return id;
  }, [dismiss]);

  useEffect(() => {
    const pending = timers.current;
    return () => { pending.forEach((timer) => window.clearTimeout(timer)); pending.clear(); };
  }, []);

  const value = useMemo<ToastContextValue>(() => ({ toasts, push, dismiss }), [dismiss, push, toasts]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

function ToastViewport({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  const reducedMotion = useReducedMotion() ?? false;
  return (
    <div className="toast-viewport" role="region" aria-label="Notifications">
      <AnimatePresence initial={false}>
        {toasts.map((toast) => {
          const Icon = TONE_ICONS[toast.tone];
          return (
            <motion.div
              key={toast.id}
              className={`toast toast--${toast.tone}`}
              role={toast.tone === "error" ? "alert" : "status"}
              layout={!reducedMotion}
              initial={reducedMotion ? false : { opacity: 0, y: 12, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.98 }}
              transition={{ duration: reducedMotion ? 0 : 0.18, ease: [0.2, 0, 0.13, 1] }}
            >
              <Icon size={16} aria-hidden="true" className="toast-icon" />
              <div className="toast-body">
                <span className="toast-message">{toast.message}</span>
                {toast.detail && <span className="toast-detail">{toast.detail}</span>}
              </div>
              <button className="toast-close" type="button" onClick={() => onDismiss(toast.id)} aria-label="Dismiss notification">
                <X size={14} aria-hidden="true" />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
