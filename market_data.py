"""Read-only public Gamma snapshots. Prices are never inferred or normalized."""
import base64
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from zoneinfo import ZoneInfo

API = 'https://gamma-api.polymarket.com'
IMAGE_HOSTS = {
    'polymarket-upload.s3.us-east-2.amazonaws.com',
    'polymarket-upload.s3.us-east-1.amazonaws.com',
    'polymarket-upload.s3.amazonaws.com', 'images.polymarket.com',
}
MONTHS = {name: n for n, name in enumerate(
    ['January','February','March','April','May','June','July','August','September','October','November','December'], 1)}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_bytes(url, maximum):
    with build_opener(NoRedirect()).open(Request(url, headers={
        'User-Agent': 'PolymarketObservation/2.0 (public research snapshots)',
        'Accept': 'application/json,image/png,image/jpeg,image/webp',
    }), timeout=20) as response:
        raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Public source response exceeds limit')
    return raw


def number(value, *, probability=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or (probability and n > 1):
            return None
        return format(n, 'f')
    except (InvalidOperation, ValueError):
        return None


def array(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    return value if isinstance(value, list) else []


def label_zh(label):
    label = str(label or '').strip()
    if label.lower() in ('no change', 'unchanged'):
        return '利率不变'
    if label.lower() in ('yes', 'no'):
        return {'yes': '是', 'no': '否'}[label.lower()]
    match = re.fullmatch(r'(\d+)(\+)? bps? (increase|decrease)', label, re.I)
    if match:
        return ('加息' if match[3].lower() == 'increase' else '降息') + match[1] + '个基点' + ('及以上' if match[2] else '')
    match = re.fullmatch(r'([A-Za-z]+) (\d{1,2})(?:,? (\d{4}))?', label)
    if match and match[1] in MONTHS and 1 <= int(match[2]) <= 31:
        return (f'{match[3]}年' if match[3] else '') + f'{MONTHS[match[1]]}月{int(match[2])}日前'
    match = re.fullmatch(r'([↑↓])\s*(\$[\d,.]+)', label)
    if match:
        return ('上涨触及 ' if match[1] == '↑' else '下跌触及 ') + match[2]
    return label


def normalize_event(data, item, fetched_at):
    expected = item['url'].rstrip('/').split('/')[-1]
    if not isinstance(data, dict) or data.get('slug') != expected or not data.get('title'):
        raise ValueError('Event identity does not match the configured source')
    if not isinstance(data.get('markets'), list) or not data['markets'] or len(data['markets']) > 1000:
        raise ValueError('Public event has no valid market list')
    markets = []
    for raw in data['markets']:
        if not isinstance(raw, dict) or not raw.get('id') or not raw.get('question'):
            raise ValueError('Unexpected market structure')
        labels, prices = array(raw.get('outcomes')), array(raw.get('outcomePrices'))
        if not labels or any(not isinstance(label, str) for label in labels):
            raise ValueError('Market outcome labels are missing')
        # Malformed/missing prices stay unavailable, including when array lengths differ.
        outcomes = [{'label': label, 'label_zh': label_zh(label),
                     'price': number(prices[i], probability=True) if len(prices) == len(labels) else None}
                    for i, label in enumerate(labels)]
        original_label = raw.get('groupItemTitle') or raw['question']
        markets.append({
            'id': str(raw['id']), 'question': raw['question'],
            'label': original_label, 'label_zh': label_zh(original_label),
            'outcomes': outcomes, 'closed': raw.get('closed') is True,
            'resolved': raw.get('umaResolutionStatus') == 'resolved',
            'active': raw.get('active') is True,
            'volume': number(raw.get('volumeNum', raw.get('volume'))),
            'end_date': raw.get('endDate') or None,
        })
    return {'status': 'ok', 'fetched_at': fetched_at,
            'source_url': API + '/events/slug/' + expected,
            'id': str(data.get('id', '')), 'original_title': data['title'],
            'volume': number(data.get('volume')), 'volume_24h': number(data.get('volume24hr')),
            'end_date': data.get('endDate') or None, 'closed': data.get('closed') is True,
            'markets': markets}


def collect_market(item):
    slug = item['url'].rstrip('/').split('/')[-1]
    if not re.fullmatch('[a-z0-9-]+', slug):
        raise ValueError('Invalid public event slug')
    endpoint = API + '/events/slug/' + slug + ('?locale=zh' if 'rank' in item else '')
    data = json.loads(fetch_bytes(endpoint, 4 * 1024 * 1024))
    if 'rank' in item and (not isinstance(data, dict) or item['id'] != 'event-' + str(data.get('id'))):
        raise ValueError('Event identity differs from daily ranking')
    result = normalize_event(data, item, datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'))
    result['source_url'] = endpoint
    icon = data.get('icon') or data.get('image')
    if isinstance(icon, str):
        try:
            parsed = urlparse(icon)
            if parsed.scheme == 'https' and parsed.hostname in IMAGE_HOSTS and not parsed.username and parsed.port in (None, 443):
                raw = fetch_bytes(icon, 256 * 1024)
                mime = ('image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else
                        'image/jpeg' if raw.startswith(b'\xff\xd8') and raw.endswith(b'\xff\xd9') else
                        'image/webp' if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' else None)
                if mime:
                    result['icon'] = 'data:' + mime + ';base64,' + base64.b64encode(raw).decode()
        except Exception:
            pass  # Covers are optional; market numbers remain independently verifiable.
    return result


def outcome_key(label):
    text = label.strip().lower()
    return {'是': 'yes', '否': 'no'}.get(text, text)


def outcome(market, label='yes'):
    return next((x.get('price') for x in market['outcomes'] if outcome_key(x['label']) == label), None)


def percentage(value):
    value = number(value, probability=True)
    if value is None:
        return '—'
    p = Decimal(value) * 100
    if 0 < p < Decimal('0.1'):
        return '<0.1%'
    if Decimal('99.9') < p < 100:
        return '>99.9%'
    return format(p.quantize(Decimal('0.1')), 'f').rstrip('0').rstrip('.') + '%' if p % 1 else f'{int(p)}%'


def money(value):
    value = number(value)
    if value is None:
        return '—'
    n = Decimal(value)
    for unit, divisor in [('B', Decimal(10)**9), ('M', Decimal(10)**6), ('K', Decimal(10)**3)]:
        if n >= divisor:
            return '$' + format((n / divisor).quantize(Decimal('0.1')), 'f').rstrip('0').rstrip('.') + unit
    return '$' + format(n.quantize(Decimal('1')), ',')


def leading_markets(markets):
    # Unsettled questions are useful for observation; resolved rows remain in the detail.
    active = [m for m in markets if not m['closed'] and not m['resolved']]
    return sorted(active or markets, key=lambda m: (outcome(m) is not None, Decimal(outcome(m) or '-1')), reverse=True)[:3]
