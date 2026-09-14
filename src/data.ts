export type Song = {
  id: string;
  title: string;
  artist: string;
  bpm: number;
  tempoQuality: number;
  listenerRank: number;
};

export type MatchBand = "exact" | "close" | "exploratory";

export type SongMatch = {
  song: Song;
  difference: number;
};

export type PreparedSong = Song & { searchText: string };

type CatalogDocument = {
  version: number;
  generatedAt: string;
  songs: unknown[];
};

export const CATALOG_URL = "/catalog/songs.v1.json";

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
  if (document.version !== 1 || !Array.isArray(document.songs) || !document.songs.length) {
    throw new Error("Catalog version 1 is missing or empty.");
  }
  if (!document.songs.every(isSong)) throw new Error("Catalog contains an invalid song record.");
  const ids = new Set(document.songs.map((song) => song.id));
  if (ids.size !== document.songs.length) throw new Error("Catalog contains duplicate recording IDs.");
  return document.songs.map((song) => ({
    ...song,
    searchText: normaliseSearch(`${song.title} ${song.artist}`),
  }));
}

export async function loadCatalog(signal?: AbortSignal): Promise<PreparedSong[]> {
  const response = await fetch(CATALOG_URL, { signal, cache: "force-cache" });
  if (!response.ok) throw new Error(`Catalog request failed with HTTP ${response.status}.`);
  return prepareCatalog(await response.json());
}

export function searchSongs(songs: PreparedSong[], query: string, limit = 6): PreparedSong[] {
  const term = normaliseSearch(query);
  if (!term) return songs.slice(0, limit);
  return songs.filter((song) => song.searchText.includes(term)).slice(0, limit);
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
