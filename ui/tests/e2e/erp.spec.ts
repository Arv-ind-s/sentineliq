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
  await page.getByRole("link", { name: "ERP Work Orders" }).click();
  await page.waitForURL(/\/erp$/);

  await expect(page.getByRole("heading", { name: "ERP Work Orders" })).toBeVisible();
  await expect(page.getByText("ERP connected · live data")).toBeVisible();
  await expect(page.getByRole("table", { name: "ERP work-order history" })).toBeVisible();
  await expect(page.getByRole("row").nth(1)).toContainText(/WO-/);
  await expect(page.locator(".erp-parts-grid")).toContainText("LUB-05");
  await expect(page.locator("body")).not.toContainText(/[£€]/);

  await page.getByRole("button", { name: "Create / sync work order" }).click();
  await expect(page.getByRole("status")).toContainText(/Work order WO-/);
  await expect(page.getByText("No work orders are waiting to synchronize.")).toBeVisible();
});
