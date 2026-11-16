import { expect, test } from "@playwright/test";

for (const [role, label, route] of [
  ["operator", "Floor operator", "/floor"],
  ["technician", "Maintenance technician", "/schedule"],
  ["planner", "Maintenance planner", "/schedule"],
  ["admin", "System administrator", "/trust"],
]) {
  test(`${role} persona retains local demo login`, async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: new RegExp(label) }).click();
    await expect(page.getByLabel("Username")).toHaveValue(role);
    await expect(page.getByLabel("Password")).toHaveValue("fixture");
    await page.getByRole("button", { name: `Enter as ${label}` }).click();
    await expect(page).toHaveURL(new RegExp(`${route}$`));
    await expect(page.getByRole("main")).toBeVisible();
  });
}
