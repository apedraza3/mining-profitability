import json
import logging
import re

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from services.cache_manager import CacheManager
import config

logger = logging.getLogger(__name__)


class MiningNowService:
    def __init__(self, cache: CacheManager):
        self.cache = cache
        self.base_url = config.MININGNOW_BASE_URL
        self.ttl = config.MININGNOW_CACHE_TTL
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.MININGNOW_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def _fetch_page(self, path: str) -> str | None:
        try:
            resp = self.session.get(
                f"{self.base_url}{path}", timeout=20
            )
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            logger.error("MiningNow fetch failed for %s: %s", path, e)
            return None

    def _parse_next_data(self, html: str) -> dict | None:
        """Strategy 1: Extract data from __NEXT_DATA__ script tag."""
        soup = BeautifulSoup(html, "html.parser")
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if script and script.string:
            try:
                return json.loads(script.string)
            except json.JSONDecodeError:
                pass
        return None

    def _parse_next_f_push(self, html: str) -> list[str]:
        """Strategy 2: Extract data from self.__next_f.push() calls."""
        chunks = []
        pattern = r'self\.__next_f\.push\(\[.*?,"(.*?)"\]\)'
        for match in re.finditer(pattern, html, re.DOTALL):
            chunks.append(match.group(1))
        return chunks

    def _parse_html_tables(self, html: str) -> list[dict]:
        """Strategy 3: Direct HTML parsing as last resort."""
        soup = BeautifulSoup(html, "html.parser")
        miners = []
        # Look for table rows or card-like elements
        rows = soup.select("table tbody tr")
        if rows:
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 4:
                    miners.append({
                        "name": cells[0].get_text(strip=True),
                        "hashrate": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                        "power": cells[2].get_text(strip=True) if len(cells) > 2 else "",
                        "efficiency": cells[3].get_text(strip=True) if len(cells) > 3 else "",
                    })
            return miners

        # Try card-based layouts
        cards = soup.select("[class*='miner'], [class*='card'], [class*='item']")
        for card in cards:
            name_el = card.select_one("h2, h3, h4, [class*='name'], [class*='title']")
            if name_el:
                miners.append({
                    "name": name_el.get_text(strip=True),
                    "raw_text": card.get_text(" ", strip=True)[:300],
                })
        return miners

    def scrape_miner_list(self) -> list[dict]:
        """Scrape the ASIC miner list page."""
        cached = self.cache.get("miner_list", self.ttl)
        if cached is not None:
            return cached

        html = self._fetch_page("/latest-asic-miner-list/")
        if not html:
            return []

        miners = []

        # Strategy 1: __NEXT_DATA__
        next_data = self._parse_next_data(html)
        if next_data:
            try:
                page_props = next_data.get("props", {}).get("pageProps", {})
                miner_data = (
                    page_props.get("miners")
                    or page_props.get("data")
                    or page_props.get("asicMiners")
                    or []
                )
                if isinstance(miner_data, list) and miner_data:
                    miners = self._normalize_miner_data(miner_data)
            except (AttributeError, TypeError):
                pass

        # Strategy 2: self.__next_f.push() chunks
        if not miners:
            chunks = self._parse_next_f_push(html)
            for chunk in chunks:
                try:
                    decoded = chunk.encode().decode("unicode_escape")
                    data = json.loads(decoded)
                    if isinstance(data, list) and data:
                        miners = self._normalize_miner_data(data)
                        if miners:
                            break
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

        # Strategy 3: HTML tables/cards
        if not miners:
            miners = self._parse_html_tables(html)

        if miners:
            self.cache.set("miner_list", miners)
        return miners

    def scrape_ranking(self) -> list[dict]:
        """Scrape the ASIC ranking page."""
        cached = self.cache.get("ranking", self.ttl)
        if cached is not None:
            return cached

        html = self._fetch_page("/asic-ranking/")
        if not html:
            return []

        rankings = []
        next_data = self._parse_next_data(html)
        if next_data:
            try:
                page_props = next_data.get("props", {}).get("pageProps", {})
                rank_data = (
                    page_props.get("rankings")
                    or page_props.get("data")
                    or page_props.get("asicRankings")
                    or []
                )
                if isinstance(rank_data, list):
                    for i, entry in enumerate(rank_data):
                        rankings.append({
                            "rank": entry.get("rank", i + 1),
                            "name": entry.get("name", entry.get("model", "")),
                            "brand": entry.get("brand", ""),
                            "score": entry.get("score", entry.get("profitabilityScore", 0)),
                        })
            except (AttributeError, TypeError):
                pass

        if not rankings:
            rankings = self._parse_html_tables(html)

        if rankings:
            self.cache.set("ranking", rankings)
        return rankings

    def _normalize_miner_data(self, raw_list: list) -> list[dict]:
        """Normalize varying JSON structures into a standard format."""
        normalized = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            normalized.append({
                "name": (
                    item.get("name")
                    or item.get("model")
                    or item.get("title")
                    or ""
                ),
                "brand": item.get("brand", item.get("manufacturer", "")),
                "hashrate": item.get("hashRate", item.get("hashrate", "")),
                "hashrate_unit": item.get("hashRateUnit", item.get("hashrateUnit", "")),
                "power": item.get("power", item.get("wattage", item.get("powerConsumption", ""))),
                "efficiency": item.get("efficiency", ""),
                "noise": item.get("noise", item.get("noiseLevel", "")),
                "cooling": item.get("cooling", item.get("coolingType", "")),
                "best_price": item.get("bestPrice", item.get("price", "")),
                "coins": item.get("coins", item.get("compatibleCoins", [])),
                "image": item.get("image", item.get("imageUrl", "")),
            })
        return normalized

    def find_miner_data(self, model_key: str) -> dict | None:
        """Fuzzy match a miner model against scraped data."""
        miner_list = self.scrape_miner_list()
        rankings = self.scrape_ranking()

        best_match = None
        best_score = 0

        for miner in miner_list:
            name = miner.get("name", "")
            score = fuzz.token_sort_ratio(model_key.lower(), name.lower())
            if score > best_score:
                best_score = score
                best_match = dict(miner)

        # Try to merge ranking data
        if best_match:
            for rank_entry in rankings:
                rank_name = rank_entry.get("name", "")
                if fuzz.token_sort_ratio(
                    best_match["name"].lower(), rank_name.lower()
                ) >= 80:
                    best_match["rank"] = rank_entry.get("rank")
                    best_match["profitability_score"] = rank_entry.get("score")
                    break

        if best_match and best_score >= 70:
            best_match["match_confidence"] = best_score
            return best_match
        return None

    def get_all_model_names(self) -> list[str]:
        """Get all miner model names for autocomplete."""
        miner_list = self.scrape_miner_list()
        return sorted(set(m.get("name", "") for m in miner_list if m.get("name")))

    def is_available(self) -> bool:
        """Check if MiningNow scraping is working (has cached data or can fetch)."""
        cached = self.cache.get("miner_list", self.ttl)
        return cached is not None and len(cached) > 0
