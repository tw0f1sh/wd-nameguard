from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin

import requests


class RconError(RuntimeError):
    pass


class WardogsRconClient:
    def __init__(self, config: dict[str, Any], retries: int = 2, backoff: float = 1.0) -> None:
        self.log = logging.getLogger("wardogs_nameguard")
        self.base_url = str(config["base_url"]).rstrip("/") + "/"
        self.api_prefix = str(config.get("api_prefix", "/v1")).strip("/")
        self.password = str(config["password"])
        self.timeout = float(config.get("timeout_seconds", 8))
        self.verify_tls = bool(config.get("verify_tls", True))
        self.retries = max(0, int(retries))
        self.backoff = max(0.0, float(backoff))
        self.player_fields = config.get("player_fields", {})

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.password}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Wardogs-NameGuard/1.0",
        })

    def _url(self, path: str) -> str:
        path = path.lstrip("/")
        prefix = self.api_prefix.strip("/")
        return urljoin(self.base_url, f"{prefix}/{path}")

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.request(
                    method,
                    self._url(path),
                    timeout=self.timeout,
                    verify=self.verify_tls,
                    **kwargs,
                )
                if 200 <= response.status_code < 300:
                    return response
                body = response.text[:1000]
                raise RconError(f"HTTP {response.status_code} for {method} {path}: {body}")
            except (requests.RequestException, RconError) as exc:
                last_exc = exc
                if attempt >= self.retries:
                    break
                sleep_for = self.backoff * (attempt + 1)
                self.log.warning("RCON request failed (%s), retrying in %.1fs", exc, sleep_for)
                time.sleep(sleep_for)
        raise RconError(str(last_exc) if last_exc else f"Request failed: {method} {path}")

    def get_players(self) -> list[dict[str, Any]]:
        response = self._request("GET", "players")
        try:
            data = response.json()
        except ValueError as exc:
            raise RconError("GET /players did not return JSON") from exc

        if isinstance(data, list):
            players = data
        elif isinstance(data, dict):
            players = data.get("players") or data.get("data") or data.get("items")
        else:
            players = None

        if not isinstance(players, list):
            raise RconError(
                "Unsupported GET /players response shape. Expected a list or an object containing players/data/items."
            )
        return [p for p in players if isinstance(p, dict)]

    def normalize_player(self, raw: dict[str, Any]) -> dict[str, str]:
        steam_key = self.player_fields.get("steam_id", "id")
        name_key = self.player_fields.get("name", "name")
        faction_key = self.player_fields.get("faction", "faction")

        steam_id = _first_value(raw, [steam_key, "id", "steamId", "steam_id", "steamID", "playerId"])
        name = _first_value(raw, [name_key, "name", "playerName", "displayName"])
        faction = _first_value(raw, [faction_key, "faction", "team", "side"], default="")

        if steam_id is None or name is None:
            raise RconError(f"Player object lacks id/name fields: {raw!r}")
        return {"steam_id": str(steam_id), "name": str(name), "faction": str(faction or "")}

    def message_player(self, player_id: str, message: str) -> None:
        self._request("POST", f"players/{player_id}/message", json={"message": message})

    def kick_player(self, player_id: str, reason: str) -> None:
        self._request("POST", f"players/{player_id}/kick", json={"reason": reason})

    def ban_player(self, player_id: str, reason: str) -> None:
        self._request("POST", "bans", json={"id": player_id, "reason": reason})


def _first_value(obj: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for key in keys:
        if not key:
            continue
        current: Any = obj
        found = True
        for part in str(key).split("."):
            if not isinstance(current, dict) or part not in current:
                found = False
                break
            current = current[part]
        if found and current is not None:
            return current
    return default
