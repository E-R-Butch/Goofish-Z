import importlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from goofish_z.core import browser_cookie, session
from goofish_z.core.browser import _cookies_to_playwright

login = importlib.import_module('goofish_z.commands.auth.login')
FUTURE_EXPIRY = int(time.time()) + 3600


def entries():
    return [
        {'name': 'unb', 'value': 'synthetic-user', 'domain': '.goofish.com', 'path': '/'},
        {'name': '_m_h5_tk', 'value': 'synthetic-goofish_0', 'domain': '.goofish.com', 'path': '/'},
        {'name': 'sgcookie', 'value': 'synthetic-goofish', 'domain': '.goofish.com', 'path': '/'},
        {'name': 'sgcookie', 'value': 'synthetic-taobao', 'domain': '.taobao.com', 'path': '/'},
        {'name': 'sgcookie', 'value': 'synthetic-other-path', 'domain': '.taobao.com', 'path': '/test'},
    ]


class CookieOriginsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='goofish-cookies-test-')
        self.addCleanup(temp.cleanup)
        self.cookie_path = Path(temp.name) / 'cookies.json'

    def test_browser_extraction_preserves_duplicate_names_domains_paths_and_expiry(self):
        jar = requests.cookies.RequestsCookieJar()
        for entry in entries():
            jar.set_cookie(requests.cookies.create_cookie(**entry, expires=int(time.time()) + 3600))
        jar.set('sgcookie', 'synthetic-unrelated', domain='.notgoofish.com')
        jar.set_cookie(requests.cookies.create_cookie('expired', 'synthetic', domain='.goofish.com', expires=1))
        actual = browser_cookie._jars_to_entries([jar, jar])
        self.assertEqual(len(actual), 5)
        self.assertEqual(len([c for c in actual if c['name'] == 'sgcookie']), 3)
        self.assertTrue(all(c.get('expires') for c in actual))

    def test_chromium_httponly_attribute_is_preserved(self):
        jar = requests.cookies.RequestsCookieJar()
        jar.set_cookie(requests.cookies.create_cookie('synthetic', 'synthetic', domain='.goofish.com', rest={'HTTPOnly': ''}))
        self.assertTrue(browser_cookie._jars_to_entries([jar])[0]['httpOnly'])

    def test_subprocess_extraction_keeps_the_same_cookie_identity(self):
        response = SimpleNamespace(returncode=0, stdout=json.dumps({'cookies': entries()}))
        with patch.object(browser_cookie.subprocess, 'run', return_value=response) as run:
            browser_cookie._extract_via_subprocess('chrome')
        script = run.call_args.args[0][2]
        setup = '''
import sys, types, requests
module = types.ModuleType('browser_cookie3')
def chrome(domain_name):
    jar = requests.cookies.RequestsCookieJar()
    jar.set('sgcookie', 'synthetic-' + domain_name, domain='.' + domain_name)
    jar.set('sgcookie', 'synthetic-path', domain='.' + domain_name, path='/test')
    jar.set('bad', 'synthetic', domain='.notgoofish.com')
    return jar
module.chrome = chrome
sys.modules['browser_cookie3'] = module
'''
        result = subprocess.run([sys.executable, '-c', setup + script, 'chrome', 'goofish.com,taobao.com', 'goofish.com,taobao.com'],
                                capture_output=True, text=True, check=True)
        cookies = json.loads(result.stdout)['cookies']
        self.assertEqual(len(cookies), 4)
        self.assertEqual({c['domain'] for c in cookies}, {'.goofish.com', '.taobao.com'})

    def test_auth_import_and_json_file_keep_cookie_origins(self):
        original = entries()
        with patch.object(login, 'resolve_cookie_path', return_value=self.cookie_path), \
             patch.object(login, '_pull_from_browser', return_value=(original, 'browser:chrome')):
            result = login.login(browser='chrome')
        self.assertEqual(result['cookies_count'], 5)
        self.assertEqual(json.loads(self.cookie_path.read_text()), original)
        self.assertEqual(self.cookie_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(login._parse_json(json.dumps(original)), original)

    def test_browser_injection_keeps_goofish_and_taobao_credentials_separate(self):
        original = entries()
        original[1].update(expires=FUTURE_EXPIRY, secure=True, httpOnly=True, sameSite='Strict')
        actual = _cookies_to_playwright(original)
        self.assertEqual(len(actual), 5)
        token = next(c for c in actual if c['name'] == '_m_h5_tk')
        self.assertEqual(token['domain'], '.goofish.com')
        self.assertEqual(token['expires'], FUTURE_EXPIRY)
        self.assertEqual(token['sameSite'], 'Strict')
        self.assertEqual({c['domain'] for c in actual if c['name'] == 'sgcookie'}, {'.goofish.com', '.taobao.com'})

    def test_legacy_goofish_cookie_header_is_not_assigned_to_taobao(self):
        cookies = _cookies_to_playwright({'_m_h5_tk': 'synthetic', 'cookie2': 'synthetic'})
        self.assertTrue(all(c['domain'] == '.goofish.com' for c in cookies))
        self.assertTrue(all('expires' not in c for c in cookies))

    def test_http_cookie_selection_cannot_be_overwritten_by_taobao_or_unrelated_hosts(self):
        original = entries() + [{'name': '_m_h5_tk', 'value': 'synthetic-wrong', 'domain': '.taobao.com'},
                                {'name': 'sgcookie', 'value': 'synthetic-wrong', 'domain': '.notgoofish.com'}]
        for rows in (original, list(reversed(original))):
            session.write_cookies_json(self.cookie_path, rows)
            values = session._load_cookies(self.cookie_path)
            self.assertEqual(values['_m_h5_tk'], 'synthetic-goofish_0')
            self.assertEqual(values['sgcookie'], 'synthetic-goofish')

    def test_refresh_updates_matching_cookie_without_erasing_other_origins(self):
        session.write_cookies_json(self.cookie_path, entries())
        fresh = [{'name': '_m_h5_tk', 'value': 'synthetic-new_0', 'domain': '.goofish.com', 'path': '/', 'expires': FUTURE_EXPIRY}]
        session.update_cookies_json(self.cookie_path, fresh)
        actual = json.loads(self.cookie_path.read_text())
        self.assertEqual(len(actual), 5)
        self.assertEqual([c for c in actual if c['name'] == '_m_h5_tk'], fresh)
        self.assertEqual(len([c for c in actual if c['domain'] == '.taobao.com']), 2)

    def test_automatic_bootstrap_persists_entries_instead_of_flattening_them(self):
        with patch.object(browser_cookie, 'extract_goofish_cookies', return_value=('chrome', entries())):
            values = session._load_or_bootstrap_cookies(self.cookie_path)
        self.assertEqual(values['sgcookie'], 'synthetic-goofish')
        self.assertEqual(json.loads(self.cookie_path.read_text()), entries())

    def test_only_expired_or_other_domain_tokens_do_not_satisfy_import_requirements(self):
        wrong = [dict(c, domain='.taobao.com') for c in entries()]
        self.assertFalse(browser_cookie._is_valid(wrong))
        expired = [dict(c, expires=1) for c in entries()]
        self.assertFalse(browser_cookie._is_valid(expired))
