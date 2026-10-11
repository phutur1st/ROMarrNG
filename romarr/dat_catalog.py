"""A bounded, platform-aware browse/search view over loaded preservation DATs.

DATs describe verified dumps, not popularity or release dates. This module
keeps that distinction in the result shape and uses an opaque, stable key for
each system/title pair so a consumer never has to pretend it is an IGDB ID.
"""

from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from dataclasses import dataclass

from .dat import Dat, Game, base_title
from .platforms import PLATFORMS, Platform, resolve

MAX_LIMIT = 50
MAX_OFFSET = 10000
_VALID_KEY = re.compile(r"^dat-[0-9a-f]{64}$")
_SKIP_LABELS = {"nintendo", "sega", "atari", "playstation", "xbox", "pc"}


def _normalise(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value or "").encode(
        "ascii", "ignore"
    ).decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


def platform_for_dat_name(name: str) -> Platform | None:
    """Resolve a DAT header to the closest declared ROMarr platform label.

    DAT publishers commonly prefix the system name with its manufacturer
    ("Nintendo - Game Boy Advance"). Exact ROMarr names remain the strongest
    match; otherwise the longest declared whole-label match wins. Ambiguous
    labels stay unmatched and are reported to the operator.
    """
    direct = resolve(name)
    if direct is not None and direct.media != "digital":
        return direct

    haystack = f" {_normalise(name)} "
    matches: dict[str, tuple[int, Platform]] = {}
    for platform in PLATFORMS:
        if platform.media == "digital":
            continue
        labels = (platform.name, *platform.aliases)
        best = 0
        for label in labels:
            normalized = _normalise(label)
            if len(normalized) < 4 or normalized in _SKIP_LABELS:
                continue
            if f" {normalized} " in haystack:
                best = max(best, len(normalized))
        if best:
            matches[platform.slug] = (best, platform)
    if not matches:
        return None
    longest = max(score for score, _ in matches.values())
    winners = [platform for score, platform in matches.values() if score == longest]
    return winners[0] if len(winners) == 1 else None


@dataclass(frozen=True)
class DatCatalogEntry:
    key: str
    title: str
    platform: Platform
    dat_name: str
    dat_version: str
    entry_name: str
    variants: int

    def as_dict(self) -> dict:
        return {
            "id": self.key,
            "catalogProvider": "dat",
            "catalogId": self.key,
            "title": self.title,
            "summary": f"Listed in {self.dat_name}"
            + (f" · {self.dat_version}" if self.dat_version else ""),
            "coverUrl": "",
            "releaseDate": "",
            "platforms": [self.platform.name],
            "platformOptions": [
                {"key": self.platform.slug, "name": self.platform.name}
            ],
            "genres": [],
            "source": "DAT",
            "dat": {
                "name": self.dat_name,
                "version": self.dat_version,
                "entry": self.entry_name,
                "variants": self.variants,
            },
        }


def _entry_key(platform_slug: str, dat_name: str, group_key: str) -> str:
    payload = "\0".join(
        (_normalise(platform_slug), _normalise(dat_name), _normalise(group_key))
    )
    return "dat-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _selected_entries(
    dats: list[Dat], preferred_regions: list[str] | tuple[str, ...] | None = None
) -> list[DatCatalogEntry]:
    regions = tuple(
        str(region).strip().lower()
        for region in (preferred_regions or ("usa", "world", "europe", "japan"))
        if str(region).strip()
    ) or ("usa", "world", "europe", "japan")
    # DATs arrive from a bounded scan. Sorting makes the winner deterministic
    # when operators load multiple DAT versions for the same system.
    ordered_dats = sorted(
        dats,
        key=lambda dat: (_normalise(dat.name), _normalise(dat.version)),
    )
    selected: list[DatCatalogEntry] = []
    seen: set[tuple[str, str]] = set()
    for dat in ordered_dats:
        platform = platform_for_dat_name(dat.name)
        if platform is None:
            continue
        chosen = dat.one_game_one_rom(list(regions))
        variant_counts: dict[str, int] = {}
        for candidate in dat.games.values():
            group_key = dat.group_key(candidate.name)
            variant_counts[group_key] = variant_counts.get(group_key, 0) + 1
        for game in sorted(chosen.values(), key=lambda row: _normalise(row.name)):
            group_key = dat.group_key(game.name)
            title = base_title(group_key)
            normalized_title = _normalise(title)
            if not normalized_title:
                continue
            dedupe_key = (platform.slug, normalized_title)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            selected.append(
                DatCatalogEntry(
                    key=_entry_key(platform.slug, dat.name, group_key),
                    title=title,
                    platform=platform,
                    dat_name=dat.name[:160],
                    dat_version=dat.version[:80],
                    entry_name=game.name[:512],
                    variants=variant_counts[group_key],
                )
            )
    selected.sort(
        key=lambda row: (
            _normalise(row.title), row.platform.slug, row.key
        )
    )
    return selected


class DatCatalogCache:
    """One catalog generation per service, shared by browse/search/detail.

    Parsed DATs are read-only after loading. Reloading replaces their objects;
    region changes also replace the snapshot. Retaining the source objects
    prevents reused object IDs from returning a previous generation. The lock
    makes simultaneous cold requests build once, and only one snapshot stays
    resident regardless of the number of searches or preference changes.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sources: tuple[Dat, ...] = ()
        self._regions: tuple[str, ...] | None = None
        self._entries: tuple[DatCatalogEntry, ...] = ()
        self._titles: tuple[str, ...] = ()
        self._by_key: dict[str, DatCatalogEntry] = {}

    def snapshot(self, dats, preferred_regions=None):
        sources = tuple(dats)
        regions = tuple(str(r).strip().lower() for r in
                        (preferred_regions or ("usa", "world", "europe", "japan"))
                        if str(r).strip()) or ("usa", "world", "europe", "japan")
        with self._lock:
            if (regions != self._regions or len(sources) != len(self._sources)
                    or any(a is not b for a, b in zip(sources, self._sources))):
                entries = tuple(_selected_entries(list(sources), regions))
                titles = tuple(_normalise(entry.title) for entry in entries)
                by_key = {entry.key: entry for entry in entries}
                self._sources, self._regions = sources, regions
                self._entries, self._titles, self._by_key = entries, titles, by_key
            return self._entries, self._titles, self._by_key


def _snapshot(dats, preferred_regions, cache):
    if cache is not None:
        return cache.snapshot(dats, preferred_regions)
    entries = tuple(_selected_entries(dats, preferred_regions))
    return entries, tuple(_normalise(e.title) for e in entries), {
        entry.key: entry for entry in entries
    }


def dat_catalog_platforms(
    dats: list[Dat],
    preferred_regions: list[str] | tuple[str, ...] | None = None,
    *, cache: DatCatalogCache | None = None,
) -> dict:
    entries, _, _ = _snapshot(dats, preferred_regions, cache)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.platform.slug] = counts.get(entry.platform.slug, 0) + 1
    unmatched = sorted(
        {
            str(dat.name or "Unnamed DAT")[:160]
            for dat in dats
            if platform_for_dat_name(dat.name) is None
        },
        key=_normalise,
    )
    return {
        "results": [
            {
                "slug": platform.slug,
                "name": platform.name,
                "gameCount": counts[platform.slug],
            }
            for platform in PLATFORMS
            if platform.slug in counts
        ],
        "unmatchedDatNames": unmatched[:100],
    }


def dat_catalog_page(
    dats: list[Dat], *, limit: int = 24, offset: int = 0,
    platform_slugs: list[str] | None = None, query: str = "",
    preferred_regions: list[str] | tuple[str, ...] | None = None,
    cache: DatCatalogCache | None = None,
) -> tuple[list[dict], int | None]:
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise ValueError("invalid catalog limit")
    if not 0 <= int(offset) <= MAX_OFFSET:
        raise ValueError("invalid catalog offset")
    if len(query) > 200:
        raise ValueError("invalid catalog search query")
    selected_slugs = set(platform_slugs or [])
    known_slugs = {platform.slug for platform in PLATFORMS}
    if len(selected_slugs) > 100 or not selected_slugs <= known_slugs:
        raise ValueError("invalid DAT platform slugs")

    needle = _normalise(query)
    entries, titles, _ = _snapshot(dats, preferred_regions, cache)
    rows = [entry for entry, title in zip(entries, titles)
            if (not selected_slugs or entry.platform.slug in selected_slugs)
            and (not needle or needle in title)]
    page = [entry.as_dict() for entry in
            rows[int(offset):int(offset) + int(limit)]]
    next_offset = int(offset) + len(page)
    return page, next_offset if next_offset < len(rows) else None


def dat_catalog_game(
    dats: list[Dat], key: str,
    preferred_regions: list[str] | tuple[str, ...] | None = None,
    *, cache: DatCatalogCache | None = None,
) -> dict | None:
    if not isinstance(key, str) or not _VALID_KEY.fullmatch(key):
        return None
    _, _, by_key = _snapshot(dats, preferred_regions, cache)
    entry = by_key.get(key)
    return entry.as_dict() if entry is not None else None
