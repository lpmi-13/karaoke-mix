from __future__ import annotations

import contextlib
import bz2
import lzma
import shutil
import tarfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO


MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024**3
MINIMUM_FREE_BYTES_AFTER_EXTRACTION = 1024**3


@contextlib.contextmanager
def open_tar(path: Path) -> Iterator[tarfile.TarFile]:
    """Open xz or zstd tar archives in streaming mode without unsafe extraction."""
    if path.name.endswith(".tar.xz"):
        with lzma.open(path, "rb") as stream:
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                yield archive
        return
    if path.name.endswith(".tar.bz2"):
        with bz2.open(path, "rb") as stream:
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                yield archive
        return
    if path.name.endswith(".tar.zst"):
        try:
            import zstandard
        except ImportError as error:
            raise SystemExit(
                "zstandard is required to read .tar.zst files; install requirements-catalog.txt"
            ) from error
        with path.open("rb") as compressed:
            decompressor = zstandard.ZstdDecompressor()
            with decompressor.stream_reader(compressed) as stream:
                with tarfile.open(fileobj=stream, mode="r|") as archive:
                    yield archive
        return
    raise ValueError(f"unsupported archive: {path}")


def regular_member_file(archive: tarfile.TarFile, member: tarfile.TarInfo) -> BinaryIO | None:
    if not member.isfile():
        return None
    if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER_BYTES:
        raise RuntimeError(
            f"archive member {member.name!r} has unsafe size {member.size} bytes"
        )
    return archive.extractfile(member)


def ensure_member_fits(member: tarfile.TarInfo, destination: Path) -> None:
    available = shutil.disk_usage(destination.parent).free
    if member.size + MINIMUM_FREE_BYTES_AFTER_EXTRACTION > available:
        raise RuntimeError(
            f"not enough free space to extract {member.name!r}: "
            f"need {member.size + MINIMUM_FREE_BYTES_AFTER_EXTRACTION} bytes, "
            f"have {available}"
        )
