from __future__ import annotations

import argparse
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, ROOT, Paths, load_config
from .db import DEFAULT_MEMORY_LIMIT


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="python -m song_pick",
        description="Build the static Beatmatch BPM catalog.",
    )
    result.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    result.add_argument("--root", type=Path, default=ROOT)
    commands = result.add_subparsers(dest="command", required=True)

    download = commands.add_parser("download", help="download and verify pinned source archives")
    download.add_argument(
        "--source",
        action="append",
        help="download only a named source (repeatable; the default downloads all sources)",
    )

    build = commands.add_parser("build", help="run the resumable import, score, selection, and export pipeline")
    build.add_argument("--allow-under-target", action="store_true")
    build.add_argument("--refresh-popularity", action="store_true")
    build.add_argument("--force", action="store_true", help="rerun completed import and scoring stages")
    build.add_argument(
        "--threads",
        type=positive_integer,
        default=1,
        metavar="N",
        help="maximum DuckDB worker threads (default: 1; increase for a faster, more CPU-intensive build)",
    )
    build.add_argument(
        "--memory-limit",
        default=DEFAULT_MEMORY_LIMIT,
        metavar="SIZE",
        help=f"maximum DuckDB memory, such as 2GB or 512MB (default: {DEFAULT_MEMORY_LIMIT})",
    )

    export = commands.add_parser("export", help="re-export the selected catalog from DuckDB")
    export.add_argument("--allow-under-target", action="store_true")

    validate = commands.add_parser("validate", help="validate the exported static catalog")
    validate.add_argument("--allow-under-target", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    config = load_config(arguments.config)
    paths = Paths.under(arguments.root.resolve())
    paths.create()

    if arguments.command == "download":
        from .download import download_all

        download_all(config, paths, arguments.source)
        return 0

    from .db import connect

    db = connect(
        paths.database,
        threads=getattr(arguments, "threads", 1),
        memory_limit=getattr(arguments, "memory_limit", DEFAULT_MEMORY_LIMIT),
    )
    try:
        if arguments.command == "build":
            from .export_catalog import export_catalog
            from .fetch_popularity import fetch_popularity
            from .import_acousticbrainz import import_acousticbrainz
            from .import_musicbrainz import import_canonical_data, import_core_data
            from .pipeline import run_stage
            from .score_tempos import score_canonical_tempos, score_source_tempos
            from .select_catalog import select_catalog
            from .validate_catalog import validate_catalog

            force = bool(arguments.force)
            run_stage(db, config, "import-acousticbrainz", lambda: import_acousticbrainz(db, config, paths), force)
            run_stage(db, config, "score-source-tempos", lambda: score_source_tempos(db, config), force)
            run_stage(db, config, "import-musicbrainz-canonical", lambda: import_canonical_data(db, config, paths), force)
            run_stage(db, config, "import-musicbrainz-core", lambda: import_core_data(db, config, paths), force)
            run_stage(db, config, "score-canonical-tempos", lambda: score_canonical_tempos(db, config), force)
            fetch_popularity(db, config, refresh=arguments.refresh_popularity)
            # Popularity includes both canonical and exact source IDs. Re-score so
            # provenance selection uses source familiarity as well as tempo quality.
            score_canonical_tempos(db, config)
            select_catalog(db, config, arguments.allow_under_target)
            export_catalog(db, config, paths)
            validate_catalog(config, paths, db, arguments.allow_under_target)
        elif arguments.command == "export":
            from .export_catalog import export_catalog
            from .validate_catalog import validate_catalog

            export_catalog(db, config, paths)
            validate_catalog(config, paths, db, arguments.allow_under_target)
        elif arguments.command == "validate":
            from .validate_catalog import validate_catalog

            validate_catalog(config, paths, db, arguments.allow_under_target)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
