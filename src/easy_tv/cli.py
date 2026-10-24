"""easy-tv administration; no TV or network actions at import time."""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from . import __version__
from .config import home, load, path_for, private_write, validate_address


def initialize(args):
    target = home() / 'config.json'
    if target.exists() and not args.force:
        raise ValueError('Configuration already exists. Choose another --home or use init --force.')
    address = args.tv or input('TV hostname or IP and ADB port (e.g. tv.local:5555): ').strip()
    validate_address(address)
    settings = {'version': 1, 'profile': args.profile,
                'tv': {'address': address},
                'server': {'listen': args.listen, 'port': args.port,
                           'allowed_cidrs': ['127.0.0.0/8', '::1/128'] + (args.allow_cidr or [])}}
    # Validate before writing, including invalid CIDRs/port numbers.
    from .config import merge, validate
    defaults = load()
    from importlib.resources import files
    defaults = merge(defaults, json.loads(files('easy_tv').joinpath('resources/profiles/' + args.profile + '.json').read_text()))
    validate(merge(defaults, settings))
    private_write(target, json.dumps(settings, indent=2) + '\n')
    token_path = path_for(load()['server']['token_file'])
    if not token_path.exists():
        private_write(token_path, secrets.token_urlsafe(32) + '\n')
    print('Created configuration and authentication token in ' + str(home()))
    print('No connection or service was started. Next: easy-tv configure-gemini; easy-tv connect; easy-tv serve')


def configure_gemini(args):
    settings = load()
    key = getpass.getpass('Gemini API key (hidden): ').strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError('Enter a nonempty API key without whitespace.')
    if args.model:
        import re
        if not re.fullmatch(r'gemini-[A-Za-z0-9.-]{1,100}', args.model):
            raise ValueError('Invalid model name.')
        path = home() / 'config.json'
        overrides = json.loads(path.read_text()) if path.exists() else {}
        overrides.setdefault('gemini', {})['model'] = args.model
        private_write(path, json.dumps(overrides, indent=2) + '\n')
    private_write(path_for(settings['gemini']['api_key_file']), key + '\n')
    print('Saved API key privately. No Gemini request was made.')


def send(args):
    settings = load()['server']
    host = settings['listen']
    host = '127.0.0.1' if host == '0.0.0.0' else ('::1' if host == '::' else host)
    host = '[' + host + ']' if ':' in host else host
    token = path_for(settings['token_file']).read_text().strip()
    request = Request('http://%s:%s/command' % (host, settings['port']),
                      data=json.dumps({'command': ' '.join(args.command)}).encode(),
                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        response = urlopen(request, timeout=25)
    except HTTPError as exc:
        response = exc
    with response:
        result = json.load(response)
    print(json.dumps(result, indent=2) if args.json else result.get('message', result.get('error', 'No response.')))
    return 0 if result.get('ok') else 1


def install_service(args):
    if sys.platform != 'linux':
        raise ValueError('The user service installer requires Linux with systemd.')
    if any(c in str(home()) + sys.executable for c in '\n\r%'):
        raise ValueError('Service paths cannot contain control characters or percent signs.')
    # systemd uses double-quoted arguments rather than shell quoting.
    def quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'
    executable = str(Path(sys.executable).absolute())
    unit = ('[Unit]\nDescription=easy-tv Android TV controller\nAfter=network-online.target\n\n'
            '[Service]\nType=simple\nExecStart=' + quote(executable) + ' -m easy_tv --home ' + quote(home()) + ' serve\n'
            'Restart=on-failure\nRestartSec=5\nUMask=0077\nNoNewPrivileges=yes\n\n'
            '[Install]\nWantedBy=default.target\n')
    target = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user/easy-tv.service'
    if target.exists() and not args.force:
        raise ValueError('easy-tv.service already exists. Use --force to replace it.')
    private_write(target, unit)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    if args.start:
        subprocess.run(['systemctl', '--user', 'enable', '--now', 'easy-tv.service'], check=True)
    print('Installed ' + str(target))
    if not args.start:
        print('Start when ready: systemctl --user enable --now easy-tv.service')
    print('For startup without a login session, an administrator can enable user lingering; see docs/INSTALLATION.md.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='easy-tv: configurable Android TV automation over ADB.')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--home', help='Separate configuration/state directory (or EASY_TV_HOME).')
    commands = parser.add_subparsers(dest='action', required=True)
    init = commands.add_parser('init', help='Create configuration; does not connect or start services.')
    init.add_argument('--tv', help='TV hostname-or-IP:ADB-port')
    init.add_argument('--profile', choices=['xiaomi-android9', 'generic-androidtv'], default='xiaomi-android9')
    init.add_argument('--listen', default='127.0.0.1')
    init.add_argument('--port', type=int, default=8767)
    init.add_argument('--allow-cidr', action='append', help='Allowed client network; repeat as needed.')
    init.add_argument('--force', action='store_true')
    gemini = commands.add_parser('configure-gemini', help='Save an API key using a hidden prompt.')
    gemini.add_argument('--model')
    commands.add_parser('connect', help='Explicitly connect ADB; approve the prompt on the TV.')
    commands.add_parser('serve', help='Start the authenticated HTTP controller in the foreground.')
    sender = commands.add_parser('send', help='Send a command to the locally configured bridge.')
    sender.add_argument('command', nargs='+')
    sender.add_argument('--json', action='store_true')
    commands.add_parser('token', help='Display your private bridge token for client setup.')
    commands.add_parser('doctor', help='Check local settings and dependencies without touching the TV.')
    service = commands.add_parser('install-service', help='Install a separate Linux user service.')
    service.add_argument('--start', action='store_true')
    service.add_argument('--force', action='store_true')
    shortcut = commands.add_parser('export-shortcut', help='Export an unsigned, credential-free Apple Shortcuts template.')
    shortcut.add_argument('--output', default='easy-tv.unsigned.shortcut')
    args = parser.parse_args(argv)
    if args.home:
        os.environ['EASY_TV_HOME'] = str(Path(args.home).expanduser().resolve())
    try:
        if args.action == 'init':
            initialize(args)
        elif args.action == 'configure-gemini':
            configure_gemini(args)
        elif args.action == 'token':
            print(path_for(load()['server']['token_file']).read_text().strip())
        elif args.action == 'connect':
            settings = load()['tv']
            validate_address(settings['address'])
            subprocess.run([settings['adb'], 'connect', settings['address']], check=True)
            subprocess.run([settings['adb'], '-s', settings['address'], 'get-state'], check=True)
        elif args.action == 'serve':
            validate_address(load()['tv']['address'])
            from .pi_server import serve
            serve()
        elif args.action == 'send':
            return send(args)
        elif args.action == 'install-service':
            validate_address(load()['tv']['address'])
            install_service(args)
        elif args.action == 'export-shortcut':
            from importlib.resources import files
            target = Path(args.output).expanduser()
            if target.exists():
                raise ValueError('Output already exists; choose a new filename.')
            target.write_bytes(files('easy_tv').joinpath('resources/shortcuts/easy-tv.unsigned.shortcut').read_bytes())
            print('Exported unsigned template. See docs/APPLE_SHORTCUTS.md for signing or manual iPhone setup.')
        elif args.action == 'doctor':
            settings = load()
            print('Configuration: ' + str(home()))
            print('Profile: ' + settings['profile'])
            print('ADB executable: ' + (shutil.which(settings['tv']['adb']) or 'MISSING'))
            print('TV address: ' + ('configured' if settings['tv']['address'] else 'MISSING; run init'))
            print('Bridge token: ' + ('present' if path_for(settings['server']['token_file']).exists() else 'MISSING'))
            from .gemini_agent import configuration
            try:
                _, model = configuration()
                print('Gemini: configured (' + model + '); not contacted')
            except RuntimeError:
                print('Gemini: not configured; exact commands still work')
            from importlib.util import find_spec
            print('Pillow: ' + ('available' if find_spec('PIL') else 'MISSING'))
            print('No ADB or network calls made.')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, URLError) as exc:
        print('easy-tv: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
