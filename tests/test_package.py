"""Portable-package behavior; no live TV, model or catalogue requests."""
import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from importlib.resources import files
from easy_tv import cli, config
from easy_tv.content_search import ContentSearch
from easy_tv.justwatch_public import parse_page
from easy_tv.teach import TeachManager
from easy_tv.tv_tools import TVTools, declarations
from test_content_search import public_page


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.dict(os.environ, {'EASY_TV_HOME': str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)

    def write(self, overrides):
        config.private_write(self.root / 'config.json', json.dumps(overrides))

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_tested_defaults_have_no_address_or_credentials(self):
        value = config.load()
        self.assertEqual(value['tv']['address'], '')
        self.assertEqual(value['profile'], 'xiaomi-android9')
        self.assertEqual(value['content']['region'], 'IN')
        self.assertEqual(value['server']['port'], 8767)
        self.assertFalse((self.root / 'bridge-token').exists())
        self.assertNotIn('api_key', value['gemini'])

    def test_generic_profile_removes_hardware_specific_mappings(self):
        self.write({'profile': 'generic-androidtv'})
        value = config.load()
        self.assertEqual(value['tv']['input_ids'], {})
        self.assertIsNone(value['tv']['launcher_package'])
        self.assertIsNone(value['tv']['input_popup_action'])
        self.assertIsNone(value.get('tested_device'))

    def test_registry_override_replaces_default_entries(self):
        self.write({'tv': {'input_ids': {'4': 'vendor/.Input/HDMI4'}, 'input_aliases': {'console': 4}, 'input_labels': {'4': 'Console'}}})
        value = config.load()['tv']
        self.assertEqual(list(value['input_ids']), ['4'])
        from easy_tv import tv
        self.assertIn('vendor%2F.Input%2FHDMI4', tv.plan('console')[-1])
        with self.assertRaises(ValueError):
            tv.plan('playstation')

    def test_custom_app_registry_can_remove_all_providers(self):
        self.write({'apps': {'custom': 'org.example.tv'}, 'app_names': {},
                    'content': {'providers': [], 'fallback': None}})
        self.assertEqual(config.load()['apps'], {'custom': 'org.example.tv'})

    def test_reject_invalid_settings(self):
        invalid = [{'server': {'port': 0}}, {'server': {'allowed_cidrs': ['bad']}},
                   {'content': {'region': 'india'}}, {'content': {'providers': ['invented']}},
                   {'content': {'cache_seconds': -1}}, {'agent': {'max_actions': 1.5}},
                   {'tv': {'input_aliases': {'console': 99}}}, {'tv': {'home_up_presses': -1}},
                   {'tv': {'input_ids': []}}, {'profile': '../file'}]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                self.write(overrides)
                with self.assertRaises(ValueError):
                    config.load()

    def test_generic_profile_has_no_empty_enum_tools(self):
        self.write({'profile': 'generic-androidtv'})
        tools = declarations()
        self.assertNotIn('select_input', [tool['name'] for tool in tools])
        for tool in tools:
            for prop in tool['parameters']['properties'].values():
                self.assertNotEqual(prop.get('enum'), [])

    def test_model_tools_respect_provider_and_fallback_configuration(self):
        self.write({'content': {'providers': ['apple tv'], 'fallback': None}})
        tools = {tool['name']: tool for tool in declarations()}
        self.assertNotIn('search_stremio', tools)
        self.assertEqual(tools['open_content']['parameters']['properties']['provider']['enum'], ['auto', 'apple tv'])

    def test_supported_adb_endpoints(self):
        for value in ['192.0.2.10:5555', 'tv.local:5555', '[2001:db8::1]:5555']:
            self.assertEqual(config.validate_address(value), value)

    def test_invalid_endpoints(self):
        for value in ['', 'tv.local', 'tv.local:0', 'tv.local:65536', 'tv.local:5555;reboot', '999.1.1.1:5555', 'tv host:5555']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.validate_address(value)

    def test_init_is_private_and_does_not_connect_or_start(self):
        with patch.object(cli.subprocess, 'run') as run:
            code, _, _ = self.run_cli('init', '--tv', 'tv.local:5555')
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertIn('127.0.0.0/8', config.load()['server']['allowed_cidrs'])
        for name in ['config.json', 'bridge-token']:
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600)
        self.assertGreaterEqual(len((self.root / 'bridge-token').read_text().strip()), 32)

    def test_init_refuses_overwrite_and_keeps_token(self):
        self.run_cli('init', '--tv', 'tv.local:5555')
        before = (self.root / 'bridge-token').read_text()
        code, _, _ = self.run_cli('init', '--tv', 'other.local:5555')
        self.assertEqual(code, 1)
        self.assertEqual(config.load()['tv']['address'], 'tv.local:5555')
        self.run_cli('init', '--force', '--tv', 'other.local:5555')
        self.assertEqual((self.root / 'bridge-token').read_text(), before)

    def test_invalid_init_writes_nothing(self):
        code, _, _ = self.run_cli('init', '--tv', 'tv.local:5555', '--allow-cidr', 'bad')
        self.assertEqual(code, 1)
        self.assertFalse((self.root / 'config.json').exists())

    def test_init_generic_profile(self):
        code, _, _ = self.run_cli('init', '--tv', 'tv.local:5555', '--profile', 'generic-androidtv')
        self.assertEqual(code, 0)
        self.assertEqual(config.load()['tv']['input_ids'], {})

    def test_configure_gemini_never_prints_key_or_contacts_network(self):
        with patch.object(cli.getpass, 'getpass', return_value='dummy-package-test-key'), patch.object(cli, 'urlopen') as network:
            code, out, err = self.run_cli('configure-gemini', '--model', 'gemini-test')
        self.assertEqual(code, 0)
        self.assertNotIn('dummy-package-test-key', out + err)
        network.assert_not_called()
        self.assertEqual(config.load()['gemini']['model'], 'gemini-test')
        self.assertEqual(stat.S_IMODE((self.root / 'gemini-key').stat().st_mode), 0o600)

    def test_doctor_never_touches_tv_or_network(self):
        with patch.object(cli.subprocess, 'run') as run, patch.object(cli, 'urlopen') as network:
            code, out, _ = self.run_cli('doctor')
        self.assertEqual(code, 0)
        self.assertIn('No ADB or network calls made', out)
        run.assert_not_called()
        network.assert_not_called()

    def test_connect_only_uses_configured_endpoint(self):
        self.write({'tv': {'address': 'tv.local:5555'}})
        with patch.object(cli.subprocess, 'run') as run:
            self.assertEqual(self.run_cli('connect')[0], 0)
        self.assertEqual(run.call_args_list[0].args[0], ['adb', 'connect', 'tv.local:5555'])
        self.assertEqual(run.call_args_list[1].args[0], ['adb', '-s', 'tv.local:5555', 'get-state'])

    def test_service_install_does_not_start_by_default(self):
        self.write({'tv': {'address': 'tv.local:5555'}})
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'xdg')}), patch.object(cli.sys, 'platform', 'linux'), patch.object(cli.subprocess, 'run') as run:
            self.assertEqual(self.run_cli('install-service')[0], 0)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ['systemctl', '--user', 'daemon-reload'])
        unit = (self.root / 'xdg/systemd/user/easy-tv.service').read_text()
        self.assertIn('-m easy_tv --home', unit)
        self.assertNotIn('sudo', unit)

    def test_teach_limits_use_selected_configuration_directory(self):
        self.write({'teach': {'max_steps': 1, 'max_record_seconds': 2, 'max_replay_seconds': 3}})
        manager = TeachManager(root=self.root, adapter=Mock())
        self.assertEqual((manager.max_steps, manager.max_record_seconds, manager.max_replay_seconds), (1, 2, 3))
        with self.assertRaises(ValueError):
            manager._validate({'start': {}, 'steps': [{'key': 'HOME', 'wait': 0.1}] * 2})

    def test_fallback_disabled_prevents_stremio_tool_before_adb(self):
        self.write({'content': {'fallback': None}})
        with patch('easy_tv.tv_tools.tv.execute') as execute:
            tools = TVTools(Mock(), threading.Lock())
            with self.assertRaises(ValueError):
                tools.call('search_stremio', {'query': 'Silo'}, threading.Event())
        execute.assert_not_called()

    def search_fixture(self, providers=None, fallback='stremio'):
        self.write({'content': {'providers': providers if providers is not None else ['netflix', 'apple tv'], 'fallback': fallback}})
        search = ContentSearch(self.root)
        title = {'id': 'a' * 16, 'title': 'Example', 'type': 'movie', 'year': '2025', 'imdb_id': 'tt1234567',
                 'offers': [{'provider': 'netflix', 'access': 'subscription'}, {'provider': 'apple tv', 'access': 'subscription'}]}
        search.cache['any:example'] = {'at': time.time(), 'result': {'region': 'IN', 'matches': [title]}}
        return search, title

    def test_provider_priority_is_configurable(self):
        search, title = self.search_fixture(['apple tv', 'netflix'])
        self.assertEqual(search.choose(title['id'])[1], 'apple tv')

    def test_unconfigured_subscription_is_not_selected(self):
        search, title = self.search_fixture([])
        self.assertEqual(search.choose(title['id'])[1], 'stremio')

    def test_disabled_fallback_rejects_explicit_and_automatic_use(self):
        search, title = self.search_fixture([], None)
        with self.assertRaisesRegex(ValueError, 'disabled'):
            search.choose(title['id'])
        with self.assertRaises(ValueError):
            search.choose(title['id'], 'stremio')

    def test_cache_never_reuses_different_country(self):
        search, title = self.search_fixture()
        search.settings['region'] = 'US'
        with self.assertRaises(ValueError):
            search.title(title['id'])
        with patch('easy_tv.justwatch_public.search_public', return_value={'region': 'US', 'matches': []}) as lookup:
            search.search('Example')
        lookup.assert_called_once_with('Example', 'any', 'US')

    def test_public_parser_uses_configured_region(self):
        result = parse_page(public_page().replace('IN', 'US').replace('/in/', '/us/'), 'Silo', region='US')
        self.assertEqual(result['region'], 'US')
        self.assertEqual(result['matches'][0]['offers'][0]['provider'], 'apple tv')
        self.assertTrue(result['sources'][-1]['url'].startswith('https://www.justwatch.com/us/'))

    def test_shortcut_is_credential_free_and_has_no_shell_action(self):
        template = files('easy_tv').joinpath('resources/shortcuts/easy-tv.unsigned.shortcut').read_bytes()
        workflow = plistlib.loads(template)
        actions = workflow['WFWorkflowActions']
        self.assertEqual(workflow['WFWorkflowName'], 'easy-tv')
        self.assertEqual(len(workflow['WFWorkflowImportQuestions']), 2)
        self.assertIn(b'SET_YOUR_BRIDGE_TOKEN', template)
        self.assertFalse(any('shell' in a['WFWorkflowActionIdentifier'] for a in actions))
        request = actions[3]['WFWorkflowActionParameters']
        self.assertEqual(request['WFHTTPMethod'], 'POST')
        self.assertEqual(request['WFHTTPBodyType'], 'JSON')
        ids = {a['WFWorkflowActionParameters']['UUID'] for a in actions}
        self.assertEqual(len(ids), len(actions))
        # Every magic variable refers to an actual action UUID.
        def walk(value):
            if isinstance(value, dict):
                if 'OutputUUID' in value:
                    self.assertIn(value['OutputUUID'], ids)
                for child in value.values(): walk(child)
            elif isinstance(value, list):
                for child in value: walk(child)
        walk(workflow)

    def test_shortcut_export_does_not_read_private_token(self):
        output = self.root / 'public.shortcut'
        self.assertEqual(self.run_cli('export-shortcut', '--output', str(output))[0], 0)
        self.assertIn(b'SET_YOUR_BRIDGE_TOKEN', output.read_bytes())
        self.assertEqual(self.run_cli('export-shortcut', '--output', str(output))[0], 1)


if __name__ == '__main__':
    unittest.main()
