import { expect, test, type Page } from "@playwright/test";


async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password").fill("fixture");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/trust$/);
  await page.getByRole("link", { name: "Live Floor" }).click();
  await page.waitForURL(/\/floor$/);
}


test("dashboard monetary values use INR and Indian grouping", async ({ page }) => {
  // Exercise formatting with a known amount independently of model quality.
  await page.route("**/api/ledger*", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    payload.policies = payload.policies.map((policy: { policy: string; avoided_cost_inr: number }) =>
      policy.policy === "predictive" ? { ...policy, avoided_cost_inr: 1234567 } : policy);
    await route.fulfill({ response, json: payload });
  });
  await login(page);

  const savings = page.locator(".kpi-card").filter({ hasText: "INR SAVED" });
  await expect(savings).toContainText("₹12,34,567");
  await expect(page.locator("body")).not.toContainText(/[£€]/);

  await page.getByRole("link", { name: "Downtime Ledger" }).click();
  await page.waitForURL(/\/ledger$/);
  const avoidedCost = page.locator(".ledger-headline-value").filter({ hasText: "predictive vs reactive" });
  await expect(avoidedCost).toContainText("₹12,34,567");
  await expect(page.locator("body")).not.toContainText(/[£€]/);
});
