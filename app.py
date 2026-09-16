from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import regex
import yaml

from logging_setup import setup_logging
from name_filters import MatchResult, NameMatcher
from rcon_client import RconError, WardogsRconClient
from state_store import StateStore

STOP = False


def handle_signal(signum: int, _frame: Any) -> None:
    global STOP
    STOP = True
    logging.getLogger("wardogs_nameguard").info("Received signal %s; shutting down", signum)


def load_config(path: str, require_rcon_password: bool = True) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    if "rcon" not in config or "base_url" not in config["rcon"]:
        raise ValueError("config: rcon.base_url is required")

    env_name = str(config["rcon"].get("password_env", "WARDOGS_RCON_KEY"))
    env_value = os.getenv(env_name)
    if env_value:
        config["rcon"]["password"] = env_value

    password = str(config["rcon"].get("password", "")).strip()
    if require_rcon_password and (not password or password == "CHANGE_ME"):
        raise ValueError(
            f"Set rcon.password in config or environment variable {env_name}."
        )

    match_mode = str(config.get("bot", {}).get("match_mode", "first"))
    if match_mode not in {"first", "all"}:
        raise ValueError("bot.match_mode must be 'first' or 'all'")

    for rule in config.get("rules", []):
        if not rule.get("id"):
            raise ValueError("Every rule needs a non-empty id")
        action_type = str(rule.get("action", {}).get("type", "none"))
        if action_type not in {"none", "kick", "ban"}:
            raise ValueError(f"Rule {rule['id']}: action.type must be none, kick or ban")
        warning_count = int(rule.get("warning", {}).get("count", 0))
        if warning_count < 0:
            raise ValueError(f"Rule {rule['id']}: warning.count cannot be negative")

        occurrence_limits = rule.get("allowed_occurrences", {}) or {}
        if not isinstance(occurrence_limits, dict):
            raise ValueError(f"Rule {rule['id']}: allowed_occurrences must be a mapping")
        for character, limit in occurrence_limits.items():
            character = str(character)
            if len(character) != 1:
                raise ValueError(
                    f"Rule {rule['id']}: allowed_occurrences keys must be exactly one Unicode code point"
                )
            try:
                limit = int(limit)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Rule {rule['id']}: allowed_occurrences[{character!r}] must be an integer"
                ) from exc
            if limit < 0:
                raise ValueError(
                    f"Rule {rule['id']}: allowed_occurrences[{character!r}] cannot be negative"
                )

        if "allowed_script_characters" in rule and rule.get("allowed_script_characters") is not None:
            try:
                allowed_script_characters = int(rule["allowed_script_characters"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Rule {rule['id']}: allowed_script_characters must be an integer"
                ) from exc
            if allowed_script_characters < 0:
                raise ValueError(
                    f"Rule {rule['id']}: allowed_script_characters cannot be negative"
                )

    return config


def resolve_path(base_dir: Path, value: str) -> str:
    p = Path(value)
    if p.is_absolute():
        return str(p)
    return str((base_dir / p).resolve())


def is_allowlisted(player: dict[str, str], allowlist: dict[str, Any]) -> bool:
    if player["steam_id"] in {str(x) for x in allowlist.get("steam_ids", [])}:
        return True
    if player["name"] in {str(x) for x in allowlist.get("names_exact", [])}:
        return True
    for pattern in allowlist.get("name_regex", []):
        if regex.search(str(pattern), player["name"], regex.IGNORECASE | regex.VERSION1):
            return True
    return False


def render(text: str, player: dict[str, str], hit: MatchResult, count: int, max_count: int) -> str:
    values = {
        "name": player["name"],
        "steam_id": player["steam_id"],
        "faction": player["faction"],
        "rule": hit.rule_id,
        "match": hit.match,
        "count": count,
        "max_count": max_count,
        "remaining": max(0, max_count - count),
    }
    try:
        return str(text).format(**values)
    except KeyError as exc:
        raise ValueError(f"Unknown placeholder {exc} in rule {hit.rule_id}") from exc


def action_event(action_logger: logging.Logger, **event: Any) -> None:
    event["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    action_logger.info(json.dumps(event, ensure_ascii=False, sort_keys=True))


def process_hit(
    client: WardogsRconClient,
    store: StateStore,
    player: dict[str, str],
    hit: MatchResult,
    dry_run: bool,
    log: logging.Logger,
    action_log: logging.Logger,
) -> None:
    rule = hit.rule
    warning_cfg = rule.get("warning", {})
    action_cfg = rule.get("action", {})
    state = store.get(player["steam_id"], hit.rule_id)

    warnings_enabled = bool(warning_cfg.get("enabled", False))
    max_warnings = int(warning_cfg.get("count", 0)) if warnings_enabled else 0
    warnings_sent = int(state.get("warnings_sent") or 0)
    interval = max(0.0, float(warning_cfg.get("interval_seconds", 0)))
    last_warning = state.get("last_warning_ts")

    if warnings_sent < max_warnings:
        now = time.time()
        if last_warning is None or now - float(last_warning) >= interval:
            next_count = warnings_sent + 1
            message = render(
                str(warning_cfg.get("text", "Please change your player name.")),
                player,
                hit,
                next_count,
                max_warnings,
            )
            if not dry_run:
                client.message_player(player["steam_id"], message)
                store.record_warning(player["steam_id"], hit.rule_id)
            action_event(
                action_log,
                event="warning",
                dry_run=dry_run,
                steam_id=player["steam_id"],
                name=player["name"],
                faction=player["faction"],
                rule=hit.rule_id,
                match=hit.match,
                detail=hit.detail,
                warning_number=next_count,
                warning_max=max_warnings,
                message=message,
            )
            log.info(
                "%s warning %d/%d to %s (%s), rule=%s",
                "DRY-RUN" if dry_run else "Sent",
                next_count,
                max_warnings,
                player["name"],
                player["steam_id"],
                hit.rule_id,
            )
        return

    action_type = str(action_cfg.get("type", "none"))
    if action_type == "none":
        return

    cooldown = max(0.0, float(action_cfg.get("cooldown_seconds", 120)))
    last_action = state.get("last_action_ts")
    if last_action is not None and time.time() - float(last_action) < cooldown:
        return

    reason = render(str(action_cfg.get("reason", "Invalid player name")), player, hit, warnings_sent, max_warnings)
    if not dry_run:
        if action_type == "kick":
            client.kick_player(player["steam_id"], reason)
        elif action_type == "ban":
            client.ban_player(player["steam_id"], reason)
        store.record_action(player["steam_id"], hit.rule_id)

    action_event(
        action_log,
        event=action_type,
        dry_run=dry_run,
        steam_id=player["steam_id"],
        name=player["name"],
        faction=player["faction"],
        rule=hit.rule_id,
        match=hit.match,
        detail=hit.detail,
        reason=reason,
    )
    log.warning(
        "%s %s for %s (%s), rule=%s, reason=%s",
        "DRY-RUN" if dry_run else "Executed",
        action_type,
        player["name"],
        player["steam_id"],
        hit.rule_id,
        reason,
    )


def run_once(
    client: WardogsRconClient,
    matcher: NameMatcher,
    store: StateStore,
    cfg: dict[str, Any],
    log: logging.Logger,
    action_log: logging.Logger,
    dry_run: bool,
) -> None:
    raw_players = client.get_players()
    players: list[dict[str, str]] = []
    for raw in raw_players:
        try:
            players.append(client.normalize_player(raw))
        except RconError as exc:
            log.error("Skipping malformed player entry: %s", exc)

    present_ids = {p["steam_id"] for p in players}
    if cfg.get("bot", {}).get("clear_state_when_player_leaves", True):
        removed = store.clear_absent_players(present_ids)
        if removed:
            log.debug("Cleared %d stale player/rule state entries", removed)

    allowlist = cfg.get("allowlist", {})
    match_mode = str(cfg.get("bot", {}).get("match_mode", "first"))
    for player in players:
        if is_allowlisted(player, allowlist):
            continue
        hits = matcher.match_all(player["name"])
        if not hits:
            continue
        if match_mode == "first":
            hits = hits[:1]
        for hit in hits:
            process_hit(client, store, player, hit, dry_run, log, action_log)


def main() -> int:
    parser = argparse.ArgumentParser(description="Wardogs RCON bad-name filter")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--once", action="store_true", help="Run one polling cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Never message/kick/ban; only log decisions")
    parser.add_argument("--check-name", help="Test one name against configured rules without RCON calls")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    cfg = load_config(str(config_path), require_rcon_password=args.check_name is None)
    base_dir = config_path.parent
    cfg.setdefault("logging", {})["directory"] = resolve_path(base_dir, str(cfg.get("logging", {}).get("directory", "./logs")))
    cfg.setdefault("state", {})["sqlite_path"] = resolve_path(base_dir, str(cfg.get("state", {}).get("sqlite_path", "./data/nameguard.sqlite3")))

    log, action_log = setup_logging(cfg.get("logging", {}))
    matcher = NameMatcher(cfg.get("rules", []))

    if args.check_name is not None:
        hits = matcher.match_all(args.check_name)
        print(json.dumps([
            {"rule": h.rule_id, "type": h.rule_type, "match": h.match, "detail": h.detail}
            for h in hits
        ], ensure_ascii=False, indent=2))
        return 1 if hits else 0

    store = StateStore(cfg["state"]["sqlite_path"])
    bot_cfg = cfg.get("bot", {})
    client = WardogsRconClient(
        cfg["rcon"],
        retries=int(bot_cfg.get("request_retry_count", 2)),
        backoff=float(bot_cfg.get("request_retry_backoff_seconds", 1.0)),
    )
    dry_run = bool(bot_cfg.get("dry_run", False) or args.dry_run)
    poll_interval = max(1.0, float(bot_cfg.get("poll_interval_seconds", 10)))

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("Wardogs NameGuard started; dry_run=%s; poll=%.1fs", dry_run, poll_interval)
    while not STOP:
        started = time.monotonic()
        try:
            run_once(client, matcher, store, cfg, log, action_log, dry_run)
        except Exception:
            log.exception("Polling cycle failed")
        if args.once:
            break
        elapsed = time.monotonic() - started
        sleep_for = max(0.1, poll_interval - elapsed)
        end = time.monotonic() + sleep_for
        while not STOP and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))

    log.info("Wardogs NameGuard stopped")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, yaml.YAMLError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2)
