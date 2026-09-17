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

test("supports skip navigation, tabs, comboboxes, and listboxes by keyboard", async ({ page }) => {
  await useCatalog(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to main content" });
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();

  const searchTab = page.getByRole("tab", { name: "Search songs" });
  const browseTab = page.getByRole("tab", { name: "Browse genres" });
  await searchTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(browseTab).toBeFocused();
  await expect(browseTab).toHaveAttribute("aria-selected", "true");

  await page.keyboard.press("Tab");
  const genreSearch = page.getByRole("combobox", { name: "Search genres" });
  await expect(genreSearch).toBeFocused();
  await genreSearch.fill("hip");
  await page.keyboard.press("ArrowDown");
  await expect(genreSearch).toHaveAttribute("aria-activedescendant", "genre-option-0");
  await page.keyboard.press("Enter");

  const genreSongs = page.getByRole("listbox", { name: "Hip Hop songs" });
  const firstSong = genreSongs.getByRole("option").first();
  await expect(firstSong).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(genreSongs.getByRole("option").nth(1)).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#matches")).toBeFocused();
});

test("keeps combobox focus while navigating suggestions and selects the active option", async ({ page }) => {
  await useCatalog(page);
  await page.goto("/");

  const search = page.getByRole("combobox", { name: "Search a song or artist" });
  await search.fill("Levitating");
  await page.keyboard.press("ArrowDown");
  await expect(search).toBeFocused();
  await expect(search).toHaveAttribute("aria-activedescendant", "song-option-0");
  await page.keyboard.press("Enter");
  await expect(page.locator("#matches")).toBeFocused();
});

test("traps focus in the modal dialog and restores it when closed", async ({ page }) => {
  await useCatalog(page);
  await page.goto("/");

  const search = page.getByRole("combobox", { name: "Search a song or artist" });
  await search.fill("Levitating");
  await page.getByRole("option", { name: /Levitating Dua Lipa/ }).click();
  await page.getByRole("button", { name: /Flexible/ }).click();
  await page.getByRole("button", { name: /Save Stayin' Alive by Bee Gees/ }).click();

  const openButton = page.getByRole("button", { name: /My set 1/ });
  await openButton.click();
  const closeButton = page.getByRole("button", { name: "Close my set" });
  const continueButton = page.getByRole("button", { name: "Keep discovering" });
  await expect(closeButton).toBeFocused();

  await page.keyboard.press("Shift+Tab");
  await expect(continueButton).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(closeButton).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(openButton).toBeFocused();
});

test("reflows at 320 CSS pixels and tolerates WCAG text spacing", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await useCatalog(page);
  await page.goto("/");
  await page.addStyleTag({ content: `
    * { letter-spacing: .12em !important; line-height: 1.5 !important; word-spacing: .16em !important; }
    p { margin-bottom: 2em !important; }
  ` });

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);

  const search = page.getByRole("combobox", { name: "Search a song or artist" });
  await search.fill("Levitating");
  await expect(page.getByRole("option", { name: /Levitating Dua Lipa/ })).toBeVisible();
});
