import copy
import unittest
from unittest.mock import patch

from market_data import collect_market, label_zh, leading_markets, money, normalize_event, outcome, percentage
from presentation import render_event


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        self.item = {'id': 'fed', 'url': 'https://polymarket.com/event/example', 'title': '美联储', 'topic': '美联储'}
        self.raw = {'id': '1', 'slug': 'example', 'title': 'Public event', 'volume': '12345', 'markets': [
            {'id': '10', 'question': 'Will the rate increase?', 'groupItemTitle': '25 bps increase',
             'outcomes': '["No", "Yes"]', 'outcomePrices': '["0.3", "0.7"]', 'closed': False},
        ]}

    def normalized(self):
        return normalize_event(self.raw, self.item, '2026-11-17T08:00:00+08:00')

    def test_probabilities_follow_labels_not_assumed_positions(self):
        market = self.normalized()['markets'][0]
        self.assertEqual(outcome(market), '0.7')
        self.assertEqual(outcome(market, 'no'), '0.3')
        self.assertEqual(market['label_zh'], '加息25个基点')

    def test_missing_or_misaligned_prices_are_not_zero_or_inferred(self):
        for value in (None, 'null', '[]', '["0.4"]', 'broken JSON'):
            with self.subTest(value=value):
                self.raw['markets'][0]['outcomePrices'] = value
                self.assertEqual([x['price'] for x in self.normalized()['markets'][0]['outcomes']], [None, None])
        self.raw['markets'][0]['outcomePrices'] = '["0.3", null]'
        self.assertIsNone(outcome(self.normalized()['markets'][0]))

    def test_invalid_prices_remain_unavailable(self):
        for value in ('NaN', 'Infinity', '-0.1', '1.1', True, None):
            self.raw['markets'][0]['outcomePrices'] = [value, value]
            self.assertIsNone(outcome(self.normalized()['markets'][0]))

    def test_closed_is_not_automatically_resolved(self):
        self.raw['markets'][0]['closed'] = True
        m = self.normalized()['markets'][0]
        self.assertTrue(m['closed'])
        self.assertFalse(m['resolved'])

    def test_invalid_identity_or_structure_rejected(self):
        for change in ({'slug': 'other'}, {'markets': []}, {'markets': [{'id': 'x'}]}):
            with self.assertRaises(ValueError):
                normalize_event(dict(self.raw, **change), self.item, 'date')

    def test_precise_display_and_literal_translation(self):
        for raw, expected in ((None, '—'), ('0', '0%'), ('1', '100%'), ('0.0001', '<0.1%'), ('0.9999', '>99.9%'), ('0.535', '53.5%')):
            self.assertEqual(percentage(raw), expected)
        self.assertEqual(money('35806337.541'), '$35.8M')
        self.assertEqual(money('0'), '$0')
        self.assertEqual(label_zh('50+ bps decrease'), '降息50个基点及以上')
        self.assertEqual(label_zh('November 30'), '11月30日前')
        self.assertEqual(label_zh('↑ $5,000'), '上涨触及 $5,000')
        self.assertEqual(label_zh('Unknown label'), 'Unknown label')

    def test_leading_rows_keep_resolved_results_in_detail_only(self):
        quote = self.normalized()
        resolved = copy.deepcopy(quote['markets'][0])
        resolved.update(id='11', closed=True, resolved=True)
        resolved['outcomes'][1]['price'] = '1'
        quote['markets'].append(resolved)
        self.assertEqual([m['id'] for m in leading_markets(quote['markets'])], ['10'])
        card, detail = render_event(dict(self.item, market_data=quote))
        self.assertNotIn('100%', card)
        self.assertIn('100%', detail)
        self.assertIn('已结算', detail)
        self.assertIn('原站截图暂不可用', detail)

    def test_quote_is_escaped_and_missing_prices_are_visible(self):
        self.raw['title'] = '<script>bad</script>'
        self.raw['markets'][0]['outcomePrices'] = None
        card, detail = render_event(dict(self.item, market_data=self.normalized()))
        self.assertNotIn('<script>bad</script>', detail)
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', detail)
        self.assertIn('—', card)

    def test_optional_bad_icon_cannot_discard_valid_prices(self):
        import json
        self.raw['icon'] = 'https://polymarket-upload.s3.us-east-2.amazonaws.com:bad/file.png'
        with patch('market_data.fetch_bytes', return_value=json.dumps(self.raw).encode()):
            result = collect_market(self.item)
        self.assertEqual(result['status'], 'ok')
        self.assertNotIn('icon', result)


if __name__ == '__main__':
    unittest.main()
