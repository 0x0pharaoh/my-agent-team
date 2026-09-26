import { expect, test } from "@playwright/test";
import { Agent } from "./agent";
import { state } from "./setup";

test("created ticket is assigned, claimed, and shows In progress", async ({ page }) => {
  const s = state();
  await page.goto("/");
  await page.getByLabel("Passphrase").fill(s.passphrase);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.getByLabel("New ticket title").fill("E2E ticket");
  await page.getByLabel(/Acceptance criteria/).fill("ships");
  await page.getByRole("button", { name: "Create ticket" }).click();
  const ready = page.locator('section[aria-label="Ready"]');
  const card = ready.locator("li", { hasText: "E2E ticket" });
  await expect(card).toBeVisible();
  const assign = card.getByLabel(/Assign /);
  const key = ((await assign.getAttribute("aria-label")) ?? "").split(" ").pop() ?? "";
  await assign.selectOption(s.seat);
  await expect(card.getByText("Pending pickup")).toBeVisible();
  const agent = Agent.fromHome(s.base, s.home);
  agent.projectId = s.projectId;
  await agent.op("ticket_claim", { key }, s.sessionId);
  await expect(page.locator('section[aria-label="In progress"]').getByText("E2E ticket")).toBeVisible({
    timeout: 15000,
  });
});
