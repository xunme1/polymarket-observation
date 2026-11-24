"""Select the day's open public events using Gamma's rolling 24-hour volume."""
import json
import re
from decimal import Decimal
from urllib.parse import urlencode

from market_data import API, fetch_bytes, number

COUNT = 9
METHOD = 'research-volume24hr-v1'
DESCRIPTION = '按近 24 小时成交量排列的 9 个研究相关热门事件'
PAGE_SIZE = 12
# Match Gamma tag slugs (stable across locales). Use the most specific match for display.
TOPICS = {
    '利率与债券': {'fed', 'fomc', 'fed-rates', 'global-rates', 'bonds', 'treasuries', 'interest-rates'},
    '原油与能源': {'oil', 'crude-oil', 'energy', 'natural-gas', 'opec'},
    '金属与商品': {'gold', 'silver', 'copper', 'commodities', 'agriculture'},
    '地缘政治': {'geopolitics', 'diplomacy-ceasefire', 'trade-war', 'tariffs'},
    '宏观经济': {'inflation', 'gdp', 'employment', 'economic-policy', 'recession'},
    '企业与市场': {'finance', 'stocks', 'equities', 'earnings', 'business', 'stock-market', 'forex'},
    '经济': {'economy', 'economics'},
}
EXCLUDED = {'sports', 'esports', 'pop-culture', 'celebrities'}


def candidate(row):
    if not isinstance(row, dict) or row.get('active') is not True or row.get('closed') or row.get('archived'):
        return None
    slug, title = row.get('slug'), row.get('title')
    gamma_id = str(row.get('id', ''))
    if not re.fullmatch('[0-9]+', gamma_id) or not isinstance(slug, str) or not re.fullmatch('[a-z0-9-]+', slug):
        return None
    volume = number(row.get('volume24hr'))
    if not isinstance(title, str) or not title.strip() or volume is None or Decimal(volume) <= 0:
        return None
    if not any(m.get('active') and not m.get('closed') and m.get('acceptingOrders') is not False
               and m.get('umaResolutionStatus') != 'resolved'
               for m in row.get('markets') or [] if isinstance(m, dict)):
        return None
    tags = {str(t.get('slug', '')).lower() for t in row.get('tags') or [] if isinstance(t, dict)}
    if tags & EXCLUDED:
        return None
    topic = next((name for name, group in TOPICS.items() if tags & group), None)
    if not topic:
        return None
    return {'id': 'event-' + gamma_id, 'url': 'https://polymarket.com/zh/event/' + slug,
            'title': title.strip(), 'topic': topic, 'ranking_volume_24h': volume}


def discover():
    by_id = {}
    urls = set()
    for offset in range(0, 600, PAGE_SIZE):
        query = urlencode({'active': 'true', 'closed': 'false', 'archived': 'false',
                           'order': 'volume24hr', 'ascending': 'false', 'locale': 'zh',
                           'limit': PAGE_SIZE, 'offset': offset})
        rows = json.loads(fetch_bytes(API + '/events?' + query, 16 * 1024 * 1024))
        if not isinstance(rows, list):
            raise ValueError('Invalid Gamma event leaderboard')
        for row in rows:
            item = candidate(row)
            if item and item['id'] not in by_id and item['url'] not in urls:
                by_id[item['id']] = item
                urls.add(item['url'])
        if len(by_id) >= COUNT:
            ranked = sorted(by_id.values(), key=lambda item: (-Decimal(item['ranking_volume_24h']), item['id']))
            return [dict(item, rank=i) for i, item in enumerate(ranked[:COUNT], 1)]
        if len(rows) < PAGE_SIZE:
            break
    raise ValueError(f'Only {len(by_id)} eligible events found; leaderboard not published')
