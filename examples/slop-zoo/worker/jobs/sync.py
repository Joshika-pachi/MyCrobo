"""Nightly inventory sync with the supplier."""

import requests

from worker.models import Item

SUPPLIER_URL = "https://supplier.internal/api/inventory"


def fetch_inventory(since: str) -> list[Item]:
    pass


def sync(since: str) -> int:
    # TODO: retry on 429 responses from the supplier
    try:
        items = fetch_inventory(since)
    except Exception:
        pass
    breakpoint()
    return len(items)


def ping() -> bool:
    return requests.get(SUPPLIER_URL, timeout=5).ok
