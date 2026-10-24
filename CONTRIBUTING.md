# Contributing

Use a fresh virtual environment. Install with `python -m pip install -e . build`, then run `python -m unittest discover -s tests -v` and `python -m build`. Tests use mocked TV/model/catalogue operations and an ephemeral loopback HTTP server. No TV address or credentials are needed.

For a new device, contribute a sanitized profile and compatibility notes with firmware/app versions and what was actually verified. For a new provider, add URL validation, regional availability mapping, native-link behavior and meaningful mocked tests. Package names alone do not implement playback.

Keep secrets, network identifiers, learned routines, logs, signed shortcuts and recordings out of commits. Rebuild the unsigned template with `python tools/build_shortcut.py`; it intentionally reads no local configuration. Do not sign a public template with an individual's Apple account as part of the build.

Record physical-device testing separately from unit tests. Distinguish app launch, app playback and verified title playback in messages and documentation. Avoid changes that replay failed actions, bypass checkpoint validation or execute arbitrary model-provided shell commands.

For a shareable release, stage intended files in Git and run `python tools/build_release.py`. It also creates a repository ZIP and removes local uid/gid/account metadata from setuptools’ source tarball. Plain `python -m build` does not perform that privacy normalization. Review archive contents before publishing.
