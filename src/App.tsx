import { useEffect, useMemo, useRef, useState } from "react";
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
  searchSongs,
  type BrowseSort,
  type MatchBand,
  type PreparedSong,
  type Song,
  type SongMatch,
} from "./data";

const BROWSE_PAGE_SIZE = 80;

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

function SearchBox({ songs, onSelect }: { songs: PreparedSong[]; onSelect: (song: Song) => void }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [genre, setGenre] = useState("");
  const [sortBy, setSortBy] = useState<BrowseSort>("title");
  const [browseLimit, setBrowseLimit] = useState(BROWSE_PAGE_SIZE);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const results = useMemo(() => searchSongs(songs, query), [query, songs]);
  const genres = useMemo(() => getGenreOptions(songs), [songs]);
  const browsedSongs = useMemo(() => browseSongs(songs, genre, sortBy), [genre, songs, sortBy]);
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
    <div className="search-wrap" ref={wrapperRef}>
      <div className={`search-box ${open ? "search-box--open" : ""}`}>
        <Search size={21} aria-hidden="true" />
        <input
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setBrowsing(false);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && results[0]) {
              choose(results[0]);
            }
            if (event.key === "Escape") setOpen(false);
          }}
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

      {open && (
        <div className={`search-results ${browsing ? "search-results--browse" : ""}`}>
          {browsing ? (
            <>
              <div className="browse-heading">
                <div><span>Browse the catalog</span><strong>{browsedSongs.length.toLocaleString()} songs</strong></div>
                <button className="browse-close" onClick={() => setOpen(false)} aria-label="Close catalog browser"><X size={18} /></button>
              </div>
              <div className="browse-controls">
                <label>
                  <span>Genre</span>
                  <select
                    aria-label="Browse by genre"
                    value={genre}
                    onChange={(event) => {
                      setGenre(event.target.value);
                      setBrowseLimit(BROWSE_PAGE_SIZE);
                    }}
                  >
                    <option value="">All genres</option>
                    {genres.map((option) => <option key={option.name} value={option.name}>{genreLabel(option.name)} ({option.count.toLocaleString()})</option>)}
                  </select>
                </label>
                <div className="browse-sort">
                  <span>Order by</span>
                  <div role="group" aria-label="Order songs by">
                    <button className={sortBy === "title" ? "active" : ""} aria-pressed={sortBy === "title"} onClick={() => { setSortBy("title"); setBrowseLimit(BROWSE_PAGE_SIZE); }}>Song title</button>
                    <button className={sortBy === "artist" ? "active" : ""} aria-pressed={sortBy === "artist"} onClick={() => { setSortBy("artist"); setBrowseLimit(BROWSE_PAGE_SIZE); }}>Artist</button>
                  </div>
                </div>
              </div>
              <div className="browse-list" role="listbox" aria-label={genre ? `${genreLabel(genre)} songs` : "All songs"}>
                {visibleBrowseSongs.map((song) => (
                  <button key={song.id} className="search-result" onClick={() => choose(song)} role="option">
                    <Artwork song={song} size="small" />
                    <span className="search-result__copy">
                      <strong>{song.title}</strong>
                      <small>{song.artist}{song.genres[0] && <span className="search-result__genre">{genreLabel(song.genres[0])}</span>}</small>
                    </span>
                    <span className="search-result__bpm">~{song.bpm.toFixed(1)} BPM</span>
                  </button>
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
              <p className="search-results__label">{query ? "Best matches" : "Familiar songs"}</p>
              <div role="listbox" aria-label="Search results">
                {results.length ? results.map((song) => (
                  <button key={song.id} className="search-result" onClick={() => choose(song)} role="option">
                    <Artwork song={song} size="small" />
                    <span className="search-result__copy">
                      <strong>{song.title}</strong>
                      <small>{song.artist}{song.genres[0] && <span className="search-result__genre">{genreLabel(song.genres[0])}</span>}</small>
                    </span>
                    <span className="search-result__bpm">~{song.bpm.toFixed(1)} BPM</span>
                  </button>
                )) : (
                  <div className="empty-search">No direct match. Try browsing by genre instead.</div>
                )}
              </div>
            </>
          )}
        </div>
      )}

      <div className="search-assists">
        <button
          className="browse-button"
          aria-expanded={open && browsing}
          onClick={() => {
            setQuery("");
            setBrowsing(true);
            setOpen(true);
          }}
        >
          <Library size={14} /> Browse {songs.length.toLocaleString()} songs by genre
        </button>
        <span>or try</span>
        {songs.slice(0, 2).map((song) => <button key={song.id} className="quick-pick" onClick={() => choose(song)}>{song.title}</button>)}
      </div>
    </div>
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
    <article className="match-card" style={{ "--delay": `${index * 70}ms` } as React.CSSProperties}>
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

function CatalogApp({ songs }: { songs: PreparedSong[] }) {
  const [source, setSource] = useState<Song>(songs[0]);
  const [band, setBand] = useState<MatchBand>("exact");
  const [showAll, setShowAll] = useState(false);
  const [saved, setSaved] = useState<Set<string>>(() => new Set());
  const resultsRef = useRef<HTMLElement>(null);
  const matches = useMemo(() => getMatches(songs, source, band), [songs, source, band]);
  const visibleMatches = showAll ? matches : matches.slice(0, 3);

  useEffect(() => setShowAll(false), [source, band]);
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

  const chooseSong = (song: Song) => {
    setSource(song);
    window.setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
  };
  const toggleSaved = (songId: string) => setSaved((current) => {
    const next = new Set(current);
    if (next.has(songId)) next.delete(songId); else next.add(songId);
    return next;
  });

  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="brand" href="#top" aria-label="Beatmatch home"><span className="brand__mark"><span /></span><span>beat<span>match</span></span></a>
        <nav aria-label="Main navigation"><a href="#matches">Discover</a><a href="#how-it-works">How it works</a><a href="#about">About</a></nav>
        <button className="set-button"><Headphones size={17} /> My set <span>{saved.size}</span></button>
      </header>

      <main id="top">
        <section className="hero">
          <div className="hero__glow hero__glow--one" /><div className="hero__glow hero__glow--two" />
          <div className="hero__copy">
            <p className="eyebrow"><Sparkles size={14} /> Find songs on the same beat</p>
            <h1>Your next song<br />is already <em>in time.</em></h1>
            <p className="hero__intro">Search or browse {songs.length.toLocaleString()} recordings by genre. We’ll find familiar songs with a similar estimated tempo—no audio, lyrics, or artwork required.</p>
            <SearchBox songs={songs} onSelect={chooseSong} />
          </div>

          <div className="hero__visual" aria-label="Song matching illustration">
            <div className="vinyl vinyl--back"><div className="vinyl__label" /></div>
            <div className="source-tile"><p>Selected recording</p><Artwork song={source} /><div><strong>{source.title}</strong><span>{source.artist}</span></div><div className="source-tile__bpm"><b>{source.bpm.toFixed(1)}</b><small>EST. BPM</small></div></div>
            <div className="tempo-line"><span /><i>1</i><i>2</i><i>3</i><i>4</i><span /></div>
            <div className="surprise-tile"><span className="surprise-tile__spark">✦</span><div><small>Closest tempo</small><strong>{matches[0]?.song.title ?? "Widen the range…"}</strong><span>{matches[0]?.song.artist}</span></div><div className="surprise-tile__match"><b>{matches[0] ? `${Math.abs(matches[0].difference).toFixed(1)}%` : "—"}</b><small>apart</small></div></div>
            <div className="floating-note floating-note--one">♪</div><div className="floating-note floating-note--two">♫</div>
          </div>
        </section>

        <section className="match-section" id="matches" ref={resultsRef}>
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

          {visibleMatches.length ? <div className="match-grid">{visibleMatches.map((match, index) => <MatchCard key={match.song.id} match={match} index={index} saved={saved.has(match.song.id)} onToggle={() => toggleSaved(match.song.id)} band={band} />)}</div> : <div className="no-matches"><Music2 size={28} /><h3>No songs in this tempo band</h3><p>Widen the range or choose another base song.</p></div>}
          {!showAll && matches.length > 3 && <button className="show-more" onClick={() => setShowAll(true)}>Show {matches.length - 3} more matches <ArrowRight size={16} /></button>}
        </section>

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
