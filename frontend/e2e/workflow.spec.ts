import { expect, test } from "@playwright/test";

async function connect(page: import("@playwright/test").Page) {
  await page.goto("/connect");
  await page.getByLabel(/Bearer token/i).fill("demo-token");
  await page.getByRole("button", { name: /Connect/i }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible({
    timeout: 15_000,
  });
}

test.describe("AgentForge dashboard workflow", () => {
  test("unauthorized API is blocked without token", async ({ request }) => {
    const res = await request.get("http://127.0.0.1:8000/v1/jobs");
    expect(res.status()).toBe(401);
  });

  test("connect, submit, watch completion", async ({ page }) => {
    await connect(page);
    await expect(page.getByText(/Demonstration mode/i)).toBeVisible();

    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "New run" }).click();
    await page.getByLabel(/^Goal/i).fill(
      "Write one sentence about AgentForge for the dashboard demo.",
    );
    await page.getByRole("button", { name: /Submit job/i }).click();
    await expect(page.getByRole("heading", { name: /Job/i })).toBeVisible();
    await expect(page.getByText("Completed").first()).toBeVisible({
      timeout: 45_000,
    });
  });

  test("approval flow approve path", async ({ page }) => {
    await connect(page);

    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "New run" }).click();
    await page.getByLabel(/^Goal/i).fill("Prepare a note that needs human approval.");
    await page
      .getByLabel(/Require human approval for a protected artifact write/i)
      .check();
    await page.getByRole("button", { name: /Submit job/i }).click();
    await expect(page.getByText(/Awaiting approval/i).first()).toBeVisible({
      timeout: 45_000,
    });

    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Approvals" }).click();
    await expect(page.getByRole("heading", { name: /Human approvals/i })).toBeVisible();
    await expect(page.getByText("Fingerprint:", { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: /Approve current proposal/i }).click();
  });

  test("reconnect after reload keeps session only if remembered", async ({ page }) => {
    await page.goto("/connect");
    await page.getByLabel(/Bearer token/i).fill("demo-token");
    await page.getByLabel(/Remember for this browser tab/i).check();
    await page.getByRole("button", { name: /Connect/i }).click();
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible({
      timeout: 15_000,
    });
    await page.reload();
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible({
      timeout: 15_000,
    });
  });
});
