import { expect, test } from "@playwright/test";

test.describe("AgentForge (backend required)", () => {
  test("connect page loads and shows brand", async ({ page }) => {
    await page.goto("/connect");
    await expect(
      page.getByRole("heading", { name: /Connect to AgentForge/i }),
    ).toBeVisible();
  });
});
