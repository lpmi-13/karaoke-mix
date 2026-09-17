import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  CircleHelp,
  Headphones,
  Library,
  Music2,
  Pause,
  Play,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  WandSparkles,
  X,
} from "lucide-react";
import {
  MATCH_THRESHOLDS,
  browseSongs,
  coloursForRecording,
  getGenreOptions,
  getMatches,
  loadCatalog,
  normaliseSearch,
  searchSongs,
  type BrowseSort,
  type MatchBand,
  type PreparedSong,
  type Song,
  type SongMatch,
} from "./data";

const BROWSE_PAGE_SIZE = 80;
const SEARCH_PAGE_SIZE = 80;
const MATCH_PREVIEW_SIZE = 3;
const MATCH_PAGE_SIZE = 60;
const SEARCH_RESULTS_TRANSITION_MS = 672;
const SEARCH_SCROLL_DURATION_MS = 864;
const SEARCH_MOTION_CURVE = [0.22, 1, 0.36, 1] as const;

function cubicBezierCoordinate(progress: number, firstControl: number, secondControl: number): number {
  const inverse = 1 - progress;
  return 3 * inverse * inverse * progress * firstControl
    + 3 * inverse * progress * progress * secondControl
    + progress * progress * progress;
}

function cubicBezierProgress(progress: number, x1: number, y1: number, x2: number, y2: number): number {
  if (progress <= 0) return 0;
  if (progress >= 1) return 1;

  let lower = 0;
  let upper = 1;
  let curveProgress = progress;
  for (let iteration = 0; iteration < 10; iteration += 1) {
    curveProgress = (lower + upper) / 2;
    if (cubicBezierCoordinate(curveProgress, x1, x2) < progress) lower = curveProgress;
    else upper = curveProgress;
  }
  return cubicBezierCoordinate(curveProgress, y1, y2);
}

function animateScrollTo(element: HTMLElement): () => void {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    element.scrollIntoView({ block: "start" });
    return () => undefined;
  }

  const start = window.scrollY;
  const documentHeight = document.documentElement.scrollHeight;
  const target = Math.max(0, Math.min(
    start + element.getBoundingClientRect().top - 24,
    documentHeight - window.innerHeight,
  ));
  const distance = target - start;
  const startedAt = performance.now();
  const root = document.documentElement;
  const previousScrollBehavior = root.style.scrollBehavior;
  let frame = 0;
  let active = true;

  root.style.scrollBehavior = "auto";
  const finish = () => {
    if (!active) return;
    active = false;
    root.style.scrollBehavior = previousScrollBehavior;
  };

  const step = (now: number) => {
    const progress = Math.min(1, (now - startedAt) / SEARCH_SCROLL_DURATION_MS);
    const eased = cubicBezierProgress(progress, ...SEARCH_MOTION_CURVE);
    window.scrollTo(0, start + distance * eased);
    if (progress < 1) frame = window.requestAnimationFrame(step);
    else finish();
  };

  frame = window.requestAnimationFrame(step);
  return () => {
    window.cancelAnimationFrame(frame);
    finish();
  };
}

function genreLabel(value: string): string {
  return value.replace(/(^|[\s/-])\p{Letter}/gu, (match) => match.toLocaleUpperCase());
}

function Artwork({ song, size = "large" }: { song: Song; size?: "small" | "large" }) {
  const initials = song.artist
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0])
    .join("");
  const colours = coloursForRecording(song.id);

  return (
    <div
      className={`artwork artwork--${size}`}
      style={{ "--art-a": colours[0], "--art-b": colours[1] } as React.CSSProperties}
      aria-hidden="true"
    >
      <span className="artwork__rings" />
      <span className="artwork__initials">{initials}</span>
    </div>
  );
}

function SongResult({ song, onSelect }: { song: Song; onSelect: (song: Song) => void }) {
  return (
    <button className="search-result" onClick={() => onSelect(song)} role="option">
      <Artwork song={song} size="small" />
      <span className="search-result__copy">
        <strong>{song.title}</strong>
        <small>{song.artist}{song.genres[0] && <span className="search-result__genre">{genreLabel(song.genres[0])}</span>}</small>
      </span>
      <span className="search-result__bpm">~{song.bpm.toFixed(1)} BPM</span>
    </button>
  );
}

function SearchBox({
  songs,
  onSelect,
  onSearch,
}: {
  songs: PreparedSong[];
  onSelect: (song: Song) => void;
  onSearch: (query: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [genre, setGenre] = useState("");
  const [genreQuery, setGenreQuery] = useState("");
  const [sortBy, setSortBy] = useState<BrowseSort>("title");
  const [browseLimit, setBrowseLimit] = useState(BROWSE_PAGE_SIZE);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const genreInputRef = useRef<HTMLInputElement>(null);
  const hasQuery = normaliseSearch(query).length > 0;
  const results = useMemo(() => searchSongs(songs, query), [query, songs]);
  const genres = useMemo(() => getGenreOptions(songs), [songs]);
  const filteredGenres = useMemo(() => {
    const term = normaliseSearch(genreQuery);
    return term ? genres.filter((option) => normaliseSearch(option.name).includes(term)) : genres;
  }, [genreQuery, genres]);
  const browsedSongs = useMemo(
    () => genre ? browseSongs(songs, genre, sortBy) : [],
    [genre, songs, sortBy],
  );
  const visibleBrowseSongs = browsedSongs.slice(0, browseLimit);

  const choose = (song: Song) => {
    onSelect(song);
    setQuery("");
    setOpen(false);
  };

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  return (
    <div className="search-wrap" id="song-picker" ref={wrapperRef}>
      <p className="finder-label">Choose how to find a starting song</p>
      <div className="finder-tabs" role="tablist" aria-label="Find a starting song">
        <button
          className={!browsing ? "active" : ""}
          role="tab"
          aria-selected={!browsing}
          aria-controls="song-finder-panel"
          onClick={() => {
            setBrowsing(false);
            setOpen(true);
            window.requestAnimationFrame(() => inputRef.current?.focus());
          }}
        >
          <Search size={17} /> Search songs
        </button>
        <button
          className={browsing ? "active" : ""}
          role="tab"
          aria-selected={browsing}
          aria-controls="song-finder-panel"
          onClick={() => {
            setQuery("");
            setBrowsing(true);
            setOpen(true);
            if (!genre) window.requestAnimationFrame(() => genreInputRef.current?.focus());
          }}
        >
          <Library size={17} /> Browse genres
        </button>
      </div>

      <div className="search-panel" id="song-finder-panel" role="tabpanel">
        {browsing ? (
          genre ? (
            <div className={`genre-selection-box ${open ? "genre-selection-box--open" : ""}`}>
              <button
                className="genre-selection-box__current"
                onClick={() => setOpen(true)}
                aria-label={`Show ${genreLabel(genre)} songs`}
                aria-expanded={open}
              >
                <Library size={21} aria-hidden="true" />
                <span><small>Selected genre</small><strong>{genreLabel(genre)}</strong></span>
              </button>
              <button
                className="genre-selection-box__change"
                onClick={() => {
                  setGenre("");
                  setGenreQuery("");
                  setOpen(true);
                  window.requestAnimationFrame(() => genreInputRef.current?.focus());
                }}
              >
                Change genre
              </button>
            </div>
          ) : (
            <div className={`search-box genre-search-box ${open ? "search-box--open" : ""}`}>
              <Library size={21} aria-hidden="true" />
              <input
                ref={genreInputRef}
                value={genreQuery}
                onChange={(event) => {
                  setGenreQuery(event.target.value);
                  setOpen(true);
                }}
                onFocus={() => setOpen(true)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setOpen(false);
                }}
                placeholder="Search genres"
                aria-label="Search genres"
                aria-expanded={open}
                aria-controls="genre-options"
                aria-autocomplete="list"
              />
              {genreQuery ? (
                <button className="icon-button" onClick={() => setGenreQuery("")} aria-label="Clear genre search">
                  <X size={18} />
                </button>
              ) : (
                <span className="genre-search-box__hint">Choose one</span>
              )}
            </div>
          )
        ) : (
          <div className={`search-box ${open ? "search-box--open" : ""}`}>
            <Search size={21} aria-hidden="true" />
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setOpen(true);
              }}
              onFocus={() => setOpen(true)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && hasQuery) {
                  event.preventDefault();
                  setOpen(false);
                  onSearch(query.trim());
                }
                if (event.key === "Escape") setOpen(false);
              }}
              enterKeyHint="search"
              placeholder="Search a song or artist"
              aria-label="Search a song or artist"
              aria-expanded={open}
            />
            {query ? (
              <button className="icon-button" onClick={() => setQuery("")} aria-label="Clear search">
                <X size={18} />
              </button>
            ) : (
              <span className="key-hint">⌘ K</span>
            )}
          </div>
        )}

        {open && (
          <div className={`search-results ${browsing ? "search-results--browse" : ""}`}>
            {browsing ? (
              genre ? (
                <>
                  <div className="browse-heading">
                    <div><span>{genreLabel(genre)} catalog</span><strong>{browsedSongs.length.toLocaleString()} {browsedSongs.length === 1 ? "song" : "songs"}</strong></div>
                    <button className="browse-close" onClick={() => setOpen(false)} aria-label="Close catalog browser"><X size={18} /></button>
                  </div>
                  <div className="browse-controls browse-controls--songs">
                    <div className="browse-sort">
                      <span>Order songs by</span>
                      <div role="group" aria-label="Order songs by">
                        <button className={sortBy === "title" ? "active" : ""} aria-pressed={sortBy === "title"} onClick={() => { setSortBy("title"); setBrowseLimit(BROWSE_PAGE_SIZE); }}>Song title</button>
                        <button className={sortBy === "artist" ? "active" : ""} aria-pressed={sortBy === "artist"} onClick={() => { setSortBy("artist"); setBrowseLimit(BROWSE_PAGE_SIZE); }}>Artist name</button>
                      </div>
                    </div>
                  </div>
                  <div className="browse-list scroll-region" role="listbox" aria-label={`${genreLabel(genre)} songs`}>
                    {visibleBrowseSongs.map((song) => (
                      <SongResult key={song.id} song={song} onSelect={choose} />
                    ))}
                    {visibleBrowseSongs.length < browsedSongs.length && (
                      <button className="browse-more" onClick={() => setBrowseLimit((current) => current + BROWSE_PAGE_SIZE)}>
                        Show {Math.min(BROWSE_PAGE_SIZE, browsedSongs.length - visibleBrowseSongs.length)} more
                      </button>
                    )}
                  </div>
                </>
              ) : (
                <>
                  <div className="browse-heading">
                    <div><span>Browse the catalog</span><strong>{filteredGenres.length.toLocaleString()} {filteredGenres.length === 1 ? "genre" : "genres"}</strong></div>
                    <button className="browse-close" onClick={() => setOpen(false)} aria-label="Close genre browser"><X size={18} /></button>
                  </div>
                  {filteredGenres.length ? (
                    <div className="genre-list scroll-region" id="genre-options" role="listbox" aria-label="Genres">
                      {filteredGenres.map((option) => (
                        <button
                          className="genre-option"
                          key={option.name}
                          role="option"
                          aria-selected="false"
                          onClick={() => {
                            setGenre(option.name);
                            setGenreQuery("");
                            setBrowseLimit(BROWSE_PAGE_SIZE);
                          }}
                        >
                          <strong>{genreLabel(option.name)}</strong>
                          <span>{option.count.toLocaleString()} {option.count === 1 ? "song" : "songs"}</span>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="empty-search">No genres match “{genreQuery}”.</div>
                  )}
                </>
              )
            ) : (
              <>
                <p className="search-results__label">{hasQuery ? "Best matches" : "Familiar songs"}</p>
                <div
                  className="search-results__list scroll-region"
                  role="listbox"
                  aria-label="Search results"
                >
                  {results.length ? results.map((song) => (
                    <SongResult key={song.id} song={song} onSelect={choose} />
                  )) : (
                    <div className="empty-search">No direct match. Try browsing by genre instead.</div>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </div>

      <div className="search-assists">
        <span>Popular:</span>
        {songs.slice(0, 2).map((song) => <button key={song.id} className="quick-pick" onClick={() => choose(song)}>{song.title}</button>)}
      </div>
    </div>
  );
}

function CatalogSearchResults({
  sectionRef,
  songs,
  query,
  expanded,
  onSelect,
  onClose,
}: {
  sectionRef: React.RefObject<HTMLElement | null>;
  songs: PreparedSong[];
  query: string | null;
  expanded: boolean;
  onSelect: (song: Song) => void;
  onClose: () => void;
}) {
  const [visibleLimit, setVisibleLimit] = useState(SEARCH_PAGE_SIZE);
  const results = useMemo(
    () => query ? searchSongs(songs, query, songs.length) : [],
    [query, songs],
  );
  const visibleResults = results.slice(0, visibleLimit);

  useEffect(() => setVisibleLimit(SEARCH_PAGE_SIZE), [query]);

  const loadMore = (event: React.UIEvent<HTMLDivElement>) => {
    const list = event.currentTarget;
    if (list.scrollHeight - list.scrollTop - list.clientHeight > 120) return;
    setVisibleLimit((current) => Math.min(current + SEARCH_PAGE_SIZE, results.length));
  };

  return (
    <section
      ref={sectionRef}
      className={`catalog-search-results ${expanded ? "catalog-search-results--open" : ""}`}
      id="catalog-search-results"
      aria-hidden={!expanded}
      inert={!expanded}
    >
      <div className="catalog-search-results__reveal">
        {query && (
          <div className="catalog-search-results__panel">
            <div className="catalog-search-results__heading">
              <div>
                <p className="eyebrow"><Search size={14} /> Catalog search</p>
                <h2>Results for <span>“{query}”</span></h2>
                <p>{results.length.toLocaleString()} {results.length === 1 ? "song" : "songs"} found. Select one to use it as your starting song.</p>
              </div>
              <button onClick={onClose} aria-label="Close search results"><X size={19} /></button>
            </div>

            {results.length ? (
              <div
                className="catalog-search-results__list scroll-region"
                role="listbox"
                aria-label={`All results for ${query}`}
                onScroll={loadMore}
              >
                {visibleResults.map((song) => <SongResult key={song.id} song={song} onSelect={onSelect} />)}
              </div>
            ) : (
              <div className="catalog-search-results__empty"><Music2 size={24} /><strong>No songs found</strong><span>Try a different title, artist, or genre.</span></div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function CountIn({ bpm }: { bpm: number }) {
  const [playing, setPlaying] = useState(false);
  const [beat, setBeat] = useState(0);
  const audioRef = useRef<AudioContext | null>(null);

  const click = (accent = false) => {
    const context = audioRef.current ?? new window.AudioContext();
    audioRef.current = context;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = accent ? 980 : 720;
    gain.gain.setValueAtTime(0.0001, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.13, context.currentTime + 0.005);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.07);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.075);
  };

  useEffect(() => {
    if (!playing) return;
    let currentBeat = 1;
    const timer = window.setInterval(() => {
      currentBeat += 1;
      if (currentBeat > 4) {
        window.clearInterval(timer);
        setPlaying(false);
        setBeat(0);
        return;
      }
      setBeat(currentBeat);
      click(false);
    }, 60_000 / bpm);
    return () => window.clearInterval(timer);
  }, [playing, bpm]);

  const start = () => {
    if (playing) {
      setPlaying(false);
      setBeat(0);
      return;
    }
    setBeat(1);
    click(true);
    setPlaying(true);
  };

  return (
    <button className="count-in" onClick={start} aria-label={playing ? "Stop count in" : "Play count in"}>
      <span className={`count-in__icon ${playing ? "count-in__icon--playing" : ""}`}>
        {playing ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}
      </span>
      <span>
        <strong>{playing ? `Beat ${beat}` : "Hear the count-in"}</strong>
        <small>4 beats at an estimated {bpm.toFixed(1)} BPM</small>
      </span>
      <span className="beat-dots" aria-hidden="true">
        {[1, 2, 3, 4].map((dot) => <i key={dot} className={dot === beat ? "active" : ""} />)}
      </span>
    </button>
  );
}

function MatchCard({
  match,
  index,
  saved,
  onToggle,
  band,
}: {
  match: SongMatch;
  index: number;
  saved: boolean;
  onToggle: () => void;
  band: MatchBand;
}) {
  const difference = match.difference;
  const tempoLabel = Math.abs(difference) <= 0.5
    ? "Exact band"
    : `${difference > 0 ? "+" : ""}${difference.toFixed(1)}% tempo`;
  const threshold = MATCH_THRESHOLDS[band];
  const score = Math.round(
    70 * Math.max(0, 1 - Math.abs(difference) / Math.max(threshold, 0.5)) + 30 * match.song.tempoQuality,
  );

  return (
    <article className="match-card" style={{ "--delay": `${Math.min(index, 8) * 70}ms` } as React.CSSProperties}>
      <div className="match-card__rank">{String(index + 1).padStart(2, "0")}</div>
      <Artwork song={match.song} />
      <div className="match-card__body">
        <div className="match-card__heading">
          <div><h3>{match.song.title}</h3><p>{match.song.artist}</p></div>
          <div className="score-ring" style={{ "--score": `${score * 3.6}deg` } as React.CSSProperties}>
            <span>{score}</span>
          </div>
        </div>

        <div className="match-card__stats">
          <div><span className="stat-label">Estimated tempo</span><strong>{match.song.bpm.toFixed(1)} <small>BPM</small></strong></div>
          <div><span className="stat-label">Tempo quality</span><strong>{Math.round(match.song.tempoQuality * 100)}%</strong></div>
          <div><span className="stat-label">Listener rank</span><strong>#{match.song.listenerRank.toLocaleString()}</strong></div>
        </div>

        <div className="match-card__tags">
          <span className="tag tag--tempo"><Check size={13} /> {tempoLabel}</span>
          {match.song.genres[0] && <span className="tag">{genreLabel(match.song.genres[0])}</span>}
          <span className="tag"><Music2 size={13} /> Automatic estimate</span>
        </div>

        <button className="try-button" onClick={onToggle}>
          {saved ? <Check size={17} /> : <WandSparkles size={17} />}
          {saved ? "Added to your set" : "Try this match"}
          {!saved && <ArrowRight size={17} />}
        </button>
      </div>
    </article>
  );
}

function MySetDrawer({
  songs,
  open,
  onClose,
  onRemove,
  onContinue,
}: {
  songs: Song[];
  open: boolean;
  onClose: () => void;
  onRemove: (songId: string) => void;
  onContinue: () => void;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    const focusFrame = window.requestAnimationFrame(() => closeButtonRef.current?.focus());
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };

    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
      previouslyFocused?.focus();
    };
  }, [open, onClose]);

  const keepFocusInDrawer = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Tab") return;
    const focusable = panelRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className={`set-drawer ${open ? "set-drawer--open" : ""}`}
      aria-hidden={!open}
      inert={!open}
    >
      <div className="set-drawer__backdrop" onClick={onClose} />
      <aside
        className="set-drawer__panel"
        id="my-set-drawer"
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="my-set-title"
        onKeyDown={keepFocusInDrawer}
      >
        <header className="set-drawer__header">
          <div>
            <p className="eyebrow"><Headphones size={14} /> Your selections</p>
            <h2 id="my-set-title">My set <span>{songs.length}</span></h2>
            <p>{songs.length ? `${songs.length} ${songs.length === 1 ? "song" : "songs"} ready to try together.` : "Build a shortlist of tempo matches to try."}</p>
          </div>
          <button ref={closeButtonRef} className="set-drawer__close" onClick={onClose} aria-label="Close my set">
            <X size={20} />
          </button>
        </header>

        <div className="set-drawer__body scroll-region">
          {songs.length ? (
            <ul className="set-list">
              {songs.map((song, index) => (
                <li className="set-list__item" key={song.id}>
                  <span className="set-list__number">{String(index + 1).padStart(2, "0")}</span>
                  <Artwork song={song} size="small" />
                  <div className="set-list__song">
                    <strong>{song.title}</strong>
                    <span>{song.artist}</span>
                  </div>
                  <div className="set-list__tempo">
                    <strong>{song.bpm.toFixed(1)}</strong>
                    <span>EST. BPM</span>
                  </div>
                  <button onClick={() => onRemove(song.id)} aria-label={`Remove ${song.title} by ${song.artist} from my set`}>
                    <Trash2 size={16} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="set-drawer__empty">
              <span><Headphones size={27} /></span>
              <h3>Your set is waiting</h3>
              <p>Add a tempo match and it’ll appear here for easy comparison.</p>
            </div>
          )}
        </div>

        <footer className="set-drawer__footer">
          <div><span>Set length</span><strong>{songs.length} {songs.length === 1 ? "track" : "tracks"}</strong></div>
          <button onClick={onContinue}>{songs.length ? "Keep discovering" : "Browse matches"} <ArrowRight size={17} /></button>
        </footer>
      </aside>
    </div>
  );
}

function CatalogApp({ songs }: { songs: PreparedSong[] }) {
  const [source, setSource] = useState<Song | null>(null);
  const [band, setBand] = useState<MatchBand>("exact");
  const [showAll, setShowAll] = useState(false);
  const [matchLimit, setMatchLimit] = useState(MATCH_PREVIEW_SIZE + MATCH_PAGE_SIZE);
  const [saved, setSaved] = useState<Set<string>>(() => new Set());
  const [setOpen, setSetOpen] = useState(false);
  const [submittedQuery, setSubmittedQuery] = useState<string | null>(null);
  const [searchExpanded, setSearchExpanded] = useState(false);
  const resultsRef = useRef<HTMLElement>(null);
  const matchResultsRef = useRef<HTMLDivElement>(null);
  const searchResultsRef = useRef<HTMLElement>(null);
  const searchCloseTimerRef = useRef<number | null>(null);
  const cancelSearchScrollRef = useRef<(() => void) | null>(null);
  const matches = useMemo(() => source ? getMatches(songs, source, band) : [], [songs, source, band]);
  const songsById = useMemo(() => new Map(songs.map((song) => [song.id, song])), [songs]);
  const savedSongs = useMemo(
    () => Array.from(saved, (songId) => songsById.get(songId)).filter((song): song is PreparedSong => Boolean(song)),
    [saved, songsById],
  );
  const previewMatches = matches.slice(0, MATCH_PREVIEW_SIZE);
  const additionalMatches = matches.slice(MATCH_PREVIEW_SIZE, matchLimit);

  useEffect(() => {
    setShowAll(false);
    setMatchLimit(MATCH_PREVIEW_SIZE + MATCH_PAGE_SIZE);
    matchResultsRef.current?.scrollTo({ top: 0 });
  }, [source, band]);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.querySelector<HTMLInputElement>(".search-box input")?.focus();
      }
    };
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);
  useEffect(() => () => {
    if (searchCloseTimerRef.current !== null) window.clearTimeout(searchCloseTimerRef.current);
    cancelSearchScrollRef.current?.();
  }, []);

  const collapseSearchResults = () => {
    setSearchExpanded(false);
    cancelSearchScrollRef.current?.();
    if (searchCloseTimerRef.current !== null) window.clearTimeout(searchCloseTimerRef.current);
    searchCloseTimerRef.current = window.setTimeout(() => {
      setSubmittedQuery(null);
      searchCloseTimerRef.current = null;
    }, SEARCH_RESULTS_TRANSITION_MS);
  };

  const chooseSong = (song: Song) => {
    const collapseDelay = searchExpanded ? SEARCH_RESULTS_TRANSITION_MS : 0;
    collapseSearchResults();
    setSource(song);
    window.setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), collapseDelay + 100);
  };
  const showSearchResults = (query: string) => {
    if (searchCloseTimerRef.current !== null) {
      window.clearTimeout(searchCloseTimerRef.current);
      searchCloseTimerRef.current = null;
    }
    cancelSearchScrollRef.current?.();
    setSearchExpanded(false);
    setSubmittedQuery(query);
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        setSearchExpanded(true);
        window.requestAnimationFrame(() => {
          if (searchResultsRef.current) cancelSearchScrollRef.current = animateScrollTo(searchResultsRef.current);
        });
      });
    });
  };
  const toggleSaved = (songId: string) => setSaved((current) => {
    const next = new Set(current);
    if (next.has(songId)) next.delete(songId); else next.add(songId);
    return next;
  });
  const closeSet = useCallback(() => setSetOpen(false), []);
  const continueMatching = useCallback(() => {
    setSetOpen(false);
    window.setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
  }, []);
  const loadMoreMatches = (event: React.UIEvent<HTMLDivElement>) => {
    const list = event.currentTarget;
    if (list.scrollHeight - list.scrollTop - list.clientHeight > 160) return;
    setMatchLimit((current) => Math.min(current + MATCH_PAGE_SIZE, matches.length));
  };
  const toggleMatchResults = () => {
    if (showAll) matchResultsRef.current?.scrollTo({ top: 0 });
    setShowAll((current) => !current);
  };

  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="brand" href="#top" aria-label="Beatmatch home"><span className="brand__mark"><span /></span><span>beat<span>match</span></span></a>
        <nav aria-label="Main navigation"><a href="#matches">Discover</a><a href="#how-it-works">How it works</a><a href="#about">About</a></nav>
        <button
          className={`set-button ${setOpen ? "set-button--active" : ""}`}
          onClick={() => setSetOpen(true)}
          aria-expanded={setOpen}
          aria-controls="my-set-drawer"
        >
          <Headphones size={17} /> My set <span>{saved.size}</span>
        </button>
      </header>

      <MySetDrawer
        songs={savedSongs}
        open={setOpen}
        onClose={closeSet}
        onRemove={toggleSaved}
        onContinue={continueMatching}
      />

      <main id="top">
        <section className="hero">
          <div className="hero__glow hero__glow--one" /><div className="hero__glow hero__glow--two" />
          <div className="hero__copy">
            <p className="eyebrow"><Sparkles size={14} /> Find songs on the same beat</p>
            <h1>Your next song<br />is already <em>in time.</em></h1>
            <p className="hero__intro">Search or browse {songs.length.toLocaleString()} recordings by genre. We’ll find familiar songs with a similar estimated tempo—no audio, lyrics, or artwork required.</p>
            <SearchBox songs={songs} onSelect={chooseSong} onSearch={showSearchResults} />
          </div>

          <div className="hero__visual" aria-label="Song matching illustration">
            <div className="vinyl vinyl--back"><div className="vinyl__label" /></div>
            <div className="source-tile">
              <p>{source ? "Selected recording" : "Start here"}</p>
              {source ? <Artwork song={source} /> : <div className="artwork artwork--large artwork--empty" aria-hidden="true"><Music2 size={24} /></div>}
              <div><strong>{source?.title ?? "Choose a song"}</strong><span>{source?.artist ?? "Search or browse the catalog"}</span></div>
              <div className={`source-tile__bpm ${source ? "" : "source-tile__bpm--empty"}`}><b>{source ? source.bpm.toFixed(1) : "—"}</b><small>EST. BPM</small></div>
            </div>
            <div className="tempo-line"><span /><i>1</i><i>2</i><i>3</i><i>4</i><span /></div>
            <div className="surprise-tile"><span className="surprise-tile__spark">✦</span><div><small>{source ? "Closest tempo" : "Then discover"}</small><strong>{source ? matches[0]?.song.title ?? "Widen the range…" : "Songs on the same beat"}</strong><span>{source ? matches[0]?.song.artist : "Your matches will appear after you choose"}</span></div><div className="surprise-tile__match"><b>{matches[0] ? `${Math.abs(matches[0].difference).toFixed(1)}%` : "—"}</b><small>apart</small></div></div>
            <div className="floating-note floating-note--one">♪</div><div className="floating-note floating-note--two">♫</div>
          </div>
        </section>

        <CatalogSearchResults
          sectionRef={searchResultsRef}
          songs={songs}
          query={submittedQuery}
          expanded={searchExpanded}
          onSelect={chooseSong}
          onClose={collapseSearchResults}
        />

        {source && <section className="match-section" id="matches" ref={resultsRef}>
          <div className="section-heading"><div><p className="eyebrow">Matched to your song</p><h2>Songs near <span>{source.bpm.toFixed(1)} estimated BPM</span></h2></div><button className="change-song" onClick={() => document.querySelector<HTMLInputElement>(".search-box input")?.focus()}><Search size={16} /> Change song</button></div>
          <div className="source-summary">
            <Artwork song={source} size="small" /><div className="source-summary__title"><small>Your base song</small><strong>{source.title} <span>· {source.artist}</span></strong></div>
            <div className="source-summary__fact"><small>Estimated tempo</small><strong>{source.bpm.toFixed(1)} BPM</strong></div>
            <div className="source-summary__fact"><small>Tempo quality</small><strong>{Math.round(source.tempoQuality * 100)}%</strong></div>
            <CountIn bpm={source.bpm} />
          </div>

          <div className="match-toolbar">
            <div className="segmented" aria-label="Tempo matching band">
              <button className={band === "exact" ? "active" : ""} onClick={() => setBand("exact")}>Exact <span>±0.5%</span></button>
              <button className={band === "close" ? "active" : ""} onClick={() => setBand("close")}>Flexible <span>±2%</span></button>
              <button className={band === "exploratory" ? "active" : ""} onClick={() => setBand("exploratory")}>Explore <span>±5%</span></button>
            </div>
          </div>
          <div className="results-meta"><p><strong>{matches.length}</strong> tempo matches in {songs.length.toLocaleString()} songs</p><span><CircleHelp size={14} /> Sorted by BPM difference, quality, then familiarity</span></div>

          {previewMatches.length ? (
            <div
              ref={matchResultsRef}
              className={`match-results scroll-region ${showAll ? "match-results--open" : ""}`}
              id="tempo-match-results"
              role="region"
              aria-label="Tempo matches"
              onScroll={loadMoreMatches}
            >
              <div className="match-grid">
                {previewMatches.map((match, index) => <MatchCard key={match.song.id} match={match} index={index} saved={saved.has(match.song.id)} onToggle={() => toggleSaved(match.song.id)} band={band} />)}
              </div>
              {matches.length > MATCH_PREVIEW_SIZE && (
                <div
                  className={`match-results-more ${showAll ? "match-results-more--open" : ""}`}
                  aria-hidden={!showAll}
                  inert={!showAll}
                >
                  <div className="match-results-more__reveal">
                    <div className="match-grid">
                      {additionalMatches.map((match, index) => (
                        <MatchCard
                          key={match.song.id}
                          match={match}
                          index={index + MATCH_PREVIEW_SIZE}
                          saved={saved.has(match.song.id)}
                          onToggle={() => toggleSaved(match.song.id)}
                          band={band}
                        />
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : <div className="no-matches"><Music2 size={28} /><h3>No songs in this tempo band</h3><p>Widen the range or choose another base song.</p></div>}
          {matches.length > MATCH_PREVIEW_SIZE && (
            <button
              className="show-more"
              aria-expanded={showAll}
              aria-controls="tempo-match-results"
              onClick={toggleMatchResults}
            >
              {showAll ? "Show fewer matches" : `Show ${matches.length - MATCH_PREVIEW_SIZE} more matches`}
              <ArrowRight size={16} />
            </button>
          )}
        </section>}

        <section className="how-section" id="how-it-works">
          <div className="how-section__intro"><p className="eyebrow">How it works</p><h2>One catalog.<br />Three tempo bands.</h2><p>Every BPM is an automatic estimate. Similar tempo is a useful starting point, not a promise that two songs will work musically.</p></div>
          <div className="steps"><article><span>01</span><Search size={21} /><h3>Pick a recording</h3><p>Search directly, or browse a genre ordered by song or artist.</p></article><article><span>02</span><SlidersHorizontal size={21} /><h3>Choose a range</h3><p>Compare exact, flexible, or exploratory BPM bands.</p></article><article><span>03</span><WandSparkles size={21} /><h3>Try the timing</h3><p>Use the count-in, then decide with your own ears.</p></article></div>
        </section>
      </main>

      <footer id="about">
        <a className="brand" href="#top"><span className="brand__mark"><span /></span><span>beat<span>match</span></span></a>
        <p>Estimated tempo data from <a href="https://acousticbrainz.org/download">AcousticBrainz</a>, identity from <a href="https://musicbrainz.org/">MusicBrainz</a>, and familiarity from <a href="https://listenbrainz.org/">ListenBrainz</a>.</p>
        <span>No lyrics, audio, or provider media hosted</span>
      </footer>
    </div>
  );
}

export function App() {
  const [songs, setSongs] = useState<PreparedSong[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setSongs(null);
    setError(null);
    loadCatalog(controller.signal)
      .then(setSongs)
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "Unknown catalog error.");
      });
    return () => controller.abort();
  }, [attempt]);

  if (error) {
    return <main className="catalog-state" role="alert"><Music2 size={34} /><h1>Catalog unavailable</h1><p>Beatmatch couldn’t load its static song catalog. {error}</p><button onClick={() => setAttempt((value) => value + 1)}><RefreshCw size={16} /> Try again</button></main>;
  }
  if (!songs) return <main className="catalog-state" aria-live="polite"><span className="catalog-state__spinner" /><h1>Loading song catalog…</h1></main>;
  return <CatalogApp songs={songs} />;
}
