# Security and data handling

easy-tv grants control over a TV through an authorized ADB host. Use a trusted private network and a separate random bridge token. It binds to loopback by default, checks a configured client CIDR allowlist, compares bearer tokens in constant time, rejects browser Origin headers, and bounds request sizes. No authentication token is accepted in a query string.

The built-in server uses plaintext HTTP and is not an internet-facing production server. Do not port-forward it. Use TLS or a private VPN if the network is not trusted; configure proxy access deliberately since the server sees the proxy's client address. Debugging exposes powerful device access outside this package as well; protect the Pi and ADB authorization keys.

`init` generates a fresh token. API keys and tokens are stored outside the repository with mode 0600. `configure-gemini` uses a hidden prompt; it never sends the key to validate it. Do not paste credentials into commands, screenshots, blog posts or issue reports. Personalized shortcuts contain the bridge token. Keep them private. Replacing `bridge-token` with a new random value and restarting the service revokes previous clients.

Gemini receives natural-language commands, observed TV state, recent in-memory conversational context, and optional UI text/screenshots when inspection is requested. Media names, account names and other on-screen information may be present. Exact offline commands and taught routine replay do not require a Gemini request. This project does not provide an offline model.

Runtime files in the configuration directory include learned routines, a bounded catalogue cache and rotating request logs. Logs contain command text and client addresses but intentionally omit Authorization headers and API keys. Screen inspections are not deliberately persisted by the agent, although Android's UI dump file can remain on the TV. Host backup software and provider-side retention are outside this project's control.

The model requests validated named tools rather than arbitrary shell commands. App and provider URL allowlists, action budgets, cancellation and verification limit its behavior. Model-generated actions can still be wrong; this is experimental home automation. Teach checkpoints cannot distinguish every dynamic UI state.

For a suspected vulnerability, do not attach credentials or screen captures with personal information. Use a private reporting channel supplied by the repository maintainer when the project is published. No public security contact is configured in this unpublished starter repository.
