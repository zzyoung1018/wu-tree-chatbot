import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const base = process.env.APP_BASE_PATH || "/";
if (!/^\/(?:[A-Za-z0-9_-]+\/)*$/.test(base)) {
  throw new Error("APP_BASE_PATH must be / or a path such as /ancient_tree/");
}
const proxy = {
  [`${base}api`]: {
    target: "http://127.0.0.1:8000",
    changeOrigin: false,
    rewrite: (path: string) => `/${path.slice(base.length)}`,
  },
};

export default defineConfig({
  base,
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy,
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    proxy,
  },
});
