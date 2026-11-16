import { expect, test, type Page } from "@playwright/test";


async function loginAsPlanner(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Username").fill("planner");
  await page.getByLabel("Password").fill("fixture");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/schedule$/);
}


test("ERP workspace exposes history and idempotent approval-backed creation", async ({ page }) => {
  await loginAsPlanner(page);
  await page.goto("/diagnosis/new-pump-07");
  await expect(page.getByRole("heading", { name: "Risk explanation" })).toBeVisible({ timeout: 25_000 });
  await page.goto("/schedule");
  const card = page.locator(".approval-card").filter({ hasText: "planner" }).first();
  await card.getByRole("button", { name: "Review evidence" }).click();
  const approve = page.getByRole("button", { name: "Approve and create work order" });
  await expect(approve).toBeEnabled({ timeout: 25_000 });
  await approve.click();
  await expect(page.getByText("Approved and synced to ERP.")).toBeVisible();
  await page.getByRole("link", { name: "ERP Work Orders" }).click();
  await page.waitForURL(/\/erp$/);

  await expect(page.getByRole("heading", { name: "ERP Work Orders" })).toBeVisible();
  await expect(page.getByText("ERP connected · live data")).toBeVisible();
  await expect(page.getByRole("table", { name: "ERP work-order history" })).toBeVisible();
  await expect(page.getByRole("row").nth(1)).toContainText(/WO-/);
  await expect(page.locator(".erp-parts-grid")).toContainText("LUB-05");
  await expect(page.locator("body")).not.toContainText(/[£€]/);

  await page.getByRole("button", { name: "Retry stored work order" }).click();
  await expect(page.locator(".erp-form-result")).toContainText(/Work order WO-/);
  await expect(page.getByText("No work orders are waiting to synchronize.")).toBeVisible();
});
