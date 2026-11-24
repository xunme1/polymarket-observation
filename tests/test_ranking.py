import argparse
import json
import unittest
from unittest.mock import patch

import observe
import ranking
from market_data import normalize_event, outcome


def row(n, **changes):
    return dict({'id': str(n), 'slug': f'public-event-{n}', 'title': f'测试事件 {n}',
                 'active': True, 'closed': False, 'volume24hr': str(100 - n),
                 'markets': [{'active': True, 'closed': False}],
                 'tags': [{'slug': 'finance'}]}, **changes)


class RankingTests(unittest.TestCase):
    def test_pages_overlap_skips_unavailable_and_orders_by_numeric_daily_volume(self):
        first = [row(1), row(2), row(3), row(4), row(5), row(6), row(7),
                 row(2), row(8, closed=True), row(9, volume24hr=None),
                 row(10, markets=[{'active': True, 'closed': True}]), row(11, archived=True)]
        second = [row(7), row(12), row(13), row(14)]
        with patch('ranking.fetch_bytes', side_effect=[json.dumps(first), json.dumps(second)]) as fetch:
            items = ranking.discover()
        self.assertEqual([item['id'] for item in items], [f'event-{n}' for n in (1,2,3,4,5,6,7,12,13)])
        self.assertEqual([item['rank'] for item in items], list(range(1, 10)))
        self.assertIn('offset=12', fetch.call_args.args[0])
        self.assertIn('order=volume24hr', fetch.call_args.args[0])

    def test_does_not_publish_short_or_invalid_leaderboard(self):
        with patch('ranking.fetch_bytes', return_value=b'[]'), self.assertRaisesRegex(ValueError, 'eligible'):
            ranking.discover()
        self.assertIsNone(ranking.candidate(row(1, slug='../wrong')))
        self.assertIsNone(ranking.candidate(row(1, volume24hr='NaN')))

    def test_lineup_frozen_for_retry_and_new_for_next_day_or_force(self):
        args = argparse.Namespace(event=None, force=False, attempts=1)
        items = [dict(ranking.candidate(row(n)),rank=n) for n in range(1,10)]
        with patch('ranking.discover', return_value=items) as discover:
            first, info = observe.choose_lineup(args, {})
            self.assertEqual(observe.choose_lineup(args, {'selection':info}), (first,info))
            self.assertEqual(discover.call_count, 1)
            args.force = True
            observe.choose_lineup(args, {'selection':info})
            args.force = False
            observe.choose_lineup(args, {'version':2, 'events':[]})
            self.assertEqual(discover.call_count, 3)

    def test_only_open_research_topics_qualify(self):
        for tag in ('sports', 'esports', 'pop-culture'):
            self.assertIsNone(ranking.candidate(row(1,tags=[{'slug':'finance'},{'slug':tag}])))
        self.assertIsNone(ranking.candidate(row(2,tags=[{'slug':'random'}])))
        self.assertIsNone(ranking.candidate(row(3,markets=[{'active':True,'acceptingOrders':False}])))
        self.assertEqual(ranking.candidate(row(4,tags=[{'slug':'oil'}]))['topic'],'原油与能源')

    def test_localized_prices_are_matched_by_label(self):
        raw = row(1, markets=[{'id':'20','question':'问题','outcomes':['否','是'],'outcomePrices':['0.7','0.3']}])
        market = normalize_event(raw, ranking.candidate(row(1)), 'time')['markets'][0]
        self.assertEqual(outcome(market), '0.3')
        self.assertEqual(outcome(market,'no'), '0.7')
