import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: `pnpm -C web/apps/admin dev` proxies the API to the compose stack (Caddy on :8080).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8080", changeOrigin: false },
      "/readyz": "http://localhost:8080",
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
