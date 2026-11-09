import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // Bind IPv4 so both http://127.0.0.1:5173 and http://localhost:5173 work on Windows.
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      // 8000 常被本机其它服务占用；DataReflex API 默认用 8001。
      "/api": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
      },
      // V2-9 sandbox badge reads GET /health (not under /api).
      "/health": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
      },
    },
  },
});
