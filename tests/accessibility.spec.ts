import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const songs = [
  { id: "00000000-0000-4000-8000-000000000001", title: "Levitating", artist: "Dua Lipa", genres: ["pop"], bpm: 103, tempoQuality: 0.99, listenerRank: 1 },
  { id: "00000000-0000-4000-8000-000000000002", title: "Stayin' Alive", artist: "Bee Gees", genres: ["disco"], bpm: 102.4, tempoQuality: 0.98, listenerRank: 2 },
  { id: "00000000-0000-4000-8000-000000000003", title: "Cole World", artist: "J Cole", genres: ["hip hop"], bpm: 102, tempoQuality: 0.97, listenerRank: 3 },
  { id: "00000000-0000-4000-8000-000000000004", title: "Nosebleeds", artist: "Danny Brown", genres: ["hip hop"], bpm: 104.5, tempoQuality: 0.96, listenerRank: 4 },
  { id: "00000000-0000-4000-8000-000000000005", title: "Top Floor", artist: "Wiz Khalifa", genres: ["hip hop"], bpm: 101, tempoQuality: 0.95, listenerRank: 5 },
  { id: "00000000-0000-4000-8000-000000000006", title: "Heat Waves", artist: "Glass Animals", genres: ["indie pop"], bpm: 108, tempoQuality: 0.94, listenerRank: 6 },
  { id: "00000000-0000-4000-8000-000000000007", title: "Don't Stop Believin'", artist: "Journey", genres: ["rock"], bpm: 90, tempoQuality: 0.93, listenerRank: 7 },
];

async function useCatalog(page: Page) {
  await page.route("**/catalog/songs.v2.json", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ version: 2, generatedAt: "2026-09-12T00:00:00Z", songs }),
  }));
}

async function getAxeViolations(page: Page, state: string) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa"])
    .analyze();
  return results.violations.flatMap((violation) => violation.nodes.map((node) =>
    `${state} | ${violation.id} | ${node.target.join(" > ")} | ${node.any[0]?.message ?? node.all[0]?.message ?? node.failureSummary}`,
  ));
}

test("has no automated WCAG A/AA violations across interactive states", async ({ page }) => {
  await useCatalog(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const violations = await getAxeViolations(page, "initial page");

  const search = page.getByRole("combobox", { name: "Search a song or artist" });
  await search.fill("Levitating");
  await expect(page.getByRole("listbox", { name: "Search results" })).toBeVisible();
  violations.push(...await getAxeViolations(page, "search suggestions"));

  await search.press("Enter");
  const allResults = page.getByRole("listbox", { name: "All results for Levitating" });
  await expect(allResults).toBeVisible();
  violations.push(...await getAxeViolations(page, "expanded catalog search"));

  await allResults.getByRole("option", { name: /Levitating Dua Lipa/ }).click();
  await expect(page.getByText("0 tempo matches in 7 songs")).toBeVisible();
  violations.push(...await getAxeViolations(page, "empty tempo matches"));

  await page.getByRole("button", { name: /Flexible/ }).click();
  await expect(page.getByRole("region", { name: "Tempo matches" })).toBeVisible();
  violations.push(...await getAxeViolations(page, "tempo matches"));

  await page.getByRole("button", { name: /Show 1 more match/ }).click();
  violations.push(...await getAxeViolations(page, "expanded tempo matches"));

  await page.getByRole("button", { name: /Save .+ to my set/ }).first().click();
  await page.getByRole("button", { name: /My set 1/ }).click();
  await expect(page.getByRole("dialog", { name: /My set 1/ })).toBeVisible();
  violations.push(...await getAxeViolations(page, "my set dialog"));

  expect(violations).toEqual([]);
});

test("has no automated WCAG A/AA violations in the catalog error state", async ({ page }) => {
  await page.route("**/catalog/songs.v2.json", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: "{}",
  }));
  await page.goto("/");
  await expect(page.getByRole("alert")).toBeVisible();
  expect(await getAxeViolations(page, "catalog error")).toEqual([]);
});

test("has no automated WCAG A/AA violations in genre browsing", async ({ page }) => {
  await useCatalog(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");

  await page.getByRole("tab", { name: "Browse genres" }).click();
  await expect(page.getByRole("listbox", { name: "Genres" })).toBeVisible();
  const violations = await getAxeViolations(page, "genre chooser");

  await page.getByRole("option", { name: "Hip Hop 3 songs" }).click();
  await expect(page.getByRole("listbox", { name: "Hip Hop songs" })).toBeVisible();
  violations.push(...await getAxeViolations(page, "genre songs"));

  expect(violations).toEqual([]);
});
