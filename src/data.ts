export type Song = {
  id: string;
  title: string;
  artist: string;
  genres: string[];
  bpm: number;
  tempoQuality: number;
  listenerRank: number;
};

export type MatchBand = "exact" | "close" | "exploratory";

export type SongMatch = {
  song: Song;
  difference: number;
};

export type PreparedSong = Song & {
  searchText: string;
  titleSort: string;
  artistSort: string;
};

export type BrowseSort = "title" | "artist";

export type GenreOption = {
  name: string;
  count: number;
};

type CatalogDocument = {
  version: number;
  generatedAt: string;
  songs: unknown[];
};

export const CATALOG_URL = "/catalog/songs.v2.json";

export const MATCH_THRESHOLDS: Record<MatchBand, number> = {
  exact: 0.5,
  close: 2,
  exploratory: 5,
};

export function normaliseSearch(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/\p{Mark}/gu, "")
    .toLocaleLowerCase()
    .replace(/[^\p{Letter}\p{Number}\s]+/gu, "")
    .trim()
    .replace(/\s+/g, " ");
}

function isSong(value: unknown): value is Song {
  if (!value || typeof value !== "object") return false;
  const song = value as Record<string, unknown>;
  return (
    typeof song.id === "string" &&
    typeof song.title === "string" &&
    song.title.trim().length > 0 &&
    typeof song.artist === "string" &&
    song.artist.trim().length > 0 &&
    Array.isArray(song.genres) &&
    song.genres.length <= 3 &&
    song.genres.every((genre) => typeof genre === "string" && genre.trim().length > 0) &&
    typeof song.bpm === "number" &&
    Number.isFinite(song.bpm) &&
    song.bpm > 0 &&
    typeof song.tempoQuality === "number" &&
    song.tempoQuality >= 0 &&
    song.tempoQuality <= 1 &&
    Number.isInteger(song.listenerRank) &&
    (song.listenerRank as number) > 0
  );
}

export function prepareCatalog(value: unknown): PreparedSong[] {
  if (!value || typeof value !== "object") throw new Error("Catalog response is not an object.");
  const document = value as Partial<CatalogDocument>;
  if (document.version !== 2 || !Array.isArray(document.songs) || !document.songs.length) {
    throw new Error("Catalog version 2 is missing or empty.");
  }
  if (!document.songs.every(isSong)) throw new Error("Catalog contains an invalid song record.");
  const ids = new Set(document.songs.map((song) => song.id));
  if (ids.size !== document.songs.length) throw new Error("Catalog contains duplicate recording IDs.");
  return document.songs.map((song) => ({
    ...song,
    searchText: normaliseSearch(`${song.title} ${song.artist} ${song.genres.join(" ")}`),
    titleSort: normaliseSearch(song.title),
    artistSort: normaliseSearch(song.artist),
  }));
}

export async function loadCatalog(signal?: AbortSignal): Promise<PreparedSong[]> {
  const response = await fetch(CATALOG_URL, { signal, cache: "force-cache" });
  if (!response.ok) throw new Error(`Catalog request failed with HTTP ${response.status}.`);
  return prepareCatalog(await response.json());
}

export function searchSongs(songs: PreparedSong[], query: string, limit = 8): PreparedSong[] {
  const term = normaliseSearch(query);
  if (!term) return songs.slice(0, limit);
  const words = term.split(" ");
  return songs
    .filter((song) => words.every((word) => song.searchText.includes(word)))
    .map((song) => {
      let relevance = 0;
      if (song.titleSort === term) relevance += 100;
      if (song.artistSort === term) relevance += 90;
      if (song.titleSort.startsWith(term)) relevance += 60;
      if (song.artistSort.startsWith(term)) relevance += 50;
      if (song.titleSort.includes(term)) relevance += 30;
      if (song.artistSort.includes(term)) relevance += 20;
      return { song, relevance };
    })
    .sort(
      (left, right) =>
        right.relevance - left.relevance ||
        left.song.listenerRank - right.song.listenerRank ||
        left.song.id.localeCompare(right.song.id),
    )
    .slice(0, limit)
    .map(({ song }) => song);
}

const collator = new Intl.Collator("en", { sensitivity: "base", numeric: true });

export function getGenreOptions(songs: Song[]): GenreOption[] {
  const counts = new Map<string, number>();
  for (const song of songs) {
    for (const genre of new Set(song.genres)) counts.set(genre, (counts.get(genre) ?? 0) + 1);
  }
  return [...counts].map(([name, count]) => ({ name, count })).sort((a, b) => collator.compare(a.name, b.name));
}

export function browseSongs(
  songs: PreparedSong[],
  genre: string,
  sortBy: BrowseSort,
): PreparedSong[] {
  const filtered = genre ? songs.filter((song) => song.genres.includes(genre)) : songs;
  const primary = sortBy === "title" ? "title" : "artist";
  const secondary = sortBy === "title" ? "artist" : "title";
  return [...filtered].sort(
    (left, right) =>
      collator.compare(left[primary], right[primary]) ||
      collator.compare(left[secondary], right[secondary]) ||
      left.id.localeCompare(right.id),
  );
}

export function getMatches(songs: Song[], source: Song, band: MatchBand): SongMatch[] {
  const threshold = MATCH_THRESHOLDS[band];
  return songs
    .filter((song) => song.id !== source.id)
    .map((song) => ({ song, difference: (100 * (song.bpm - source.bpm)) / source.bpm }))
    .filter((match) => Math.abs(match.difference) <= threshold)
    .sort(
      (left, right) =>
        Math.abs(left.difference) - Math.abs(right.difference) ||
        right.song.tempoQuality - left.song.tempoQuality ||
        left.song.listenerRank - right.song.listenerRank ||
        (left.song.id < right.song.id ? -1 : left.song.id > right.song.id ? 1 : 0),
    );
}

export function coloursForRecording(id: string): [string, string] {
  let hash = 2166136261;
  for (const character of id) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  const firstHue = Math.abs(hash) % 360;
  const secondHue = (firstHue + 55 + ((hash >>> 8) % 70)) % 360;
  return [`hsl(${firstHue} 72% 59%)`, `hsl(${secondHue} 55% 31%)`];
}
