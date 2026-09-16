# Wardogs NameGuard

Python service for polling the Wardogs RCON `GET /v1/players` endpoint, detecting configurable bad player names and warning, kicking or banning players through the RCON API.


## Yes, its made with AI, if you want to cry about it, dont use it!
- build and tested on -> `v1 • ++Wardogs+Live-CL-501228`

## Included features

- UTF-8 / Unicode-safe processing (`NFKC`, `casefold`)
- Unicode script filters, including Cyrillic, Han, Hangul, Hiragana and Katakana
- General occurrence allowance across all configured Unicode scripts
- Per-character occurrence allowances inside `unicode_script` and `characters` rules
- Literal custom character/symbol blacklist
- Literal custom sequence blacklist
- Forbidden-term matcher with optional separator removal and configurable Leetspeak mapping
- Custom regex presets
- Per-rule warning count, warning text and warning interval
- Per-rule action: `none`, `kick`, or `ban`
- Allowlist by Steam ID, exact name, or regex
- Persistent warning/action state in SQLite
- Automatic state reset when a player leaves the server
- Separate rotating `bot.log` and JSONL `actions.log`
- Size-based or time-based log rotation
- Dry-run mode and standalone name-test mode
- PM2 ecosystem configuration

## RCON API mapping

The client uses:

- `GET /v1/players`
- `POST /v1/players/{id}/message` with `{ "message": "..." }`
- `POST /v1/players/{id}/kick` with `{ "reason": "..." }`
- `POST /v1/bans` with `{ "id": "...", "reason": "..." }`

Authentication is sent as:

```text
Authorization: Bearer <RCON key/password>
```

## Install on the vServer

```bash
unzip wardogs_nameguard.zip
cd wardogs_nameguard
./install.sh
nano config.yaml
```

First test without executing any action:

```bash
.venv/bin/python app.py --config config.yaml --dry-run --once
```

Test a name locally, without connecting to RCON:

```bash
.venv/bin/python app.py --config config.yaml --check-name 'T3st.Name'
```

Start with PM2:

```bash
pm2 start ecosystem.config.js
pm2 save
pm2 status
pm2 logs wardogs-nameguard
```

For PM2 autostart after reboot, run the command printed by:

```bash
pm2 startup
```

## RCON password / Bearer key

Two methods are supported:

1. Set `rcon.password` in `config.yaml`.
2. Preferably set the environment variable named by `rcon.password_env` (default `WARDOGS_RCON_KEY`). An environment value overrides the YAML password.

Keep `config.yaml` private:

```bash
chmod 600 config.yaml
```

The key is never written to the logs.

## Rule types

### `unicode_script`

Matches any single code point belonging to one of the configured Unicode Scripts. Example scripts: `Cyrillic`, `Han`, `Hangul`, `Hiragana`, `Katakana`.

You can allow a generalized number of matching Unicode-script characters across all scripts in the rule:

```yaml
type: unicode_script
scripts: ["Cyrillic", "Han", "Hangul", "Hiragana", "Katakana"]
allowed_script_characters: 1
```

With `allowed_script_characters: 1`, one matching script character anywhere in the complete name is allowed. The second matching script character triggers the rule, even when both characters belong to different configured scripts. Examples: `Player乂`, `Player漢`, or `PlayerЖ` are allowed; `Player乂乂`, `Player乂漢`, and `Player乂Ж` match the rule. Set it to `0` to disable this tolerance.

You can additionally define an exact per-character ceiling:

```yaml
allowed_script_characters: 1
allowed_occurrences:
  "乂": 1
```

`allowed_occurrences` is applied in addition to the generalized total limit and cannot increase `allowed_script_characters`. It can therefore make selected characters stricter. If `allowed_script_characters` is omitted entirely, the original exact-exception behavior remains: a listed character is allowed up to its configured count, while any other character from the blocked scripts triggers immediately. Counts are exact Unicode code-point counts across the complete player name.

### `characters`

Every Unicode character in `characters:` is treated literally. Regex escaping is not needed. Save the YAML file as UTF-8. `allowed_occurrences` is also supported here; for example, a blocked `★` can be tolerated once but matched from the second occurrence onward.

### `sequences`

Matches a literal string/sequence. Can be case-sensitive or case-insensitive.

### `terms`

Matches blocked terms after optional Unicode normalization, case folding, Leetspeak conversion and removal of separators/punctuation. This is suitable for names such as `b.a.d`, `b_a_d`, or Leetspeak variants when your configured mapping resolves them.

### `regex`

Runs your own regex presets. The project uses the third-party `regex` module, which provides stronger Unicode support than Python's built-in `re` module.

## Warning/action flow

For each player and each matching rule:

1. The configured number of warnings is sent first.
2. Warning messages respect `interval_seconds`.
3. After the warning count is exhausted, the configured action is executed.
4. `cooldown_seconds` prevents repeated action calls in a tight loop.
5. When the player leaves the server, his rule state is cleared by default. A rejoin therefore starts a fresh warning cycle.

Available message placeholders:

- `{name}`
- `{steam_id}`
- `{faction}`
- `{rule}`
- `{match}`
- `{count}`
- `{max_count}`
- `{remaining}`

## Player response field names

If `GET /v1/players` does not use `id`, `name`, and `faction`, edit:

```yaml
rcon:
  player_fields:
    steam_id: "steamId"
    name: "name"
    faction: "faction"
```

Dotted nested paths are supported, for example `player.id`.

The parser also has fallbacks for common ID/name/team key variants.

## Logs

- `logs/bot.log`: runtime/service messages
- `logs/actions.log`: one JSON object per warning/kick/ban event

Example `actions.log` event:

```json
{"event":"kick","steam_id":"7656...","name":"Example","rule":"regex_presets","reason":"Gesperrtes Namensmuster","timestamp":"2026-09-16T20:00:00+0200"}
```

Configure size rotation:

```yaml
logging:
  rotation:
    mode: size
    max_bytes: 5242880
    backups: 10
```

Or daily/time rotation:

```yaml
logging:
  rotation:
    mode: time
    when: midnight
    interval: 1
    backups: 14
    utc: false
```

## Recommended rollout

Keep `bot.dry_run: true` for the first live test and inspect `logs/actions.log`. Once your script, symbol, term and regex rules are producing the expected matches, switch it to `false`.
