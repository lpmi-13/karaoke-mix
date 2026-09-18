from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .config import Config, Paths, Source
from .network import open_source_url as urlopen


CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _verified(path: Path, expected: str, expected_size: int) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == expected_size
        and sha256_file(path) == expected.lower()
    )


def _source_destination(source: Source, paths: Paths) -> Path:
    raw = paths.raw.resolve()
    destination = (raw / source.filename).resolve()
    if not destination.is_relative_to(raw):
        raise ValueError(f"source filename escapes the raw data directory: {source.filename}")
    return destination


def download_source(source: Source, paths: Paths, retries: int = 5) -> Path:
    destination = _source_destination(source, paths)
    partial = destination.with_suffix(destination.suffix + ".part")
    if _verified(destination, source.sha256, source.size):
        return destination
    if _verified(partial, source.sha256, source.size):
        partial.replace(destination)
        return destination
    if destination.exists():
        raise RuntimeError(
            f"existing download has the wrong checksum: {destination}; move it aside and retry"
        )

    for attempt in range(retries + 1):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > source.size:
                raise RuntimeError(
                    f"partial download is larger than the pinned size: {partial}"
                )
            headers = {"User-Agent": "karaoke-mix catalog builder/1.0"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = Request(source.url, headers=headers)
            with urlopen(request, timeout=120) as response:
                status = getattr(response, "status", 200)
                append = offset > 0 and status == 206
                if offset and not append:
                    offset = 0
                mode = "ab" if append else "wb"
                written = offset
                with partial.open(mode) as output:
                    while chunk := response.read(CHUNK_SIZE):
                        written += len(chunk)
                        if written > source.size:
                            raise RuntimeError(
                                f"download for {source.name} exceeded pinned size "
                                f"of {source.size} bytes"
                            )
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            if written != source.size:
                partial.unlink(missing_ok=True)
                if attempt == retries:
                    raise RuntimeError(
                        f"size mismatch for {source.name}: expected {source.size}, got {written}"
                    )
                time.sleep(min(2**attempt, 30))
                continue
            actual = sha256_file(partial)
            if actual != source.sha256.lower():
                partial.unlink()
                if attempt == retries:
                    raise RuntimeError(
                        f"checksum mismatch for {source.name}: expected {source.sha256}, got {actual}"
                    )
                time.sleep(min(2**attempt, 30))
                continue
            partial.replace(destination)
            return destination
        except (HTTPError, URLError, TimeoutError) as error:
            if isinstance(error, HTTPError) and error.code == 416 and partial.exists():
                partial.unlink()
                if attempt < retries:
                    continue
            if attempt == retries:
                raise RuntimeError(f"failed to download {source.url}: {error}") from error
            time.sleep(min(2**attempt, 30))
    raise AssertionError("unreachable")


def download_all(config: Config, paths: Paths, names: list[str] | None = None) -> None:
    paths.create()
    wanted = set(names or [source.name for source in config.sources])
    unknown = wanted.difference(source.name for source in config.sources)
    if unknown:
        raise SystemExit(f"unknown source name(s): {', '.join(sorted(unknown))}")

    records: list[dict[str, object]] = []
    for source in config.sources:
        if source.name not in wanted:
            continue
        path = download_source(source, paths)
        records.append(
            {
                "name": source.name,
                "url": source.url,
                "snapshot": source.snapshot,
                "license": source.license,
                "filename": source.filename,
                "size": source.size,
                "sha256": source.sha256,
            }
        )
        print(f"verified {source.name}: {path}")

    manifest_path = paths.raw / "source-manifest.json"
    existing: list[dict[str, object]] = []
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8") as handle:
            existing = json.load(handle).get("sources", [])
    merged = {str(item["name"]): item for item in existing}
    merged.update({str(item["name"]): item for item in records})
    payload = {"sources": [merged[name] for name in sorted(merged)]}
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
