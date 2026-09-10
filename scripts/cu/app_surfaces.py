"""Explicit application protocols. No shell evaluation, browser launch, or config edits."""
import http.client
from html.parser import HTMLParser
import importlib.util
import json
import os
import re
import shutil
import time
from urllib.parse import parse_qsl, urlsplit


def validate_uri(uri):
    if not isinstance(uri, str) or not 1 <= len(uri) <= 32768 or any(ord(c) < 32 for c in uri):
        raise ValueError('Invalid URI')
    parsed = urlsplit(uri)
    if parsed.scheme in ('http', 'https'):
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('URI requires a host and no embedded credentials')
    elif parsed.scheme == 'obsidian':
        allowed = {'open': {'vault', 'file'}, 'new': {'vault', 'name', 'content'}}
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        params = dict(pairs)
        if parsed.netloc not in allowed or parsed.path not in ('', '/') or parsed.fragment or \
                len(params) != len(pairs) or set(params)-allowed[parsed.netloc] or not params.get('vault'):
            raise ValueError('Supported Obsidian URIs: open?vault=&file= or new?vault=&name=&content=')
        if parsed.netloc == 'new' and (not params.get('name') or '/' in params['name'] or '\\' in params['name'] or params['name'] in ('.', '..')):
            raise ValueError('Obsidian new requires a note name, without a path')
        if any('\x00' in v for v in params.values()):
            raise ValueError('URI parameters must not contain NUL')
    else:
        raise ValueError('Only http, https, and bounded Obsidian open/new URIs are supported')
    return uri


def cdp_port():
    value = os.environ.get('WCU_CDP_PORT', '')
    if not value.isdecimal() or not 1024 <= int(value) <= 65535:
        raise ValueError('CDP unavailable: configure WCU_CDP_PORT for an existing loopback debugging browser')
    return int(value)


def surface_context(intent):
    """Candidates with explicit prerequisites, never inferred authorization."""
    actions = []
    if any(word in intent.casefold() for word in ('obsidian', 'note', 'uri')):
        actions.append({'tool': 'open_uri', 'app': 'obsidian', 'operation': 'new or open',
                        'prerequisites': ['known vault', 'registered obsidian URI handler', 'authorized note content'],
                        'launcher_available': bool(shutil.which('xdg-open'))})
    if any(word in intent.casefold() for word in ('browser', 'chrom', 'web', 'comment', 'page')):
        actions.append({'tool': 'cdp_read', 'app': 'chromium',
                        'prerequisites': ['existing loopback CDP browser', 'selected page ID and expected URL'],
                        'configured': bool(os.environ.get('WCU_CDP_PORT')),
                        'dependency_available': importlib.util.find_spec('websockets') is not None})
    return actions


class PageText(HTMLParser):
    """Bounded text/link extraction, not a claim about rendered visibility."""
    def __init__(self, limit):
        super().__init__(convert_charrefs=True)
        self.limit, self.parts, self.links = limit, [], []
        self.skipped = []
        self.size = 0
        self.truncated = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'template'):
            self.skipped.append(tag)
        if not self.skipped and tag == 'a' and len(self.links) < 40:
            href = dict(attrs).get('href', '')
            if href and len(href) <= 2048:
                self.links.append(href)
        if tag in ('p', 'div', 'br', 'li', 'tr'):
            self.handle_data('\n')

    def handle_endtag(self, tag):
        if self.skipped and tag == self.skipped[-1]:
            self.skipped.pop()

    def handle_data(self, data):
        if self.skipped:
            return
        remaining = self.limit-self.size
        self.truncated |= len(data) > remaining
        self.parts.append(data[:remaining])
        self.size += min(remaining, len(data))


def page_list(port):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        connection.request('GET', '/json/list')
        reply = connection.getresponse()
        data = reply.read(1_048_577)
        if reply.status != 200 or len(data) > 1_048_576:
            raise ValueError('CDP page listing unavailable or oversized')
        pages = json.loads(data)
        if not isinstance(pages, list) or len(pages) > 200:
            raise ValueError('Invalid CDP page listing')
        return [p for p in pages if p.get('type') == 'page']
    finally:
        connection.close()


def connect_cdp(url):
    try:
        from websockets.sync.client import connect
    except ImportError as exc:
        raise RuntimeError('CDP requires optional Python dependency websockets>=15') from exc
    return connect(url, proxy=None, open_timeout=3, close_timeout=.2, max_size=1_048_576)


def cdp_read(target_id=None, expected_url=None, selector='body', max_chars=4096):
    port = cdp_port()
    pages = page_list(port)
    if target_id is None:
        return {'pages': [{k: p.get(k) for k in ('id', 'title', 'url')} for p in pages], 'read_only': True}
    found = [p for p in pages if p.get('id') == target_id and p.get('url') == expected_url]
    if len(found) != 1:
        raise ValueError('Page ID and expected URL must match exactly; list pages again')
    url = found[0].get('webSocketDebuggerUrl', '')
    parsed = urlsplit(url)
    if parsed.scheme != 'ws' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port != port or \
            parsed.username or parsed.password or parsed.query or parsed.fragment or \
            not re.fullmatch(r'/devtools/page/[A-Za-z0-9_-]+', parsed.path):
        raise ValueError('CDP must use the configured loopback page endpoint')
    # Force numeric loopback; never resolve a server-provided hostname or proxy.
    url = f'ws://127.0.0.1:{port}{parsed.path}'
    end = time.monotonic()+10
    with connect_cdp(url) as socket:
        sequence = 0
        def request(method, params=None):
            nonlocal sequence
            sequence += 1
            socket.send(json.dumps({'id': sequence, 'method': method, 'params': params or {}}))
            while True:
                remaining = end-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('CDP read deadline')
                response = json.loads(socket.recv(timeout=remaining))
                if response.get('id') != sequence:
                    continue
                if 'error' in response:
                    raise ValueError('CDP read failed; inspect fresh page evidence')
                return response['result']
        frame = request('Page.getFrameTree')['frameTree']['frame']
        if frame.get('url') != expected_url:
            raise ValueError('Page navigated before read')
        root = request('DOM.getDocument', {'depth': 0})['root']
        if root.get('documentURL') != expected_url:
            raise ValueError('Page navigated before read')
        node = request('DOM.querySelector', {'nodeId': root['nodeId'], 'selector': selector})['nodeId']
        if not node:
            return {'status': 'not_found', 'read_only': True}
        html = request('DOM.getOuterHTML', {'nodeId': node})['outerHTML']
        current = request('Page.getFrameTree')['frameTree']['frame']
        if any(current.get(k) != frame.get(k) for k in ('id', 'loaderId', 'url')):
            raise ValueError('Page navigated during read')
    text = PageText(max_chars)
    text.feed(html)
    return {'status': 'read', 'target_id': target_id, 'url': expected_url,
            'text': ''.join(text.parts).strip(), 'links': text.links, 'complete': not text.truncated,
            'read_only': True, 'visibility': 'DOM text; may include hidden content', 'trusted_instructions': False}
