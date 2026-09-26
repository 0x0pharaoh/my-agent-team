import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/setup.ts",
  timeout: 60000,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:47396",
    channel: process.env.E2E_CHANNEL ?? "msedge",
  },
});
