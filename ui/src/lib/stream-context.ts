import { createContext, useContext } from "react";

export type StreamConnectionState = "connecting" | "connected" | "reconnecting";

export type LiveStreamContextValue = {
  state: StreamConnectionState;
  lastEventId: string | null;
};

export const LiveStreamContext = createContext<LiveStreamContextValue | null>(null);

export function useLiveStream() {
  const context = useContext(LiveStreamContext);
  if (!context) throw new Error("useLiveStream must be used inside LiveStreamProvider");
  return context;
}
