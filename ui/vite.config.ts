import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const apiPrefixes = [
  "/auth",
  "/health",
  "/metrics",
  "/machines",
  "/diagnoses",
  "/runs",
  "/alerts",
  "/approvals",
  "/schedule",
  "/ingest",
  "/ledger",
  "/eval",
  "/llm",
  "/audit",
  "/erp",
  "/rehearsal",
  "/stream",
  "/meta",
  "/docs",
  "/openapi.json",
];

/** Several API prefixes collide with client routes (`/schedule`, `/erp`,
 *  `/ledger`, `/audit`). Without this bypass a page refresh or a pasted deep
 *  link on those routes was proxied to FastAPI and the browser rendered raw
 *  JSON instead of the app. Document navigations ask for HTML and carry no
 *  `X-Requested-With`; the app's own `fetchJson` always sends
 *  `Accept: application/json`, so the two are cleanly separable. */
const bypassDocumentNavigations: ProxyOptions["bypass"] = (request) => {
  const accept = request.headers.accept ?? "";
  const isDocument = request.method === "GET" && accept.includes("text/html");
  return isDocument ? "/index.html" : undefined;
};

const apiProxy: Record<string, ProxyOptions> = {};
for (const prefix of apiPrefixes) {
  const options: ProxyOptions = { target: "http://127.0.0.1:8000", changeOrigin: false };
  // `/docs` and `/openapi.json` are meant to be opened in a browser tab.
  if (prefix !== "/docs" && prefix !== "/openapi.json") options.bypass = bypassDocumentNavigations;
  if (prefix === "/stream") {
    options.configure = (proxy) => {
      proxy.on("proxyRes", (proxyResponse) => {
        proxyResponse.headers["cache-control"] = "no-cache";
        proxyResponse.headers["x-accel-buffering"] = "no";
      });
    };
  }
  apiProxy[prefix] = options;
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "localhost",
    port: 5173,
    proxy: apiProxy,
  },
});
