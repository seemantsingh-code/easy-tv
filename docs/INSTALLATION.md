# Installation and operations

The Raspberry Pi runs the service; a Mac is not needed to keep it alive. Python 3.11+ and ADB are required. On Raspberry Pi OS, prerequisites can be installed with `sudo apt install python3-venv adb`; this project's installer does not run sudo or change system packages.

From the checkout, run `sh scripts/install-pi.sh`. It creates `~/.local/share/easy-tv/venv`, refuses to overwrite an existing environment, and prints next commands. It does not start a service, connect ADB or migrate any old setup. Override the destination with `EASY_TV_VENV` when needed. Downloads require internet access.

Alternatively, create a fresh virtual environment and `python -m pip install /path/to/this/checkout` or install the locally built wheel. easy-tv is not currently published to a package registry. Do not assume `pip install easy-tv` from a public index refers to this project.

Use the installed `easy-tv` executable's full path or activate the environment. Run `init`, `configure-gemini`, `connect`, then `serve` as described in the README. Network debugging must already be enabled on your TV. The tested Android 9 device uses TCP ADB port 5555 and an authorization prompt. Other TVs may require `adb pair` and a different port; follow the TV's own instructions and configure that endpoint. ADB authorization keys remain in the operating user's normal ADB directory; they are not part of this package.

## Linux service

After foreground testing succeeds, use the executable from the installation virtual environment:

```sh
easy-tv install-service
systemctl --user enable --now easy-tv.service
systemctl --user status easy-tv.service
journalctl --user -u easy-tv.service -n 50
```

`install-service --start` combines installation and explicit startup. The installer refuses to replace an existing easy-tv unit unless `--force` is passed. The unit embeds the active Python executable and configuration directory, so use the intended environment and `--home` option when installing it.

To keep a user service running after logout and start it on boot, an administrator may need `sudo loginctl enable-linger "$USER"`. This is an explicit system policy change; the installer does not apply it. A user systemd session must exist for `systemctl --user` to work.

Stop or remove this installation:

```sh
systemctl --user disable --now easy-tv.service
# Then remove only the easy-tv unit, its virtual environment and its configuration
# if you no longer need them. Configuration contains secrets and learned routines.
```

Do not reuse an existing TV controller's port, configuration directory, virtual environment or service name. The default new bridge uses port 8767. Two running controllers can still send conflicting commands to the same TV; stop one controller when testing the other.

## Teach mode

Say `teach` through the shortcut. The controller returns to its configured home baseline, then listens to remote button events through ADB. Perform the steps with the physical remote, say `save movie night`, then invoke `movie night`. Use `lessons` to list names, `replace movie night` to explicitly replace an existing lesson, and `cancel` to stop. A changed screen checkpoint stops replay. `save` requires a name unless the recording was started with one.

Checkpoint matching helps avoid replay on a different screen; it cannot guarantee that every app's changing content or hidden focus is observable. Validate your own routine before relying on it. DRM and missing UI metadata limit inspection.
