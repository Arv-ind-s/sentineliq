import { createContext, useContext } from "react";

export type ToastTone = "info" | "success" | "warning" | "error";

export type Toast = {
  id: number;
  tone: ToastTone;
  message: string;
  detail?: string;
};

export type ToastContextValue = {
  toasts: Toast[];
  push: (message: string, options?: { tone?: ToastTone; detail?: string; timeoutMs?: number }) => number;
  dismiss: (id: number) => void;
};

export const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}
