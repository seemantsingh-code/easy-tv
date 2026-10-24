# Apple Shortcuts client

Use one shortcut named **easy-tv**. Ask Siri to run easy-tv, then give the request when prompted. Examples: “PlayStation”, “find and play Silo”, “what is playing?”, “status”, “cancel”, “teach”, “save movie night”, “lessons”. Siri does not automatically forward every arbitrary TV instruction to this project.

The iPhone and Pi must be on the same trusted network. The server must listen on the Pi's LAN interface, and `server.allowed_cidrs` must include the phone's network. The name `easy-tv.local` below is a placeholder: use your own Pi's hostname or reserved IP. Read your private token with `easy-tv token` on the Pi; do not share a personalized shortcut publicly.

## Create directly on iPhone (no Mac needed)

1. Create a new shortcut named easy-tv. Add **Ask for Input**: prompt “What should the TV do?”, input type Text. Siri can collect the spoken response.
2. Add **URL**: `http://YOUR_PI:8767/command`.
3. Add **Get Contents of URL**. Use the URL above; set method **POST**. Add header `Authorization` with value `Bearer YOUR_BRIDGE_TOKEN` (one space after Bearer). Set request body **JSON**, add text field `command`, and set its value to the magic variable **Provided Input** from step 1.
4. Add **Get Dictionary Value**, key `message`, from Contents of URL.
5. Add **Show Result** using Dictionary Value. Optionally use Speak Text for spoken output when running by tapping.

Run it by tapping once and allow local network access if prompted. Try “inputs”, then an AI request. There is no Run Shell Script action. Keep the existing working shortcut while testing a new installation.

If a long request returns a progress message, run the same shortcut again with “status”. To stop it, use “cancel”; previously completed TV actions remain applied.

## Generated template (optional Mac path)

The repository includes a credential-free **unsigned** plist template. iOS may reject an unsigned generated file. Its structure is tested, but the new template has not been imported on a physical iPhone; manual setup above is the supported fallback.

```sh
easy-tv export-shortcut --output easy-tv.unsigned.shortcut
# macOS: sign locally with Apple's Shortcuts CLI; this can require Apple sign-in/network access.
shortcuts sign --mode anyone --input easy-tv.unsigned.shortcut --output easy-tv.shortcut
```

AirDrop the signed file to your iPhone and open it with Shortcuts. Answer its URL and token import questions, or edit the first two Text actions if your OS does not show those questions. The URL must end in `/command`. Signing can embed the signer's identity; review files before public distribution. Never commit signed or personalized exports. Put them in an ignored `exports/` directory.

## API contract for future clients

```http
POST /command
Authorization: Bearer YOUR_BRIDGE_TOKEN
Content-Type: application/json

{"command":"PlayStation"}
```

A successful response includes `ok` and a user-facing `message`. An AI job may also include `running: true`; poll with the command `status`. Errors include `error` and `message`; clients must check HTTP status and `ok`, not announce success unconditionally. Authenticated `GET /health` checks the bridge without operating the TV. Browser-origin requests are rejected. No Android client is bundled yet.

Reference: [Apple's guide to HTTP API requests in Shortcuts](https://support.apple.com/guide/shortcuts/request-your-first-api-apd58d46713f/ios).
