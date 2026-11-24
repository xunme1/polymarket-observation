"""Exercise the generated site in the same two opaque sandbox frames as production."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
SITE = 'https://xunme1.github.io/polymarket-observation/'
HOST = 'https://research.example.test'
ARTIFACTS = ROOT / 'artifacts/ui'


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    reports = json.loads((ROOT / 'dist/manifest.json').read_text())['reports']
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1080})
        errors, violations, external = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda msg: violations.append(msg.text) if 'Content Security Policy' in msg.text else None)
        page.route(HOST + '/', lambda route: route.fulfill(content_type='text/html',
            headers={'Content-Security-Policy': "default-src 'none'; frame-src 'self'; style-src 'unsafe-inline'"},
            body='<style>body{margin:0}iframe{width:100%;height:100vh;border:0;display:block}</style><iframe src="/bridge.html" sandbox="allow-scripts"></iframe>'))
        page.route(HOST + '/bridge.html', lambda route: route.fulfill(content_type='text/html',
            headers={'Content-Security-Policy': "default-src 'none'; frame-src " + SITE + "; style-src 'unsafe-inline'"},
            body='<style>body{margin:0}iframe{width:100%;height:100vh;border:0;display:block}</style><iframe src="' + SITE + '" sandbox="allow-scripts"></iframe>'))

        def snapshot(route):
            relative = route.request.url.removeprefix(SITE) or 'index.html'
            target = (ROOT / 'dist' / relative).resolve()
            assert target.is_relative_to((ROOT / 'dist').resolve()) and target.is_file()
            route.fulfill(content_type='text/html; charset=utf-8', body=target.read_bytes())

        page.route(SITE + '**', snapshot)
        page.on('request', lambda req: external.append(req.url) if not req.url.startswith((HOST, SITE, 'data:')) else None)
        page.goto(HOST + '/')
        site = page.frame_locator('iframe').frame_locator('iframe')
        expect(site.locator('.event-card')).to_have_count(9)
        widths = site.locator('.event-grid').evaluate('e => getComputedStyle(e).gridTemplateColumns.split(" ").length')
        assert widths == 3, widths
        assert site.locator('.event-card .price').count() > 0
        site.locator('.event-icon').first.wait_for()
        page.screenshot(path=str(ARTIFACTS / 'desktop.png'), full_page=True)
        # Topic selection and whole-card click; quotes render inside the opaque frame.
        # Pick an event whose screenshot and quotes were both captured in this edition.
        key = site.locator('template').evaluate_all(
            "els => els.find(e => e.content.querySelector('.shot') && e.content.querySelector('.detail-outcomes'))?.id")
        assert key, 'No event has both screenshots and quotes'
        button = site.locator('button[data-open="' + key.removeprefix('detail-') + '"]')
        topic = button.locator('xpath=ancestor::article').get_attribute('data-topic')
        site.get_by_role('button', name=topic, exact=True).click()
        expect(site.locator('.event-card:visible').first).to_be_visible()
        assert all(t == topic for t in site.locator('.event-card:visible').evaluate_all('es=>es.map(e=>e.dataset.topic)'))
        button.click()
        expect(site.locator('#detail')).to_be_visible()
        expect(site.locator('#detail-content [data-pane="data"]')).to_be_visible()
        expect(site.locator('#detail .detail-outcomes .outcome-row').first).to_be_visible()
        page.screenshot(path=str(ARTIFACTS / 'detail.png'), full_page=True)
        site.get_by_role('tab', name='原站截图', exact=True).click()
        site.locator('#detail .shot').first.click()
        expect(site.locator('#viewer')).to_be_visible()
        expect(site.locator('#large-image')).to_have_js_property('complete', True)
        width = site.locator('#large-image').evaluate('e => e.getBoundingClientRect().width')
        site.get_by_role('button', name='放大', exact=True).click()
        assert site.locator('#large-image').evaluate('e => e.getBoundingClientRect().width') > width
        page.keyboard.press('Escape')
        expect(site.locator('#viewer')).not_to_be_visible()
        expect(site.locator('#detail')).to_be_visible()
        page.keyboard.press('Escape')
        expect(site.locator('#detail')).not_to_be_visible()
        expect(button).to_be_focused()
        site.get_by_role('button', name='全部事件', exact=True).click()
        page.set_viewport_size({'width': 390, 'height': 844})
        assert site.locator('.event-grid').evaluate('e => getComputedStyle(e).gridTemplateColumns.split(" ").length') == 1
        assert site.locator('html').evaluate('e => e.scrollWidth') <= 390
        page.screenshot(path=str(ARTIFACTS / 'mobile.png'), full_page=True)
        site.locator('.event-card').first.click()
        assert site.locator('#detail').evaluate('e => e.scrollWidth') <= 390
        assert site.locator('#detail-content').evaluate('e => e.scrollTop') == 0
        expect(site.get_by_role('button', name='关闭事件详情')).to_be_visible()
        page.screenshot(path=str(ARTIFACTS / 'mobile-detail.png'), full_page=True)
        site.get_by_role('button', name='关闭事件详情').click()
        if len(reports) > 1:
            site.get_by_label('选择观察日期').select_option(reports[-1]['date'])
            expect(site.get_by_label('选择观察日期')).to_have_value(reports[-1]['date'])
            expect(site.locator('.event-card')).to_have_count(reports[-1]['events'])
            site.locator('.event-card').first.click()
            expect(site.locator('#detail [data-pane="shots"]')).to_be_visible()
            site.get_by_role('button', name='关闭事件详情').click()
            site.get_by_label('选择观察日期').select_option(reports[0]['date'])
            expect(site.locator('.event-card .price').first).to_be_visible()
        assert not errors, errors
        assert not violations, violations
        assert not external, external
        browser.close()
    print('PASS: desktop, mobile, filters, detail, screenshots, keyboard, history, nested CSP, no external requests')


if __name__ == '__main__':
    main()
