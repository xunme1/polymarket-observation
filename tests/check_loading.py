"""Simulate a slow HTML response: visible cards must not silently discard early clicks."""
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observe import render
from market_data import normalize_event


def main():
    item = {'id': 'fed', 'title': '加载状态验收', 'topic': '美联储',
            'url': 'https://polymarket.com/event/test'}
    item['market_data'] = normalize_event({
        'slug': 'test', 'title': 'Loading test', 'markets': [{
            'id': '1', 'question': 'Test?', 'outcomes': ['Yes', 'No'],
            'outcomePrices': ['0.6', '0.4'],
        }],
    }, item, '2026-11-23T08:00:00+08:00')
    body = render({'version': 2, 'date': '2026-11-23',
                   'generated_at': '2026-11-23T08:00:00+08:00', 'events': [item]}, ['2026-11-23'])
    split = body.index(b'<template id="detail-')
    release = Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body[:split])
            self.wfile.flush()
            release.wait(15)
            self.wfile.write(body[split:])

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='commit')
            expect(page.locator('.card-open')).to_be_visible()
            expect(page.locator('.card-open')).to_be_disabled()
            expect(page.locator('#loading-status')).to_be_visible()
            expect(page.locator('#dates')).to_be_disabled()
            expect(page.locator('.filter').first).to_be_disabled()
            release.set()
            expect(page.locator('html')).to_have_attribute('data-ready', 'true')
            expect(page.locator('#loading-status')).to_be_hidden()
            page.locator('.card-open').click()
            expect(page.locator('#detail')).to_be_visible()
            browser.close()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
    print('PASS: slow-response loading state and first enabled card click')


if __name__ == '__main__':
    main()
