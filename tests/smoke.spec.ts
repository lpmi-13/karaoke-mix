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

test("loads the catalog, searches title and artist, and saves a tempo match", async ({ page }) => {
  await useCatalog(page);
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

test("browses a genre and toggles between song and artist ordering", async ({ page }) => {
  await useCatalog(page);
  await page.goto("/");

  const browseGenres = page.getByRole("tab", { name: "Browse genres" });
  await expect(browseGenres).toBeVisible();
  await browseGenres.click();
  await expect(browseGenres).toHaveAttribute("aria-selected", "true");
  await page.getByRole("combobox", { name: "Browse by genre" }).selectOption("hip hop");
  await expect(page.getByText("3 songs", { exact: true })).toBeVisible();
  await expect(page.locator(".browse-list .search-result strong").first()).toHaveText("Cole World");

  await page.getByRole("button", { name: "Artist", pressed: false }).click();
  await expect(page.locator(".browse-list .search-result strong").first()).toHaveText("Nosebleeds");
  await expect(page.getByRole("button", { name: "Artist", pressed: true })).toBeVisible();
});

test("uses exact, flexible, and exploratory thresholds", async ({ page }) => {
  await useCatalog(page);
  await page.goto("/");
  const search = page.getByRole("textbox", { name: "Search a song or artist" });
  await search.fill("Levitating");
  await page.getByRole("option", { name: /Levitating Dua Lipa/ }).click();

  await expect(page.getByText("0 tempo matches in 7 songs")).toBeVisible();
  await page.getByRole("button", { name: /Flexible/ }).click();
  await expect(page.getByText("4 tempo matches in 7 songs")).toBeVisible();
  await page.getByRole("button", { name: /Explore/ }).click();
  await expect(page.getByText("5 tempo matches in 7 songs")).toBeVisible();
});

test("plays an estimated-BPM count-in", async ({ page }) => {
  await useCatalog(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Play count in" }).click();
  await expect(page.getByText(/Beat [1-4]/)).toBeVisible();
});

test("shows a useful error when the static catalog cannot load", async ({ page }) => {
  await page.route("**/catalog/songs.v2.json", (route) =>
    route.fulfill({ status: 503, contentType: "application/json", body: "{}" }),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Catalog unavailable");
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
});
