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
  await login(page);

  const savings = page.locator(".kpi-card").filter({ hasText: "INR SAVED" });
  await expect(savings).toContainText(/₹\s*\d{1,2}(?:,\d{2})+,\d{3}/);
  await expect(page.locator("body")).not.toContainText(/[£€]/);

  await page.getByRole("link", { name: "Downtime Ledger" }).click();
  await page.waitForURL(/\/ledger$/);
  const avoidedCost = page.locator(".ledger-headline-value").filter({ hasText: "predictive vs reactive" });
  await expect(avoidedCost).toContainText(/₹\s*\d{1,2}(?:,\d{2})+,\d{3}/);
  await expect(page.locator("body")).not.toContainText(/[£€]/);
});
