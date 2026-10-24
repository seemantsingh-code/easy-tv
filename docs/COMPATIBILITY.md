# Compatibility and evidence

“Tested” below describes the source prototype on one home setup. The extracted portable package has local automated validation, not a fresh physical-TV deployment. No claim of universal Android TV or app support is made.

| Component / feature | Prototype evidence | Package default / limitation |
| --- | --- | --- |
| Xiaomi MiTV-AXSO0, Android 9 | ADB navigation and input switching worked | `xiaomi-android9` profile; configurable input intents and IDs |
| Raspberry Pi always-on host | Existing bridge ran on Pi | Fresh Python 3.11+ venv; Linux systemd user-service installer supplied, deployment untested |
| iPhone Shortcuts and Siri | HTTP shortcut and spoken requests worked | One assistant-independent HTTP endpoint; new unsigned template is structurally tested only |
| PlayStation | HDMI 1 switching worked and was recorded | Alias → HDMI 1; change aliases/IDs for your wiring |
| FLauncher | Teach baseline and screen comparison tested | `me.efesser.flauncher`, Home + 6 Up + 6 Left; screenshot comparison ignores clock |
| Netflix | Dune (2021) playback confirmed | `com.netflix.ninja/.MainActivity`, string `source=30`; other versions may differ |
| Apple TV | Silo playback confirmed after manual profile selection | Native content links; DRM blocks screenshots and media title can be hidden |
| Prime Video / Hotstar | Installed/configured provider adapters | End-to-end playback not confirmed in this session; account/profile/UI differences apply |
| Stremio | Installed/configured fallback; legacy app version 1.4.13 observed | No universal playback guarantee; own add-ons, stream/episode choices may be required |
| YouTube / PatchWall | App launch mappings included | No new cross-platform catalogue playback adapter implied |
| India availability | Public JustWatch data used for tested titles | `IN`; other country codes configurable but untested |
| Gemini | Natural-language TV control worked | `gemini-3.5-flash-lite`; configurable model/key and action budgets |
| Other Android TV / Google TV devices | Untested | Generic profile; supply input IDs, app packages and validate permissions/launcher |
| Android phone / Echo client | Future work | HTTP contract documented; no client included |

Subscription-first means a configured provider has a reported subscription/free offer. It does not inspect billing or prove the signed-in account owns that subscription. Amazon add-on channels are excluded from base Prime. Rent/buy offers are not selected automatically.

Apple TV's hidden title is significant: an active Apple TV media session can confirm app playback while leaving title verification unavailable. The code reports that distinction. Opening Netflix or a title detail screen alone does not establish playback.

The catalogue adapter reads public HTML data rather than a private service API. Layout changes, request limits, redirects and regional differences can break it. Errors are reported instead of guessed availability. Check applicable service terms before operating a public or commercial installation. No provider partnership is implied.
