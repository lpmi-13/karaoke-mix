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

async function useCatalog(page: Page, catalog = songs) {
  await page.route("**/catalog/songs.v2.json", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ version: 2, generatedAt: "2026-09-12T00:00:00Z", songs: catalog }),
  }));
}

test("waits for a song choice before showing tempo matches", async ({ page }) => {
  await useCatalog(page);
  await page.goto("/");

  await expect(page.locator(".source-tile strong")).toHaveText("Choose a song");
  await expect(page.locator("#matches")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Tempo matches" })).toHaveCount(0);

  const search = page.getByRole("textbox", { name: "Search a song or artist" });
  await search.fill("Levitating");
  await page.getByRole("option", { name: /Levitating Dua Lipa/ }).click();

  await expect(page.locator("#matches")).toBeVisible();
  await expect(page.getByRole("heading", { name: /Songs near 103.0 estimated BPM/ })).toBeVisible();
});

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
  const mySetButton = page.getByRole("button", { name: /My set 1/ });
  await expect(mySetButton).toBeVisible();
  await mySetButton.click();

  const mySet = page.locator("#my-set-drawer");
  await expect(mySet).toBeVisible();
  await expect(mySet).toHaveAccessibleName("My set 1");
  await expect(mySet.getByText("Stayin' Alive", { exact: true })).toBeVisible();
  await expect(mySet.getByText("Bee Gees", { exact: true })).toBeVisible();
  await expect(mySet.getByText("102.4", { exact: true })).toBeVisible();
  await mySet.getByRole("button", { name: "Remove Stayin' Alive by Bee Gees from my set" }).click();
  await expect(page.getByRole("button", { name: /My set 0/ })).toBeVisible();
  await expect(mySet).toHaveAccessibleName("My set 0");
  await expect(mySet.getByText("Your set is waiting")).toBeVisible();
  await expect(tryMatch).toHaveAccessibleName("Try this match");
  await page.keyboard.press("Escape");
  await expect(page.locator("#my-set-drawer")).toBeHidden();
  await expect(page.getByRole("button", { name: /My set 0/ })).toBeFocused();

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
  await expect(page.locator(".browse-list")).toHaveClass(/scroll-region/);
  await expect(browseGenres).toHaveAttribute("aria-selected", "true");
  await page.getByRole("combobox", { name: "Browse by genre" }).selectOption("hip hop");
  await expect(page.getByText("3 songs", { exact: true })).toBeVisible();
  await expect(page.locator(".browse-list .search-result strong").first()).toHaveText("Cole World");

  await page.getByRole("button", { name: "Artist", pressed: false }).click();
  await expect(page.locator(".browse-list .search-result strong").first()).toHaveText("Nosebleeds");
  await expect(page.getByRole("button", { name: "Artist", pressed: true })).toBeVisible();
});

test("expands all search results on Enter without selecting a suggestion", async ({ page }) => {
  const matchingSongs = Array.from({ length: 20 }, (_, index) => ({
    id: `search-result-${index}`,
    title: `Shared Groove ${String(index + 1).padStart(2, "0")}`,
    artist: `Test Artist ${index + 1}`,
    genres: ["pop"],
    bpm: 100 + index / 10,
    tempoQuality: 0.9,
    listenerRank: songs.length + index + 1,
  }));
  await useCatalog(page, [...songs, ...matchingSongs]);
  await page.goto("/");

  const expandedResults = page.locator("#catalog-search-results");
  expect(await expandedResults.evaluate((element) => element.getBoundingClientRect().height)).toBe(0);

  const search = page.getByRole("textbox", { name: "Search a song or artist" });
  await search.fill("Shared Groove");
  await expect(page.getByText("Best matches", { exact: true })).toBeVisible();
  const suggestions = page.getByRole("listbox", { name: "Search results" });
  await expect(suggestions).toHaveClass(/scroll-region/);
  await expect(suggestions.getByRole("option")).toHaveCount(8);

  const scrollBeforeSearch = await page.evaluate(() => window.scrollY);
  await search.press("Enter");
  await expect(page.getByRole("listbox", { name: "Search results" })).toBeHidden();
  await expect(expandedResults).toHaveClass(/catalog-search-results--open/);
  await expect.poll(() => expandedResults.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThan(0);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(scrollBeforeSearch);
  await expect(page.locator(".source-tile strong")).toHaveText("Choose a song");
  const resultsHeading = page.locator(".catalog-search-results__heading h2");
  await expect(resultsHeading).toHaveText("Results for “Shared Groove”");
  await expect(resultsHeading).toBeVisible();

  const results = page.getByRole("listbox", { name: "All results for Shared Groove" });
  await expect(results).toHaveClass(/scroll-region/);
  await expect(results.getByRole("option")).toHaveCount(20);
  const dimensions = await results.evaluate((element) => ({
    clientHeight: element.clientHeight,
    overflowY: getComputedStyle(element).overflowY,
    scrollHeight: element.scrollHeight,
  }));
  expect(dimensions.overflowY).toBe("auto");
  expect(dimensions.scrollHeight).toBeGreaterThan(dimensions.clientHeight);

  await results.scrollIntoViewIfNeeded();
  await results.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect(results.getByRole("option", { name: /Shared Groove 20/ })).toBeInViewport();

  await page.getByRole("button", { name: "Close search results" }).click();
  await expect(expandedResults).not.toHaveClass(/catalog-search-results--open/);
  await expect(resultsHeading).toBeAttached();
  expect(await expandedResults.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThan(0);
  await expect(resultsHeading).toHaveCount(0);
  await expect.poll(() => expandedResults.evaluate((element) => element.getBoundingClientRect().height)).toBe(0);

  await search.press("Enter");
  const reopenedResults = page.getByRole("listbox", { name: "All results for Shared Groove" });
  await expect(reopenedResults).toBeVisible();
  await reopenedResults.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await reopenedResults.getByRole("option", { name: /Shared Groove 20/ }).click();
  await expect(page.getByRole("heading", { name: /Songs near 101.9 estimated BPM/ })).toBeVisible();
  await expect(expandedResults).not.toHaveClass(/catalog-search-results--open/);
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

test("reveals additional tempo matches in a bounded scroll area", async ({ page }) => {
  const tempoMatches = Array.from({ length: 36 }, (_, index) => ({
    id: `tempo-match-${index}`,
    title: `Tempo Match ${String(index + 1).padStart(2, "0")}`,
    artist: `Tempo Artist ${index + 1}`,
    genres: ["dance"],
    bpm: 103 + (index % 5) / 100,
    tempoQuality: 0.99 - index / 1000,
    listenerRank: index + 2,
  }));
  await useCatalog(page, [songs[0], ...tempoMatches]);
  await page.goto("/");

  const search = page.getByRole("textbox", { name: "Search a song or artist" });
  await search.fill("Levitating");
  await page.getByRole("option", { name: /Levitating Dua Lipa/ }).click();

  const scroller = page.getByRole("region", { name: "Tempo matches" });
  await expect(scroller).toHaveClass(/scroll-region/);
  const reveal = page.locator(".match-results-more");
  await expect(scroller.locator(".match-card").first()).toBeVisible();
  await expect(scroller.locator(".match-card").nth(3)).toBeHidden();
  expect(await reveal.evaluate((element) => element.getBoundingClientRect().height)).toBe(0);

  const toggle = page.locator(".show-more");
  await expect(toggle).toHaveAccessibleName("Show 33 more matches");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(toggle).toHaveAttribute("aria-controls", "tempo-match-results");
  await expect.poll(() => reveal.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThan(0);

  await expect(scroller.locator(".match-card").nth(3)).toBeVisible();
  const dimensions = await scroller.evaluate((element) => ({
    clientHeight: element.clientHeight,
    overflowY: getComputedStyle(element).overflowY,
    scrollHeight: element.scrollHeight,
    viewportHeight: window.innerHeight,
  }));
  expect(dimensions.overflowY).toBe("auto");
  expect(dimensions.scrollHeight).toBeGreaterThan(dimensions.clientHeight);
  expect(dimensions.clientHeight).toBeLessThanOrEqual(dimensions.viewportHeight * 0.71);

  await scroller.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect.poll(() => scroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await expect.poll(() => scroller.locator(".match-card").first().evaluate((card) => {
    const cardBounds = card.getBoundingClientRect();
    const scrollBounds = card.closest("#tempo-match-results")!.getBoundingClientRect();
    return cardBounds.bottom > scrollBounds.top && cardBounds.top < scrollBounds.bottom;
  })).toBe(false);

  await page.getByRole("button", { name: "Show fewer matches" }).click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect.poll(() => reveal.evaluate((element) => element.getBoundingClientRect().height)).toBe(0);
  expect(await scroller.evaluate((element) => element.scrollTop)).toBe(0);
});

test("plays an estimated-BPM count-in", async ({ page }) => {
  await useCatalog(page);
  await page.goto("/");
  const search = page.getByRole("textbox", { name: "Search a song or artist" });
  await search.fill("Levitating");
  await page.getByRole("option", { name: /Levitating Dua Lipa/ }).click();
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
