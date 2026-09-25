import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // In development the API runs on the host (or is port-forwarded from Compose).
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // The app's own stylesheets are loaded, not stubbed: src/test/theme.test.ts reads
    // them raw to check that every shade is defined and no raw hex slips in.
    css: { include: [/\/src\/.+\.css/] },
  },
});
