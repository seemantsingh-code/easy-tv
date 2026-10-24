# Configuration

Run `easy-tv init` first. Edit its `config.json`; restart the service after changes. Missing fields inherit the packaged defaults plus selected profile. Unknown fields are currently ignored. Configuration version is `1`.

```json
{
  "version": 1,
  "profile": "xiaomi-android9",
  "tv": {"address": "tv.local:5555"},
  "server": {
    "listen": "0.0.0.0",
    "port": 8767,
    "allowed_cidrs": ["192.0.2.0/24", "127.0.0.0/8", "::1/128"]
  },
  "gemini": {"model": "gemini-3.5-flash-lite"},
  "content": {
    "region": "IN",
    "providers": ["netflix", "prime video", "hotstar", "apple tv"],
    "fallback": "stremio",
    "cache_seconds": 14400
  }
}
```

Addresses above are placeholders. A TV address accepts IPv4, DNS names or bracketed IPv6, always with a port. HTTP serving currently uses IPv4; use an IPv4 listen address. The CLI adds loopback networks when initializing. A manually edited allowlist applies to loopback clients too; include loopback if you want `easy-tv send` on the Pi. Changing the allowlist can prevent local requests; this is intentional.

| Setting | Purpose / default |
| --- | --- |
| `tv.adb` | Executable name or absolute path; `adb` |
| `tv.input_popup_action` | Xiaomi Inputs intent; null uses `KEYCODE_TV_INPUT` |
| `tv.input_ids` | HDMI number strings mapped to complete Android TV passthrough IDs |
| `tv.input_aliases` | Alias → HDMI number, e.g. `{"playstation":1}` |
| `tv.input_labels` | HDMI number strings → friendly feedback labels |
| `tv.launcher_package` | FLauncher in tested profile; null in generic profile |
| `tv.home_up_presses`, `tv.home_left_presses` | Home normalization; 6/6 tested, 0/0 generic |
| `tv.home_settle_seconds` | Delay after Home; 0.8 |
| `apps` | Name → Android package registry; see shipped defaults |
| `app_names` | Name → display name |
| `netflix.component`, `netflix.source` | Tested explicit Netflix TV entry point and string source extra |
| `server.token_file` | `bridge-token`, relative to configuration directory |
| `gemini.api_key_env` | `GEMINI_API_KEY` |
| `gemini.api_key_file` | `gemini-key`; fallback when environment variable is absent |
| `content.region` | Uppercase country code; `IN` tested, other regions untested |
| `content.providers` | Ordered subset of implemented provider adapters, which must also be in `apps` |
| `content.fallback` | `stremio` or null; null disables automatic and explicit Stremio content fallback |
| `content.cache_seconds` | Availability TTL; 14400 |
| `agent.max_rounds` | Model/action cycles; 8 |
| `agent.max_actions` | Tool calls; 20 |
| `agent.max_seconds` | Request budget; 90; a pending network call may take its timeout to stop |
| `agent.max_inspections` | Screen inspections; 3 |
| `agent.response_wait_seconds` | Initial synchronous wait; 10, then use Status |
| `teach.max_steps` | Physical presses; 80 |
| `teach.max_record_seconds` | Recording time; 180 |
| `teach.max_replay_seconds` | Replay time; 300 |
| `teach.screen_timeout` | Checkpoint wait; 8 |

Maps `apps`, `app_names`, `tv.input_ids`, `tv.input_aliases` and `tv.input_labels` **replace** the corresponding default map when specified. Other sections merge. Supply all wanted entries. Removing a provider app also requires removing it from `content.providers` and disabling Stremio fallback if removing Stremio.

To adapt inputs, inspect your own TV with `adb -s YOUR_TV:PORT shell dumpsys tv_input` and configure the reported passthrough IDs. Do not assume another manufacturer's numbering matches DroidLogic. Teach mode requires physical input event permission and an observable, repeatable home screen. The current screenshot comparison tuning is specific to the tested launcher; other launchers need validation.

Known subscription adapters are Netflix, Prime Video, Hotstar and Apple TV. Adding an arbitrary package to `apps` enables launch control, not a new catalogue/native-playback adapter. Regional Amazon link hosts may require extending the provider URL allowlist in code; do not weaken it to arbitrary hosts.

Run `easy-tv doctor` for offline dependency/configuration checks. Set `GEMINI_MODEL` to override the configured model temporarily. The CLI reads configuration before loading the TV modules; restart an existing Python process to apply app registry changes.
