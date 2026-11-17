"""Capture public event pages, then build a self-contained static snapshot site."""
import argparse
import base64
import hashlib
import html
import json
import math
import re
import shutil
import tempfile
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from market_data import collect_market
from presentation import render_event

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Shanghai")
MAX_REPORT = 8 * 1024 * 1024
MAX_SITE = 800 * 1024 * 1024
MORE = re.compile(r"^(显示更多|展示更多|Show more)\b|^显示更多", re.I)
RESOLVED = re.compile(r"^(View resolved|Show resolved|查看已结算|显示已结算)", re.I)


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    temp.replace(path)


def watchlist():
    items = json.loads((ROOT / "watchlist.json").read_text())
    ids = set()
    for item in items:
        u = urlparse(item["url"])
        if (u.scheme != "https" or u.netloc != "polymarket.com"
                or not re.fullmatch(r"/(?:zh/)?event/[a-z0-9-]+", u.path)):
            raise ValueError("Watchlist must contain public Polymarket event URLs")
        if not re.fullmatch(r"[a-z0-9-]+", item["id"]) or item["id"] in ids:
            raise ValueError("Invalid or repeated event ID")
        ids.add(item["id"])
    return items


# Read geometry only. No rewriting, hiding, or redrawing original page content.
GEOMETRY = r"""h => {
  let column = h;
  while (column) {
    const r = column.getBoundingClientRect();
    if (r.width >= 600 && r.width <= 1100 && /\d+(?:\.\d+)?%/.test(column.innerText)) break;
    column = column.parentElement;
  }
  if (!column) throw new Error('Event column not found');
  const candidates = Array.from(column.children).filter(e => {
    const r = e.getBoundingClientRect(), t = e.innerText;
    return r.width > 500 && r.height > 50 && /\d+(?:\.\d+)?%/.test(t)
      && !/(评论\s*\(|Comments\s*\(|常见问题|Frequently asked)/i.test(t);
  });
  if (!candidates.length) throw new Error('Outcome region not found');
  const r = column.getBoundingClientRect();
  const bottom = Math.max(...candidates.map(e => e.getBoundingClientRect().bottom));
  const text = candidates.map(e => e.innerText).join('\n');
  if (!/(交易量|成交量|\bVol\b|Volume)/i.test(text)) throw new Error('Volume missing');
  return {x:r.x + scrollX,y:r.y + scrollY,width:r.width,height:bottom-r.y,
    title:h.innerText,text,percentage_count:(text.match(/\d+(?:\.\d+)?%/g)||[]).length};
}"""


def wait_hydrated(page, locator):
    # A server-rendered button can be visible before React attaches its handler.
    handle = locator.element_handle()
    page.wait_for_function("e => Object.keys(e).some(k => k.startsWith('__reactProps') && typeof e[k]?.onClick === 'function')", arg=handle, timeout=30000)


def capture(browser, item):
    context = browser.new_context(viewport={"width": 1440, "height": 1100},
                                  locale="zh-CN", timezone_id="Asia/Shanghai",
                                  device_scale_factor=1, color_scheme="light")
    page = context.new_page()
    try:
        response = page.goto(item["url"], wait_until="domcontentloaded", timeout=45000)
        if response and response.status >= 400:
            raise ValueError(f"Source returned HTTP {response.status}")
        title = page.locator("h1:visible").first
        title.wait_for(timeout=30000)
        if re.search(r"just a moment|access denied|verify you are human|访问受限|验证您", page.title(), re.I):
            raise ValueError("Source access restriction / verification page")
        page.wait_for_function(r"() => /\d+(?:\.\d+)?%/.test(document.body.innerText)", timeout=30000)
        more_count = 0
        for _ in range(8):
            more = page.get_by_role("button", name=MORE).filter(visible=True)
            if not more.count():
                break
            button = more.first
            wait_hydrated(page, button)
            button.click()
            button.wait_for(state="hidden", timeout=10000)
            more_count += 1
        if page.get_by_role("button", name=MORE).filter(visible=True).count():
            raise ValueError("Outcome list did not fully expand")
        resolved = page.get_by_role("button", name=RESOLVED).filter(visible=True)
        resolved_opened = False
        if resolved.count():
            button = resolved.first
            wait_hydrated(page, button)
            before = title.evaluate(GEOMETRY)["text"]
            button.click()
            deadline = time.monotonic() + 10
            while title.evaluate(GEOMETRY)["text"] == before:
                if time.monotonic() > deadline:
                    raise ValueError("Resolved outcomes did not expand")
                page.wait_for_timeout(200)
            resolved_opened = True
        page.evaluate("window.scrollTo(0,0)")
        page.evaluate("document.fonts.ready")
        rect = title.evaluate(GEOMETRY)
        if not (600 <= rect["width"] <= 1100 and 200 <= rect["height"] <= 15000):
            raise ValueError("Unexpected screenshot region / page structure")
        # Keep the complete crop inside the viewport; Chromium encodes every JPEG.
        page.set_viewport_size({"width": 1440, "height": math.ceil(rect["y"] + rect["height"] + 40)})
        page.evaluate("window.scrollTo(0,0)")
        rect = title.evaluate(GEOMETRY)
        images = []
        for offset in range(0, math.ceil(rect["height"]), 1200):
            height = min(1200, rect["height"] - offset)
            if height < 1:
                continue
            raw = page.screenshot(type="jpeg", quality=75, animations="disabled", clip={
                "x": rect["x"], "y": rect["y"] + offset,
                "width": rect["width"], "height": height})
            if len(raw) < 1500 or not raw.startswith(b"\xff\xd8") or not raw.endswith(b"\xff\xd9"):
                raise ValueError("Empty or invalid JPEG")
            images.append({"jpeg": base64.b64encode(raw).decode(),
                           "sha256": hashlib.sha256(raw).hexdigest(),
                           "width": math.ceil(rect["width"]), "height": math.ceil(height)})
        return dict(item, status="ok", captured_at=now(), original_title=rect["title"],
                    images=images, expanded_more=more_count, expanded_resolved=resolved_opened,
                    percentage_count=rect["percentage_count"])
    finally:
        context.close()


def safe_json(data):
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def render(snapshot, dates, latest=False):
    esc = html.escape
    rendered = [render_event(item) for item in snapshot["events"]]
    count = sum(bool(e.get("images")) or has_quote(e) for e in snapshot["events"])
    full = all(complete(e) for e in snapshot["events"]) if snapshot.get("version", 1) >= 2 else count == len(snapshot["events"])
    options = "".join(f'<option value="{d}" {"selected" if d == snapshot["date"] else ""}>{d}</option>' for d in dates)
    topics = dict.fromkeys(e["topic"] for e in snapshot["events"])
    nav = "".join(f'<button class="filter" data-filter="{esc(topic)}" aria-pressed="false">{esc(topic)}</button>' for topic in topics)
    template = (ROOT / "template.html").read_text()
    values = {"DATE": snapshot["date"], "STATUS": "完整更新" if full else "部分更新",
              "COUNT": str(count), "TOTAL": str(len(snapshot["events"])), "CARDS": "".join(pair[0] for pair in rendered),
              "DETAILS": "".join(pair[1] for pair in rendered),
              "OPTIONS": options, "NAV": nav, "PREFIX": "reports/" if latest else "",
              "BUILT": esc(snapshot["generated_at"].replace("T", " "))}
    for key, value in values.items():
        template = template.replace("@@" + key + "@@", value)
    raw = template.encode()
    if len(raw) > MAX_REPORT:
        raise ValueError(f"Report exceeds 8 MiB: {len(raw)} bytes")
    return raw


def build_site(archive, output, pending=None):
    records = {p.stem: json.loads(p.read_text()) for p in archive.glob("????-??-??.json")}
    if pending:
        records[pending["date"]] = pending
    if not records:
        raise ValueError("No successful snapshots; no page will be published")
    dates = sorted(records, reverse=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        site = Path(temp)
        (site / "reports").mkdir()
        manifest = []
        for date in dates:
            body = render(records[date], dates)
            (site / "reports" / f"{date}.html").write_bytes(body)
            manifest.append({"date": date, "url": f"reports/{date}.html", "size": len(body), "sha256": hashlib.sha256(body).hexdigest()})
        (site / "index.html").write_bytes(render(records[dates[0]], dates, latest=True))
        atomic_json(site / "manifest.json", {"version": 1, "reports": manifest})
        (site / ".nojekyll").touch()
        if sum(p.stat().st_size for p in site.rglob("*") if p.is_file()) > MAX_SITE:
            raise ValueError("Site exceeds 800 MiB; publication stopped, history retained")
        if pending:
            atomic_json(archive / f'{pending["date"]}.json', pending)
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(site, output)


def has_quote(event):
    quote = event.get("market_data", {})
    return quote.get("status") == "ok" and bool(quote.get("markets"))


def complete(event):
    return bool(event.get("images")) and has_quote(event)


def same_day(value, date):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ).date().isoformat() == date
    except (AttributeError, ValueError):
        return False


def run(args):
    from playwright.sync_api import sync_playwright
    items = watchlist()
    if args.event:
        if args.archive == "snapshots":
            raise ValueError("Single-event preview requires a separate --archive directory")
        items = [e for e in items if e["id"] == args.event]
        if not items:
            raise ValueError("Unknown event")
    date = datetime.now(TZ).date().isoformat()
    archive, output = Path(args.archive), Path(args.output)
    archive.mkdir(parents=True, exist_ok=True)
    previous_path = archive / f"{date}.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    cached = {e["id"]: e for e in previous.get("events", [])} if not args.force else {}
    results = {}
    for item in items:
        old = cached.get(item["id"], {})
        result = dict(item)
        if old.get("url") == item["url"]:
            if old.get("images") and same_day(old.get("captured_at"), date):
                result.update({k: v for k, v in old.items() if k not in ("market_data", "status", "error", "screenshot_error")})
                result.update(item)
            if has_quote(old) and same_day(old["market_data"].get("fetched_at"), date):
                result["market_data"] = old["market_data"]
        results[item["id"]] = result
    if all(complete(e) for e in results.values()):
        build_site(archive, output)
        print("SKIP: today's complete snapshot already exists")
        return
    # API and screenshots fail/retry independently. Launch Chromium only when needed.
    with ExitStack() as stack:
        browser = None
        for attempt in range(args.attempts):
            for item in items:
                result = results[item["id"]]
                if not has_quote(result):
                    try:
                        result["market_data"] = collect_market(item)
                        print(f'API OK {item["id"]}', flush=True)
                    except Exception as error:
                        result["market_data"] = {"status": "error", "error": str(error)[:400], "attempted_at": now()}
                        print(f'API FAIL {item["id"]}: {str(error)[:400]}', flush=True)
                if not result.get("images"):
                    try:
                        if browser is None:
                            p = stack.enter_context(sync_playwright())
                            browser = p.chromium.launch(headless=True)
                            stack.callback(browser.close)
                        result.update(capture(browser, item))
                        result.pop("screenshot_error", None)
                        print(f'SCREENSHOT OK {item["id"]}: {len(result["images"])} segments', flush=True)
                    except Exception as error:
                        detail = str(error).split("Call log:")[0][:400]
                        result["screenshot_error"] = detail
                        print(f'SCREENSHOT FAIL {item["id"]}: {detail}', flush=True)
                result["status"] = "ok" if complete(result) else "partial" if result.get("images") or has_quote(result) else "error"
                result["attempted_at"] = now()
            if all(complete(e) for e in results.values()):
                break
            if attempt + 1 < args.attempts:
                time.sleep(args.retry_seconds)
    status = {"last_attempt": now(), "events": [{
        "id": e["id"], "status": e.get("status", "ok"),
        "captured_at": e.get("captured_at"), "screenshot_error": e.get("screenshot_error"),
        "data_fetched_at": e.get("market_data", {}).get("fetched_at"),
        "data_error": e.get("market_data", {}).get("error"),
    } for e in results.values()]}
    atomic_json(Path("artifacts/status.json"), status)
    successful = sum(bool(e.get("images")) or has_quote(e) for e in results.values())
    if not successful:
        raise ValueError("All events failed; previous snapshot and deployed site preserved")
    snapshot = {"version": 2, "date": date, "generated_at": now(), "events": [results[e["id"]] for e in items]}
    build_site(archive, output, snapshot)
    print(f"READY {date}: {successful}/{len(items)} events; {output / 'index.html'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist")
    parser.add_argument("--archive", default="snapshots")
    parser.add_argument("--attempts", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--retry-seconds", type=int, default=300)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--event", help="Single-event isolated preview (use a separate archive)")
    args = parser.parse_args()
    run(args)
