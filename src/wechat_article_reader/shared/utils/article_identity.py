"""Pure article identity and exact delivery fingerprints."""

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_KEYS = frozenset(
    {"from", "scene", "clicktime", "enterid", "sessionid", "subscene", "ascene"}
)


def normalize_article_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
    ]
    # Stable by key; preserve the order of repeated values within a key.
    query.sort(key=lambda item: item[0])
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), "")
    )


def article_digest(
    html: str, text: str, title: str, author: str | None, account: str | None
) -> str:
    payload = json.dumps([html, text, title, author, account], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_section_title(value: str) -> str:
    value = re.sub(r"[\u200b-\u200f\u2060-\u206f\ufeff\u00ad]", "", value)
    return " ".join(value.split())
