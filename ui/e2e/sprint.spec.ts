import { expect, test } from "@playwright/test";
import { state } from "./setup";

test("sprint is planned, started, and completed", async ({ page }) => {
  const s = state();
  await page.goto("/");
  await page.getByLabel("Passphrase").fill(s.passphrase);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.getByRole("button", { name: "Backlog" }).click();
  await page.getByLabel("Sprint name").fill("E2E sprint");
  await page.getByLabel("Sprint goal").fill("prove it");
  await page.getByLabel("Ends").fill("2026-12-31");
  await page.getByRole("button", { name: "Plan sprint" }).click();
  const section = page.locator('section[aria-label="E2E sprint"]');
  await expect(section).toBeVisible();
  await section.getByRole("button", { name: "Start" }).click();
  await expect(section.getByText("active")).toBeVisible();
  await section.getByRole("button", { name: "Complete" }).click();
  await expect(page.locator('section[aria-label="Past sprints"]')).toContainText("E2E sprint");
});
