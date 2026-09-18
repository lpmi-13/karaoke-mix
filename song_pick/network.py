from __future__ import annotations

from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SOURCE_HOSTS = frozenset({"data.metabrainz.org", "ftp.musicbrainz.org"})
POPULARITY_HOSTS = frozenset({"api.listenbrainz.org"})
LISTENBRAINZ_TOKEN_ENVIRONMENT = "LISTENBRAINZ_TOKEN"
MAX_SOURCE_DOWNLOAD_BYTES = 32 * 1024**3


def validate_https_url(url: str, allowed_hosts: frozenset[str], label: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{label} is not a valid URL") from error
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https":
        raise ValueError(f"{label} must use HTTPS")
    if hostname not in allowed_hosts:
        raise ValueError(f"{label} host {hostname!r} is not approved")
    if port not in (None, 443):
        raise ValueError(f"{label} must use the default HTTPS port")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not contain credentials")
    if parsed.fragment:
        raise ValueError(f"{label} must not contain a fragment")


def validate_source_url(url: str) -> None:
    validate_https_url(url, SOURCE_HOSTS, "source URL")


def validate_popularity_url(url: str) -> None:
    validate_https_url(url, POPULARITY_HOSTS, "popularity URL")
    if urlsplit(url).path != "/1/popularity/recording":
        raise ValueError("popularity URL must use the ListenBrainz recording endpoint")


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port or 443


class RestrictedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: frozenset[str], label: str):
        self.allowed_hosts = allowed_hosts
        self.label = label

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        try:
            validate_https_url(redirected.full_url, self.allowed_hosts, self.label)
        except ValueError as error:
            raise HTTPError(
                redirected.full_url,
                code,
                f"refusing unsafe redirect: {error}",
                headers,
                fp,
            ) from error
        if _origin(req.full_url) != _origin(redirected.full_url):
            redirected.remove_header("Authorization")
        return redirected


_source_opener = build_opener(RestrictedRedirectHandler(SOURCE_HOSTS, "source URL"))
_popularity_opener = build_opener(
    RestrictedRedirectHandler(POPULARITY_HOSTS, "popularity URL")
)


def open_source_url(request: Request, timeout: float):
    validate_source_url(request.full_url)
    return _source_opener.open(request, timeout=timeout)


def open_popularity_url(request: Request, timeout: float):
    validate_popularity_url(request.full_url)
    return _popularity_opener.open(request, timeout=timeout)
