import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const apiProxy: ProxyOptions = {
  target: "http://127.0.0.1:8000",
  changeOrigin: false,
  rewrite: (path) => path.replace(/^\/api/, ""),
  configure: (proxy) => {
    proxy.on("proxyRes", (proxyResponse, request) => {
      if (!request.url?.startsWith("/api/stream")) return;
      proxyResponse.headers["cache-control"] = "no-cache";
      proxyResponse.headers["x-accel-buffering"] = "no";
    });
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "localhost",
    port: 5173,
    proxy: { "/api": apiProxy },
  },
});
