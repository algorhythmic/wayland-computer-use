import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

from test_server import server
from cu import app_surfaces as app


class SurfaceTests(unittest.TestCase):
    def test_uri_validates_scheme_parameters_and_encoding(self):
        uri = 'obsidian://new?'+urlencode({'vault': 'My vault', 'name': 'HN', 'content': 'Excerpt\nReflection? &'})
        self.assertEqual(app.validate_uri(uri), uri)
        for uri in ('javascript:alert(1)', 'file:///etc/passwd', 'https://user:secret@example.com',
                    'obsidian://new?vault=v&name=n&overwrite=true', 'obsidian://new?vault=v&name=n&x-success=https://x',
                    'obsidian://new?vault=v&name=..%2Fn', 'obsidian://new?vault=v&vault=w&name=n'):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                app.validate_uri(uri)

    def test_dispatches_once_without_shell_or_completion_claim(self):
        d = server.Desktop()
        self.addCleanup(d.close)
        uri = 'obsidian://new?vault=Test&name=HN&content=Excerpt'
        with patch.object(server, 'desktop_locked', return_value=False), patch.object(server, 'run') as run:
            result = d.call('open_uri', {'uri': uri})
        self.assertEqual(run.call_args.args[0], ['xdg-open', uri])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads(result[0]['text'])['application_accepted'], 'unverified')
        with patch.object(server, 'run') as run, self.assertRaises(ValueError):
            d.call('open_uri', {'uri': 'javascript:alert(1)'})
        run.assert_not_called()

    def test_readonly_cdp_selects_exact_page_and_excludes_scripts(self):
        pages = [{'id': 'page1', 'type': 'page', 'url': 'https://example.com/',
                  'webSocketDebuggerUrl': 'ws://localhost:9222/devtools/page/page1'}]
        methods = []
        replies = iter([{'frameTree': {'frame': {'id': 'frame1', 'loaderId': 'load1', 'url': 'https://example.com/'}}}, {'root': {'nodeId': 1, 'documentURL': 'https://example.com/'}}, {'nodeId': 2},
                        {'outerHTML': '<div>Excerpt <a href="/item?id=1">comments</a><script>secret</script><input type="password" value="secret"></div>'},
                        {'frameTree': {'frame': {'id': 'frame1', 'loaderId': 'load1', 'url': 'https://example.com/'}}}])
        class Socket:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def send(self, text):
                self.request = json.loads(text)
                methods.append(self.request['method'])
            def recv(self, timeout): return json.dumps({'id': self.request['id'], 'result': next(replies)})
        with patch.dict(os.environ, {'WCU_CDP_PORT': '9222'}), patch.object(app, 'page_list', return_value=pages), \
                patch.object(app, 'connect_cdp', return_value=Socket()) as connect:
            body = app.cdp_read('page1', 'https://example.com/', '.comment')
        self.assertEqual(connect.call_args.args[0], 'ws://127.0.0.1:9222/devtools/page/page1')
        self.assertEqual(body['text'], 'Excerpt comments')
        self.assertEqual(body['links'], ['/item?id=1'])
        self.assertTrue(body['read_only'])
        self.assertEqual(methods, ['Page.getFrameTree', 'DOM.getDocument', 'DOM.querySelector', 'DOM.getOuterHTML', 'Page.getFrameTree'])

    def test_cdp_rejects_redirected_debugging_endpoint_and_wrong_page(self):
        page = {'id': 'one', 'url': 'https://example.com/', 'webSocketDebuggerUrl': 'ws://evil.invalid:9222/devtools/page/one'}
        with patch.dict(os.environ, {'WCU_CDP_PORT': '9222'}), patch.object(app, 'page_list', return_value=[page]), \
                patch.object(app, 'connect_cdp') as connect:
            for identifier, url in (('two', page['url']), ('one', page['url'])):
                with self.assertRaises(ValueError): app.cdp_read(identifier, url)
        connect.assert_not_called()

    def test_cdp_bounded_text_and_invalid_requests(self):
        parser = app.PageText(4)
        parser.feed('<div>0123456789</div>')
        self.assertLessEqual(len(''.join(parser.parts)), 4)
        self.assertTrue(parser.truncated)
        for args in ({'target_id': 'one'}, {'expected_url': 'https://example.com/'}, {'max_chars': 0}, {'selector': 'x'*513}):
            with self.assertRaises(ValueError): server.validate('cdp_read', args)

    def test_app_surfaces_respect_context_budget(self):
        d = server.Desktop()
        self.addCleanup(d.close)
        with patch.object(server.SnapshotStore, 'load', side_effect=ValueError('missing')):
            for limit in (256, 2048):
                result = d.call('context_for_task', {'intent': 'Obsidian note browser comment', 'max_bytes': limit})
                body = json.loads(result[0]['text'])
                self.assertEqual(len(result[0]['text'].encode()), body['output']['bytes'])
                self.assertLessEqual(body['output']['bytes'], limit)
                if limit == 2048:
                    self.assertEqual([a['tool'] for a in body['app_surfaces']], ['open_uri', 'cdp_read'])
