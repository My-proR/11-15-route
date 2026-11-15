import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const chromium = process.env.PW_CHROMIUM ?? (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
export default defineConfig({
  testDir: "./specs",
  timeout: 30_000,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:5173", trace: "off", screenshot: "only-on-failure", launchOptions: chromium ? { executablePath: chromium } : {} },
  projects: [{ name: "desktop", use: { viewport: { width: 1280, height: 800 } } }, { name: "mobile", use: { viewport: { width: 390, height: 844 } }, grep: /@mobile/ }],
});
