import { test, expect } from "@playwright/test";

test("app boots and redirects to login", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Choose your control-room role" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Choose a demo persona" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Floor operator/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Maintenance technician/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Maintenance planner/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /System administrator/ })).toBeVisible();
});
