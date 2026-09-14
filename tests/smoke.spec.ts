import { expect, test } from "@playwright/test";

test("loads the catalog, searches title and artist, and saves a tempo match", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /your next song is already in time/i })).toBeVisible();

  const search = page.getByRole("textbox", { name: "Search a song or artist" });
  await search.fill("Levitating");
  await page.getByRole("option", { name: /Levitating Dua Lipa/ }).click();
  await expect(page.getByRole("heading", { name: /Songs near 103.0 estimated BPM/ })).toBeVisible();

  await page.getByRole("button", { name: /Flexible/ }).click();
  await expect(page.locator(".match-card h3").first()).toHaveText("Stayin' Alive");
  await expect(page.locator(".match-card h3").filter({ hasText: "Levitating" })).toHaveCount(0);

  const tryMatch = page.getByRole("button", { name: /Try this match/ }).first();
  await tryMatch.click();
  await expect(page.getByRole("button", { name: /Added to your set/ }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /My set 1/ })).toBeVisible();

  await search.fill("dua lipa");
  await expect(page.getByRole("option", { name: /Levitating Dua Lipa/ })).toBeVisible();
  await search.fill("dont stop believin");
  await expect(page.getByRole("option", { name: /Don't Stop Believin' Journey/ })).toBeVisible();
});

test("uses exact, flexible, and exploratory thresholds", async ({ page }) => {
  await page.goto("/");
  const search = page.getByRole("textbox", { name: "Search a song or artist" });
  await search.fill("Levitating");
  await page.getByRole("option", { name: /Levitating Dua Lipa/ }).click();

  await expect(page.getByText("0 tempo matches in 18 songs")).toBeVisible();
  await page.getByRole("button", { name: /Flexible/ }).click();
  await expect(page.getByText("4 tempo matches in 18 songs")).toBeVisible();
  await page.getByRole("button", { name: /Explore/ }).click();
  await expect(page.getByText("5 tempo matches in 18 songs")).toBeVisible();
});

test("plays an estimated-BPM count-in", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Play count in" }).click();
  await expect(page.getByText(/Beat [1-4]/)).toBeVisible();
});

test("shows a useful error when the static catalog cannot load", async ({ page }) => {
  await page.route("**/catalog/songs.v1.json", (route) =>
    route.fulfill({ status: 503, contentType: "application/json", body: "{}" }),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Catalog unavailable");
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
});
