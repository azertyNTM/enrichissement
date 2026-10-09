"""Étape 2a : recherche web (Brave Search API ou Serper), fournisseur choisi par configuration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, count: int = 10) -> list[SearchResult]: ...


def request_with_retry(send, attempts: int = 4):
    """Retente les erreurs passagères (délai dépassé, 429, 5xx). Les refus définitifs (crédits, clé) remontent."""
    for attempt in range(attempts):
        try:
            r = send()
            if r.status_code != 429 and r.status_code < 500:
                break
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
        time.sleep(2 ** attempt)
    r.raise_for_status()
    return r


class BraveProvider:
    name = "brave"
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, client: httpx.Client, api_key: str):
        self.client, self.api_key = client, api_key

    def search(self, query: str, count: int = 10) -> list[SearchResult]:
        r = request_with_retry(lambda: self.client.get(
            self.URL,
            params={"q": query, "count": count, "country": "fr", "search_lang": "fr"},
            headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
        ))
        results = (r.json().get("web") or {}).get("results") or []
        return [SearchResult(x.get("url", ""), x.get("title", ""), x.get("description", ""))
                for x in results if x.get("url")]


class SerperProvider:
    name = "serper"
    URL = "https://google.serper.dev/search"

    def __init__(self, client: httpx.Client, api_key: str):
        self.client, self.api_key = client, api_key

    def search(self, query: str, count: int = 10) -> list[SearchResult]:
        r = request_with_retry(lambda: self.client.post(
            self.URL,
            json={"q": query, "gl": "fr", "hl": "fr", "num": count},
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
        ))
        results = r.json().get("organic") or []
        return [SearchResult(x.get("link", ""), x.get("title", ""), x.get("snippet", ""))
                for x in results if x.get("link")]


def serper_places(client: httpx.Client, api_key: str, query: str) -> list[dict]:
    """Fiches Google Maps (titre, adresse, téléphone) via Serper."""
    r = request_with_retry(lambda: client.post(
        "https://google.serper.dev/places",
        json={"q": query, "gl": "fr", "hl": "fr"},
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
    ))
    return r.json().get("places") or []


def make_provider(name: str, client: httpx.Client, brave_key: str = "", serper_key: str = "") -> SearchProvider:
    if name == "brave":
        if not brave_key:
            raise ValueError("BRAVE_API_KEY manquant")
        return BraveProvider(client, brave_key)
    if name == "serper":
        if not serper_key:
            raise ValueError("SERPER_API_KEY manquant")
        return SerperProvider(client, serper_key)
    raise ValueError(f"SEARCH_PROVIDER inconnu : {name!r} (attendu : brave ou serper)")
