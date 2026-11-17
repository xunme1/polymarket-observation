"""Render readable market cards, with original screenshots retained in details."""
import html
from datetime import datetime
from decimal import Decimal
from market_data import leading_markets, money, outcome, percentage

esc = html.escape
ICONS = {'fed':'🏛','treasury':'↗','oil':'◈','gold':'Au','blockade':'◉','ceasefire':'◎'}


def date_label(value, *, short=False):
    try:
        from zoneinfo import ZoneInfo
        d = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Shanghai'))
        return d.strftime('%m-%d %H:%M' if short else '%Y-%m-%d %H:%M:%S')
    except (AttributeError, ValueError):
        return '未提供'


def stamp(value, label='数据更新'):
    if not value:
        return ''
    return f'<span class="timestamp">{label} <time datetime="{esc(value)}" title="北京时间">{date_label(value, short=True)}</time><span class="stale" hidden>已过期</span></span>'


def market_state(m):
    return '已结算' if m['resolved'] else '已关闭' if m['closed'] else '未结算'


def price_pair(m, *, compact=False):
    yes, no = outcome(m), outcome(m, 'no')
    if not any(x['label'].lower() == 'yes' for x in m['outcomes']):
        return '<div class="prices">' + ''.join(f'<span class="price neutral">{esc(x["label_zh"])} {esc(percentage(x["price"]))}</span>' for x in m['outcomes']) + '</div>'
    return (f'<div class="prices{ " compact" if compact else ""}">'
            f'<span class="price yes" title="原站 Yes 价格：{esc(str(yes)) if yes is not None else "未提供"}"><small>是</small> <strong>{esc(percentage(yes))}</strong></span>'
            f'<span class="price no" title="原站 No 价格：{esc(str(no)) if no is not None else "未提供"}"><small>否</small> <strong>{esc(percentage(no))}</strong></span></div>')


def market_row(m, *, detail=False):
    title = esc(m['label_zh'])
    if detail:
        subtitle = f'<small class="original-label">{esc(m["label"])}</small>' if m['label_zh'] != m['label'] else ''
        title = f'<div class="outcome-label">{title}{subtitle}<span class="market-state">{market_state(m)}</span></div>'
    else:
        title = f'<span class="outcome-label" title="{esc(m["label"])}">{title}</span>'
    return '<li class="outcome-row">' + title + price_pair(m, compact=not detail) + '</li>'


def screenshots(item):
    images = item.get('images', [])
    if not images:
        reason = item.get('screenshot_error') or item.get('error') or '本期没有原站截图。'
        return f'<div class="empty"><strong>原站截图暂不可用</strong><p>{esc(reason)}</p><span>没有用历史图片替代本期截图。</span></div>'
    shots = ''.join(f'<button class="shot" type="button" aria-label="放大 {esc(item["title"])} 第 {i+1} 段"><img loading="lazy" width="{p["width"]}" height="{p["height"]}" src="data:image/jpeg;base64,{p["jpeg"]}" alt="{esc(item.get("original_title", item["title"]))} · 原站截图第 {i+1} 段"></button>' for i, p in enumerate(images))
    return f'<p class="screenshot-caption">原站截图，点击放大。{stamp(item.get("captured_at"), "截图时间")}</p><div class="images">{shots}</div>'


def render_event(item):
    quote = item.get('market_data', {})
    available = quote.get('status') == 'ok' and bool(quote.get('markets'))
    markets = quote.get('markets', []) if available else []
    identifier, title, topic = esc(item['id']), esc(item['title']), esc(item['topic'])
    icon = quote.get('icon', '') if available else ''
    cover = f'<img class="event-icon" src="{esc(icon)}" alt="">' if icon.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,')) else f'<span class="event-icon icon-{identifier}" aria-hidden="true">{ICONS.get(item["id"], "◎")}</span>'
    settled = sum(m['resolved'] for m in markets)
    state = '已结束' if available and quote.get('closed') else '观察中' if available else '截图存档' if item.get('images') else '暂不可用'
    if available:
        if len(markets) == 1 and outcome(markets[0]) is not None:
            p = Decimal(outcome(markets[0])) * 100
            content = (f'<div class="binary-summary"><span>“是”的市场概率</span><svg viewBox="0 0 124 84" role="img" aria-label="是 {esc(percentage(outcome(markets[0])))}"><path d="M8 62 A54 54 0 0 1 116 62" pathLength="100" class="gauge-bg"/><path d="M8 62 A54 54 0 0 1 116 62" pathLength="100" class="gauge-value" stroke-dasharray="{p} 100"/><text x="62" y="63" text-anchor="middle">{esc(percentage(outcome(markets[0])))}</text></svg></div>' + price_pair(markets[0]))
        else:
            content = '<ul class="outcomes">' + ''.join(market_row(m) for m in leading_markets(markets)) + '</ul>'
        hint = f'{len(markets)} 个结果' + (f' · {settled} 个已结算' if settled else '')
        bottom = f'<strong>{esc(money(quote.get("volume")))}</strong><span>成交量</span>'
        updated = stamp(quote.get('fetched_at'))
    elif item.get('images'):
        first = item['images'][0]
        content = f'<div class="archive-thumb"><img src="data:image/jpeg;base64,{first["jpeg"]}" alt="原站截图预览" loading="lazy"></div>'
        hint, bottom = '查看完整原站截图', '<span>本期仅提供截图</span>'
        updated = stamp(item.get('captured_at'), '截图时间')
    else:
        content = '<div class="card-empty">本次数据暂不可用<br><small>查看详情了解采集状态</small></div>'
        hint, bottom, updated = '等待更新', '<span>未替换为历史数据</span>', ''
    card = (f'<article class="event-card" data-topic="{topic}" data-title="{title}">'
            f'<div class="card-head">{cover}<div><span class="topic">{topic}</span><h2><button class="card-open" data-open="{identifier}" aria-haspopup="dialog" aria-label="查看 {title} 完整详情">{title}</button></h2></div></div>'
            f'<div class="card-content">{content}</div><div class="card-status"><span>{hint}</span><span class="state">{state}</span></div>'
            f'<div class="card-footer"><div>{bottom}</div><span class="card-arrow" aria-hidden="true">↗</span></div><div class="card-time">{updated}</div></article>')
    if available:
        open_rows = [m for m in markets if not m['closed'] and not m['resolved']]
        closed_rows = [m for m in markets if m['closed'] or m['resolved']]
        body = (f'<div class="detail-facts"><div><span>累计成交量</span><strong>{esc(money(quote.get("volume")))}</strong></div><div><span>24 小时成交量</span><strong>{esc(money(quote.get("volume_24h")))}</strong></div><div><span>原站截止时间 · 北京时间</span><strong class="end-date">{date_label(quote.get("end_date"))}</strong></div></div>'
                f'<p class="detail-stamp">{stamp(quote.get("fetched_at"))}</p><p class="explain">概率为采集时原站 Yes / No 价格的百分比展示；各条件独立列示，未重新归一化。没有报价显示“—”。</p>')
        if open_rows:
            body += '<h3>未结算结果</h3><ul class="detail-outcomes">' + ''.join(market_row(m, detail=True) for m in open_rows) + '</ul>'
        if closed_rows:
            body += f'<details class="resolved" {"open" if not open_rows else ""}><summary>已关闭 / 已结算结果（{len(closed_rows)}）</summary><ul class="detail-outcomes">' + ''.join(market_row(m, detail=True) for m in closed_rows) + '</ul></details>'
        body += f'<p class="original-title">原站标题：{esc(quote["original_title"])}</p>'
    else:
        body = '<div class="empty"><strong>本期没有结构化概率数据</strong><p>可切换到“原站截图”查看当时的完整页面。历史概率未使用今天的数据回填。</p>'
        if quote.get('error'):
            body += f'<p>{esc(quote["error"])}</p>'
        body += '</div>'
    detail = (f'<template id="detail-{identifier}" data-title="{title}" data-topic="{topic}" data-initial="{"data" if available else "shots"}">'
              f'<section data-pane="data">{body}</section><section data-pane="shots" hidden>{screenshots(item)}</section>'
              f'<footer class="detail-source"><span>原站地址 · 可选中复制</span><code>{esc(item["url"])}</code></footer></template>')
    return card, detail
