import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const seed = JSON.parse(readFileSync(join(process.cwd(), ".seed.json"), "utf8")) as { org_id: string; tokens: Record<string, string> };

async function signInAs(page: Page, role: string) {
  await page.addInitScript(([token, org]) => { sessionStorage.setItem("rp_dev_token", token!); localStorage.setItem("rp_active_org", org!); }, [seed.tokens[role], seed.org_id]);
  await page.goto("/");
}

test("login screen validates input", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Or use email and password").click();
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Enter a valid email")).toBeVisible();
});

test("login offers Google sign-in and switches to Tamil", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
  await page.getByRole("button", { name: "தமிழ்" }).click();
  await expect(page.getByRole("button", { name: "Google மூலம் தொடரவும்" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "ta");
  await page.reload();                                                                    // the choice is remembered
  await expect(page.getByRole("button", { name: "Google மூலம் தொடரவும்" })).toBeVisible();
});

test("app is installable: manifest, icons and service worker are served", async ({ page, request }) => {
  const m = await (await request.get("/manifest.webmanifest")).json();
  expect(m.display).toBe("standalone");
  for (const icon of m.icons) expect((await request.get(icon.src)).ok()).toBeTruthy();
  expect((await request.get("/sw.js")).ok()).toBeTruthy();
  await page.goto("/");
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute("href", "/manifest.webmanifest");
});

test("an invalid invite link says so without asking to sign in", async ({ page }) => {
  await page.goto("/join/not-a-real-invite-token-0000");
  await expect(page.getByText("This invite link is not valid")).toBeVisible();
});

test("dispatcher can open Invite links and see drivers waiting for an account", async ({ page }) => {
  await signInAs(page, "dispatcher");
  await page.getByRole("link", { name: "Invite links" }).click();
  await expect(page.getByRole("heading", { name: "Invite links" })).toBeVisible();
  await expect(page.getByText("Drivers without an account")).toBeVisible();
});

test("dispatcher sees the overview with real counts and no admin-only navigation", async ({ page }) => {
  await signInAs(page, "dispatcher");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByText("Active employees")).toBeVisible();
  await expect(page.getByRole("link", { name: "Daily allocations" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Settings & privacy" })).toHaveCount(0);   // org admin only
  await expect(page.getByRole("link", { name: "Live tracking" })).toHaveCount(0);        // feature is off by default
});

test("admin: location features are off and cannot be switched on without deployment/platform approval", async ({ page }) => {
  await signInAs(page, "org_admin");
  await page.getByRole("link", { name: "Settings & privacy" }).click();
  const sos = page.getByRole("switch", { name: "SOS GPS location" });
  await expect(sos).toBeDisabled();
  await expect(sos).not.toBeChecked();
  await expect(page.getByRole("switch", { name: "Live vehicle tracking" })).toBeDisabled();
  await expect(page.getByText(/disabled for this deployment/i).first()).toBeVisible();
});

test("admin creates an employee through the form and sees it in the list; invalid input is rejected inline", async ({ page }) => {
  await signInAs(page, "org_admin");
  await page.getByRole("link", { name: "Employees", exact: true }).click();
  await expect(page.getByText("Employee 1").first()).toBeVisible();
  await page.getByRole("button", { name: /Add employee/ }).click();
  await page.getByLabel("Full name *").fill("E2E Person");
  await page.getByLabel("Work email *").fill("not-an-email");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText("Enter a valid email")).toBeVisible();
  await expect(page.getByText("Employee code is required")).toBeVisible();
  await page.getByLabel("Employee code *").fill("E2E01");
  await page.getByLabel("Work email *").fill("e2e.person@example.com");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText("Created", { exact: true })).toBeVisible();
  await page.getByLabel("Search Employees").fill("E2E Person");
  await expect(page.getByRole("cell", { name: "E2E Person", exact: true })).toBeVisible();
  // duplicate code is a server-side 409 and is shown to the user
  await page.getByRole("button", { name: /Add employee/ }).click();
  await page.getByLabel("Employee code *").fill("E2E01");
  await page.getByLabel("Full name *").fill("Someone Else");
  await page.getByLabel("Work email *").fill("someone.else@example.com");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("status").filter({ hasText: /exist|duplicate|already|conflict/i })).toBeVisible();
});

test("dispatcher previews a plan; without a routing service the plan is honestly marked non-final", async ({ page }) => {
  await signInAs(page, "dispatcher");
  await page.getByRole("link", { name: "Daily allocations" }).click();
  await page.getByLabel("Shift").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Preview plan" }).click();
  await expect(page.getByText("Proposed plan (not saved)")).toBeVisible();
  await expect(page.getByText(/Road distances are not final/)).toBeVisible();
});

test("employee portal: SOS without GPS feature sends alert and never asks for location @mobile", async ({ page, context }) => {
  let geoAsked = false;
  await context.grantPermissions([]);
  await page.addInitScript(() => { const g = navigator.geolocation; navigator.geolocation.getCurrentPosition = (...a) => { (window as unknown as { __geo: boolean }).__geo = true; return g.getCurrentPosition.apply(g, a as never); }; });
  await signInAs(page, "employee1");
  await expect(page.getByRole("heading", { name: "My transport" })).toBeVisible();
  await page.getByRole("link", { name: "Emergency SOS" }).first().click();
  await expect(page.getByText(/Your location is not collected/)).toBeVisible();
  await page.getByRole("button", { name: "SEND SOS" }).click();
  await expect(page.getByText(/Your alert was sent/)).toBeVisible();
  geoAsked = await page.evaluate(() => Boolean((window as unknown as { __geo?: boolean }).__geo));
  expect(geoAsked).toBe(false);
});

test("employee cannot reach staff pages", async ({ page }) => {
  await signInAs(page, "employee1");
  await expect(page.getByRole("link", { name: "Employees", exact: true })).toHaveCount(0);
  await page.goto("/allocations");
  await expect(page.getByText(/Insufficient role|Not permitted|Forbidden|role/i).first()).toBeVisible();
});

test("dispatcher opens Routes & fleet; driver can switch duty status", async ({ page, browser }) => {
  await signInAs(page, "dispatcher");
  await page.getByRole("link", { name: "Routes & fleet" }).click();
  await expect(page.getByRole("heading", { name: "Routes & fleet" })).toBeVisible();
  await expect(page.getByText(/Routes on /)).toBeVisible();
  const ctx = await browser.newContext();
  const dp = await ctx.newPage();
  await dp.addInitScript(([token, org]) => { sessionStorage.setItem("rp_dev_token", token!); localStorage.setItem("rp_active_org", org!); }, [seed.tokens["driver1"], seed.org_id]);
  await dp.goto("/me");
  await expect(dp.getByText("You are available for trips")).toBeVisible();
  await dp.getByRole("button", { name: "Go off duty" }).click();
  await expect(dp.getByText("You are off duty")).toBeVisible();
  await dp.getByRole("button", { name: "Go available" }).click();
  await expect(dp.getByText("You are available for trips")).toBeVisible();
  await ctx.close();
});

test("org admin opens Billing (off until the platform operator enables it); dispatcher has no Billing", async ({ page }) => {
  await signInAs(page, "org_admin");
  await page.getByRole("link", { name: "Billing" }).click();
  await expect(page.getByRole("heading", { name: "Billing" })).toBeVisible();
  await expect(page.getByText("Billing is not active for your organization yet.")).toBeVisible();
});

test("dispatcher does not see Billing", async ({ page }) => {
  await signInAs(page, "dispatcher");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Billing" })).toHaveCount(0);
});
