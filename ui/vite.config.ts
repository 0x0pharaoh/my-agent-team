import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../server/src/my_team/static", emptyOutDir: true },
  test: { include: ["src/**/*.test.ts"] },
});
