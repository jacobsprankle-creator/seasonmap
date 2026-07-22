import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // Local dev: `wrangler dev` serves the worker on :8787.
    proxy: { "/api": "http://localhost:8787" },
  },
});
