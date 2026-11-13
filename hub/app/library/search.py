"""Case-insensitive multi-token search over the normalized `search_text` columns (persistence spec).

`tracks.search_text` is `normalize_title(artist + title + album)`, `albums.search_text` is
`normalize_title(album_artist + title)`. Both are filled by ORM events on every insert/update, and the
query normalizes its tokens the same way: `search_text ILIKE ALL (ARRAY['%токен%', ...])`.
"""

from __future__ import annotations

from sqlalchemy import all_, literal
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import ARRAY, Text

from app.matching.normalize import normalize_title

SEARCH_TEXT_LENGTH = 2048


def track_search_text(artist: str | None, title: str | None, album: str | None) -> str:
    """`normalize_title("{artist} {title} {album}")` — what `tracks.search_text` holds."""
    return normalize_title(f"{artist or ''} {title or ''} {album or ''}").text[:SEARCH_TEXT_LENGTH]


def album_search_text(album_artist: str | None, title: str | None) -> str:
    """`normalize_title("{album_artist} {title}")` — what `albums.search_text` holds."""
    return normalize_title(f"{album_artist or ''} {title or ''}").text[:SEARCH_TEXT_LENGTH]


def _escape(token: str) -> str:
    """LIKE metacharacters in a user's token are literal text, not patterns."""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_tokens(query: str | None) -> list[str]:
    """The normalized tokens of `query`; empty — nothing to search for."""
    return normalize_title(query or "").text.split()


def ilike_all(query: str | None) -> ColumnElement[bool] | None:
    """`col ILIKE ALL (ARRAY[...])`: every token must occur anywhere in the normalized column.

    None — the query has no tokens; the caller returns nothing rather than matching every row.
    """
    tokens = search_tokens(query)
    if not tokens:
        return None
    return all_(literal([f"%{_escape(token)}%" for token in tokens], ARRAY(Text)))
