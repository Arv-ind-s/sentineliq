import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { LiveStreamContext, type StreamConnectionState } from "./stream-context";

type StreamPayload = Record<string, unknown>;

const MAX_TELEMETRY_POINTS = 60;
const POLLING_INTERVAL_MS = 10_000;
const POLLING_QUERY_KEYS = [["machines"], ["alerts"], ["health"], ["live-telemetry"]] as const;

function parsePayload(event: MessageEvent<string>): StreamPayload | null {
  try {
    const payload: unknown = JSON.parse(event.data);
    return payload && typeof payload === "object" ? payload as StreamPayload : null;
  } catch {
    return null;
  }
}

function rememberTelemetry(queryClient: ReturnType<typeof useQueryClient>, payload: StreamPayload) {
  const machineId = typeof payload.machine_id === "string" ? payload.machine_id : null;
  const channels = payload.channels && typeof payload.channels === "object" ? payload.channels as Record<string, unknown> : null;
  const value = channels?.vibration_rms_mm_s;
  if (!machineId || typeof value !== "number" || !Number.isFinite(value)) return;
  queryClient.setQueryData<number[]>(["live-telemetry", machineId], (current = []) => [...current, value].slice(-MAX_TELEMETRY_POINTS));
}

function streamUrl(lastEventId: string | null) {
  return lastEventId ? `/stream/events?last_event_id=${encodeURIComponent(lastEventId)}` : "/stream/events";
}

export function LiveStreamProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<StreamConnectionState>("connecting");
  const [lastEventId, setLastEventId] = useState<string | null>(null);
  const stateRef = useRef<StreamConnectionState>("connecting");
  const lastEventIdRef = useRef<string | null>(null);
  const reconnectAttempt = useRef(0);
  const reconnectTimer = useRef<number | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    mountedRef.current = true;
    let stopped = false;

    const handleEvent = (event: MessageEvent<string>, kind: "telemetry" | "alert" | "run_state") => {
      if (event.lastEventId) {
        lastEventIdRef.current = event.lastEventId;
        setLastEventId(event.lastEventId);
      }
      const payload = parsePayload(event);
      if (!payload) return;
      if (kind === "telemetry") rememberTelemetry(queryClient, payload);
      if (kind === "alert") {
        void queryClient.invalidateQueries({ queryKey: ["alerts"] });
        void queryClient.invalidateQueries({ queryKey: ["machines"] });
      }
      if (kind === "run_state") {
        void queryClient.invalidateQueries({ queryKey: ["diagnosis"] });
        void queryClient.invalidateQueries({ queryKey: ["agent-trace"] });
      }
    };

    const clearReconnectTimer = () => {
      if (reconnectTimer.current !== null) {
        window.clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
    };

    const connect = () => {
      if (stopped) return;
      clearReconnectTimer();
      setState(reconnectAttempt.current ? "reconnecting" : "connecting");
      const source = new EventSource(streamUrl(lastEventIdRef.current));
      sourceRef.current = source;
      const events: Array<"telemetry" | "alert" | "run_state"> = ["telemetry", "alert", "run_state"];
      events.forEach((kind) => source.addEventListener(kind, (event) => handleEvent(event as MessageEvent<string>, kind)));
      source.onopen = () => {
        if (!mountedRef.current || sourceRef.current !== source) return;
        reconnectAttempt.current = 0;
        setState("connected");
      };
      source.onerror = () => {
        if (sourceRef.current !== source || stopped) return;
        source.close();
        sourceRef.current = null;
        reconnectAttempt.current += 1;
        setState("reconnecting");
        const delay = Math.min(30_000, 1_000 * 2 ** Math.min(reconnectAttempt.current - 1, 5));
        reconnectTimer.current = window.setTimeout(connect, delay);
      };
    };

    connect();
    const polling = window.setInterval(() => {
      if (stateRef.current === "connected") return;
      // SSE is the low-latency path; these active queries are the bounded
      // fallback that keeps the visible screens current during an outage.
      POLLING_QUERY_KEYS.forEach((queryKey) => {
        void queryClient.refetchQueries({ queryKey, type: "active" });
      });
    }, POLLING_INTERVAL_MS);

    return () => {
      stopped = true;
      mountedRef.current = false;
      clearReconnectTimer();
      sourceRef.current?.close();
      sourceRef.current = null;
      window.clearInterval(polling);
    };
  }, [queryClient]);

  const value = useMemo(() => ({ state, lastEventId }), [lastEventId, state]);
  return <LiveStreamContext.Provider value={value}>{children}</LiveStreamContext.Provider>;
}
