import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.API_PROXY_URL || "http://127.0.0.1:8010",
        changeOrigin: true,
      },
      "/a2a": {
        target: process.env.API_PROXY_URL || "http://127.0.0.1:8010",
        changeOrigin: true,
      },
      "/.well-known/agent-card.json": {
        target: process.env.API_PROXY_URL || "http://127.0.0.1:8010",
        changeOrigin: true,
      },
    },
  },
});
