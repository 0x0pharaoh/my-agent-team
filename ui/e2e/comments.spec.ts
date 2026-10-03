import { expect, test } from "@playwright/test";
import { Agent } from "./agent";
import { state } from "./setup";

test("ticket comments from the human and an agent appear live in the open dialog", async ({ page }) => {
  const s = state();
  await page.goto("/");
  await page.getByLabel("Passphrase").fill(s.passphrase);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.getByLabel("New ticket title").fill("Comment ticket");
  await page.getByLabel(/Acceptance criteria/).fill("discussed");
  await page.getByRole("button", { name: "Create ticket" }).click();
  const card = page.locator('section[aria-label="Ready"]').locator("li", { hasText: "Comment ticket" });
  const key = ((await card.getByLabel(/Assign /).getAttribute("aria-label")) ?? "").split(" ").pop() ?? "";
  await card.getByRole("button", { name: /Comment ticket/ }).click();

  const comments = page.locator('section[aria-label="Comments"]');
  await comments.getByLabel(`Comment on ${key}`).fill("Please reuse the existing index.");
  await comments.getByRole("button", { name: "Post" }).click();
  await expect(comments.getByText("Please reuse the existing index.")).toBeVisible();

  const agent = Agent.fromHome(s.base, s.home);
  agent.projectId = s.projectId;
  await agent.op("message_send", { to: key, body: "Will do, reusing it." }, s.sessionId);
  await expect(comments.getByText("Will do, reusing it.")).toBeVisible({ timeout: 15000 });
});
