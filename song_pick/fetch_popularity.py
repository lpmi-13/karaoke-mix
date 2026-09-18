from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .config import Config
from .db import columnar_parameters, set_stat, transaction
from .network import (
    LISTENBRAINZ_TOKEN_ENVIRONMENT,
    open_popularity_url as urlopen,
    validate_popularity_url,
)


TRANSIENT_STATUS = {429, 500, 502, 503, 504}
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _chunks(values: list[str], size: int):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _batch_key(mbids: list[str]) -> str:
    return hashlib.sha256("\n".join(mbids).encode()).hexdigest()


def request_popularity(mbids: list[str], settings: dict[str, Any]) -> list[dict[str, Any]]:
    payload = json.dumps({"recording_mbids": mbids}).encode()
    retries = int(settings["maximumRetries"])
    initial_backoff = float(settings["initialBackoffSeconds"])
    url = str(settings["url"])
    validate_popularity_url(url)
    token_environment = str(settings.get("tokenEnvironment") or "")
    if token_environment != LISTENBRAINZ_TOKEN_ENVIRONMENT:
        raise ValueError(
            f"tokenEnvironment must be {LISTENBRAINZ_TOKEN_ENVIRONMENT!r}"
        )
    if not 0 <= retries <= 10 or not 0 <= initial_backoff <= 60:
        raise ValueError("popularity retry settings are outside their safe bounds")
    for attempt in range(retries + 1):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": str(settings["userAgent"]),
        }
        token = os.environ.get(token_environment)
        if token:
            headers["Authorization"] = f"Token {token}"
        request = Request(
            url,
            data=payload,
            method="POST",
            headers=headers,
        )
        try:
            with urlopen(request, timeout=60) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise RuntimeError(
                    f"ListenBrainz response exceeded {MAX_RESPONSE_BYTES} bytes"
                )
            result = json.loads(body)
            if not isinstance(result, list):
                raise RuntimeError("ListenBrainz returned a non-list popularity response")
            if len(result) > len(mbids) or any(not isinstance(item, dict) for item in result):
                raise RuntimeError("ListenBrainz returned an invalid popularity response")
            return result
        except HTTPError as error:
            if error.code in {401, 403} and not token:
                raise RuntimeError(
                    f"ListenBrainz requires authentication; set {token_environment} and retry"
                ) from error
            if error.code not in TRANSIENT_STATUS or attempt == retries:
                raise RuntimeError(f"ListenBrainz popularity request failed: HTTP {error.code}") from error
            retry_after = error.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else initial_backoff * 2**attempt
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt == retries:
                raise RuntimeError(f"ListenBrainz popularity request failed: {error}") from error
            delay = initial_backoff * 2**attempt
        time.sleep(min(delay, 60))
    raise AssertionError("unreachable")


def fetch_popularity(db: Any, config: Config, refresh: bool = False) -> None:
    mbids = [
        row[0]
        for row in db.execute(
            """
            SELECT recording_mbid FROM (
              SELECT canonical_recording_mbid::VARCHAR AS recording_mbid
              FROM tempo_candidate WHERE exclusion_reason IS NULL
              UNION
              SELECT source_recording_mbid::VARCHAR AS recording_mbid
              FROM source_tempo_candidate WHERE exclusion_reason IS NULL
            )
            ORDER BY recording_mbid
            """
        ).fetchall()
    ]
    settings = config.popularity
    batch_size = int(settings["batchSize"])
    if not 1 <= batch_size <= 10_000:
        raise ValueError("popularity batch size is outside its safe bounds")
    total_batches = (len(mbids) + batch_size - 1) // batch_size
    completed = {
        row[0]
        for row in db.execute("SELECT batch_key FROM popularity_batch").fetchall()
    }
    fetched = 0
    skipped = 0
    for number, batch in enumerate(_chunks(mbids, batch_size), start=1):
        key = _batch_key(batch)
        if not refresh and key in completed:
            skipped += len(batch)
            continue
        response = request_popularity(batch, settings)
        by_mbid = {str(item.get("recording_mbid")): item for item in response}
        as_of = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows = []
        for mbid in batch:
            item = by_mbid.get(mbid, {})
            rows.append(
                (
                    mbid,
                    item.get("total_user_count"),
                    item.get("total_listen_count"),
                    as_of,
                )
            )
        with transaction(db):
            db.execute(
                """
                WITH batch AS (
                  SELECT unnest(?::VARCHAR[])::UUID AS recording_mbid,
                         unnest(?::BIGINT[]) AS total_listener_count,
                         unnest(?::BIGINT[]) AS total_listen_count,
                         unnest(?::VARCHAR[])::TIMESTAMP AS as_of
                )
                INSERT INTO popularity SELECT * FROM batch
                ON CONFLICT (recording_mbid) DO UPDATE SET
                  total_listener_count = excluded.total_listener_count,
                  total_listen_count = excluded.total_listen_count,
                  as_of = excluded.as_of
                """,
                columnar_parameters(rows),
            )
            db.execute(
                """
                INSERT INTO popularity_batch VALUES (?, current_timestamp, ?)
                ON CONFLICT (batch_key) DO UPDATE SET
                  completed_at = excluded.completed_at, item_count = excluded.item_count
                """,
                [key, len(batch)],
            )
        fetched += len(batch)
        print(
            f"popularity batch {number} out of {total_batches}: "
            f"checkpointed {len(batch):,} recordings"
        )

    set_stat(db, "listenbrainz", "requested", fetched)
    set_stat(db, "listenbrainz", "cached", skipped)
    set_stat(db, "listenbrainz", "total", len(mbids))
    print(f"popularity complete: {fetched:,} fetched, {skipped:,} resumed from cache")
