import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Session cookie is HttpOnly + SameSite=Lax, so dev must be same-origin.
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
