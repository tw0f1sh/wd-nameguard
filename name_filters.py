from __future__ import annotations

import re as pyre
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

import regex


@dataclass(frozen=True)
class MatchResult:
    rule_id: str
    rule_type: str
    description: str
    match: str
    detail: str
    rule: dict[str, Any]


class NameMatcher:
    def __init__(self, rules: Iterable[dict[str, Any]]) -> None:
        self.rules = [r for r in rules if r.get("enabled", True)]
        self._compiled_regex: dict[str, list[tuple[str, regex.Pattern]]] = {}
        self._compiled_scripts: dict[str, list[tuple[str, regex.Pattern]]] = {}
        self._prepare()

    def _prepare(self) -> None:
        for rule in self.rules:
            rule_id = str(rule.get("id", "unnamed"))
            rule_type = rule.get("type")
            if rule_type == "regex":
                flags = regex.VERSION1
                if rule.get("flags", {}).get("ignore_case", False):
                    flags |= regex.IGNORECASE
                compiled = []
                for entry in rule.get("patterns", []):
                    if isinstance(entry, str):
                        name, pattern = entry, entry
                    else:
                        name = str(entry.get("name") or entry.get("pattern") or "regex")
                        pattern = str(entry.get("pattern", ""))
                    if not pattern:
                        continue
                    compiled.append((name, regex.compile(pattern, flags)))
                self._compiled_regex[rule_id] = compiled

            elif rule_type == "unicode_script":
                compiled = []
                for script in rule.get("scripts", []):
                    script_name = str(script)
                    # regex module supports Unicode Script properties directly.
                    compiled.append(
                        (script_name, regex.compile(rf"\p{{Script={script_name}}}", regex.VERSION1))
                    )
                self._compiled_scripts[rule_id] = compiled

    def match_all(self, name: str) -> list[MatchResult]:
        results: list[MatchResult] = []
        for rule in self.rules:
            hit = self._match_rule(name, rule)
            if hit is not None:
                results.append(hit)
        return results

    def _match_rule(self, name: str, rule: dict[str, Any]) -> MatchResult | None:
        rule_id = str(rule.get("id", "unnamed"))
        rule_type = str(rule.get("type", ""))
        description = str(rule.get("description", ""))

        if rule_type == "unicode_script":
            # Two allowance mechanisms are supported:
            #
            # 1) allowed_script_characters: N
            #    Allows up to N code points TOTAL from all configured scripts.
            #    Example with N=1: one Han OR Cyrillic character is OK, while a
            #    second configured-script character triggers the rule.
            #
            # 2) allowed_occurrences: {"乂": 1}
            #    Optional exact-character ceiling. It is applied IN ADDITION to
            #    the general total limit and never increases that total limit.
            #    When allowed_script_characters is omitted, this retains the
            #    original behavior: listed characters are exceptions up to their
            #    configured count; all other configured-script chars trigger.
            allowed_occurrences = _allowed_occurrences(rule)
            allowed_script_characters = _optional_nonnegative_int(
                rule, "allowed_script_characters"
            )
            counts = Counter(name)

            script_hits: list[tuple[int, str, str]] = []
            for script, pattern in self._compiled_scripts.get(rule_id, []):
                for m in pattern.finditer(name):
                    script_hits.append((m.start(), script, m.group(0)))
            script_hits.sort(key=lambda item: item[0])

            if not script_hits:
                return None

            if allowed_script_characters is not None:
                # Exact per-character limits can make the rule stricter, but
                # they cannot make the general total allowance larger.
                for _, script, ch in script_hits:
                    if ch not in allowed_occurrences:
                        continue
                    if counts[ch] > allowed_occurrences[ch]:
                        cp = f"U+{ord(ch):04X}"
                        uname = unicodedata.name(ch, "UNKNOWN")
                        detail = (
                            f"script={script}; {cp} {uname}; "
                            f"occurrences={counts[ch]}; "
                            f"allowed_occurrences={allowed_occurrences[ch]}; "
                            f"script_characters={len(script_hits)}; "
                            f"allowed_script_characters={allowed_script_characters}"
                        )
                        return MatchResult(
                            rule_id, rule_type, description, ch, detail, rule
                        )

                if len(script_hits) <= allowed_script_characters:
                    return None

                # Report the first code point that exceeds the total allowance.
                _, script, ch = script_hits[allowed_script_characters]
                cp = f"U+{ord(ch):04X}"
                uname = unicodedata.name(ch, "UNKNOWN")
                detail = (
                    f"script={script}; {cp} {uname}; "
                    f"script_characters={len(script_hits)}; "
                    f"allowed_script_characters={allowed_script_characters}"
                )
                return MatchResult(rule_id, rule_type, description, ch, detail, rule)

            # Backwards-compatible behavior when no generalized script allowance
            # is configured.
            for _, script, ch in script_hits:
                if ch in allowed_occurrences and counts[ch] <= allowed_occurrences[ch]:
                    continue

                cp = f"U+{ord(ch):04X}"
                uname = unicodedata.name(ch, "UNKNOWN")
                if ch in allowed_occurrences:
                    detail = (
                        f"script={script}; {cp} {uname}; "
                        f"occurrences={counts[ch]}; allowed={allowed_occurrences[ch]}"
                    )
                else:
                    detail = f"script={script}; {cp} {uname}"
                return MatchResult(rule_id, rule_type, description, ch, detail, rule)
            return None

        if rule_type == "characters":
            chars = str(rule.get("characters", ""))
            blocked = set(chars)
            allowed_occurrences = _allowed_occurrences(rule)
            counts = Counter(name)
            for ch in name:
                if ch in blocked:
                    if ch in allowed_occurrences and counts[ch] <= allowed_occurrences[ch]:
                        continue
                    cp = f"U+{ord(ch):04X}"
                    uname = unicodedata.name(ch, "UNKNOWN")
                    detail = f"{cp} {uname}"
                    if ch in allowed_occurrences:
                        detail += (
                            f"; occurrences={counts[ch]}; "
                            f"allowed={allowed_occurrences[ch]}"
                        )
                    return MatchResult(rule_id, rule_type, description, ch, detail, rule)
            return None

        if rule_type == "sequences":
            case_sensitive = bool(rule.get("case_sensitive", False))
            haystack = name if case_sensitive else name.casefold()
            for seq in rule.get("sequences", []):
                seq = str(seq)
                needle = seq if case_sensitive else seq.casefold()
                if needle and needle in haystack:
                    return MatchResult(rule_id, rule_type, description, seq, "sequence", rule)
            return None

        if rule_type == "terms":
            opts = rule.get("normalize", {})
            normalized_name = normalize_for_terms(name, opts)
            for term in rule.get("terms", []):
                term = str(term)
                normalized_term = normalize_for_terms(term, opts)
                if normalized_term and normalized_term in normalized_name:
                    return MatchResult(
                        rule_id,
                        rule_type,
                        description,
                        term,
                        f"normalized_name={normalized_name}",
                        rule,
                    )
            return None

        if rule_type == "regex":
            for preset_name, pattern in self._compiled_regex.get(rule_id, []):
                m = pattern.search(name)
                if m:
                    matched = m.group(0) or preset_name
                    return MatchResult(
                        rule_id,
                        rule_type,
                        description,
                        preset_name,
                        f"regex_match={matched!r}",
                        rule,
                    )
            return None

        raise ValueError(f"Unknown rule type {rule_type!r} in rule {rule_id!r}")


def _allowed_occurrences(rule: dict[str, Any]) -> dict[str, int]:
    """Return exact-code-point occurrence allowances for a rule.

    A value N means the character may occur at most N times in the complete
    player name before the rule matches. Characters not listed here keep the
    normal rule behavior.
    """
    raw = rule.get("allowed_occurrences", {}) or {}
    result: dict[str, int] = {}
    for ch, limit in raw.items():
        value = int(limit)
        if value < 0:
            raise ValueError("allowed_occurrences values must be >= 0")
        result[str(ch)] = value
    return result


def _optional_nonnegative_int(rule: dict[str, Any], key: str) -> int | None:
    """Read an optional non-negative integer from a rule."""
    if key not in rule or rule.get(key) is None:
        return None
    value = int(rule[key])
    if value < 0:
        raise ValueError(f"{key} must be >= 0")
    return value


def normalize_for_terms(value: str, opts: dict[str, Any]) -> str:
    out = value
    if opts.get("unicode_nfkc", True):
        out = unicodedata.normalize("NFKC", out)
    if opts.get("casefold", True):
        out = out.casefold()

    if opts.get("leetspeak", False):
        leet_map = {str(k): str(v) for k, v in opts.get("leet_map", {}).items()}
        if leet_map:
            # Longest source token first; supports multi-character mappings too.
            pattern = pyre.compile("|".join(pyre.escape(k) for k in sorted(leet_map, key=len, reverse=True)))
            out = pattern.sub(lambda m: leet_map[m.group(0)], out)

    if opts.get("remove_separators", False):
        # Keep only Unicode letters and numbers. This collapses spaces, dots,
        # underscores, hyphens and decorative punctuation used for obfuscation.
        out = "".join(ch for ch in out if unicodedata.category(ch)[0] in {"L", "N"})

    return out
