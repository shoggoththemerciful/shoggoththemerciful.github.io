#!/usr/bin/env python3
import argparse
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
STORE_FILE = DATA_DIR / "watchlist.json"
USER_AGENT = "market-tracker-local/0.1"
POLL_SECONDS = 8

store_lock = Lock()
live_lock = Lock()
live_cache = {"updated_at": 0, "markets": []}


def now_ts():
    return int(time.time())


def http_json(url, method="GET", body=None):
    data = None
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"{error.code} {error.reason}: {detail}") from error


def load_store():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STORE_FILE.exists():
        return {"watchlist": []}
    try:
        with STORE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return {"watchlist": data.get("watchlist", [])}
    except (OSError, json.JSONDecodeError):
        return {"watchlist": []}


def save_store(store):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = STORE_FILE.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(store, file, indent=2, sort_keys=True)
    temp_file.replace(STORE_FILE)


def dollars_to_cents(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number * 100 if number <= 1 else number, 4)


def parse_json_array(value):
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def parse_link_or_text(value):
    text = (value or "").strip()
    result = {"kind": "keyword", "source": "", "query": text, "side": "", "url": ""}
    if not text:
        return result
    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return result
    if not parsed.scheme or not parsed.netloc:
        if is_kalshi_ticker(text):
            result["kind"] = "ticker"
        return result

    host = parsed.netloc.lower()
    params = urllib.parse.parse_qs(parsed.query)
    if "kalshi.com" in host:
        ticker = first_param(params, "op_market_ticker", "market_ticker", "ticker") or extract_kalshi_ticker(parsed.path)
        return {
            "kind": "url",
            "source": "kalshi",
            "query": ticker.upper() if ticker else "",
            "side": parse_side(first_param(params, "op_order_side", "side")),
            "url": text,
        }
    if "polymarket.com" in host:
        query = first_param(params, "q", "query", "search") or slug_to_query(parsed.path)
        return {
            "kind": "url",
            "source": "polymarket",
            "query": query,
            "side": parse_side(first_param(params, "side", "outcome")),
            "url": text,
        }
    return result


def first_param(params, *names):
    for name in names:
        values = params.get(name)
        if values and values[0]:
            return values[0]
    return ""


def parse_side(value):
    side = str(value or "").lower()
    if side in ("yes", "y"):
        return "yes"
    if side in ("no", "n"):
        return "no"
    return ""


def extract_kalshi_ticker(path):
    for part in path.split("/"):
        if is_kalshi_ticker(part):
            return part
    return ""


def is_kalshi_ticker(value):
    text = str(value or "").strip()
    return bool(text and text.upper().startswith("KX") and all(char.isalnum() or char == "-" for char in text))


def slug_to_query(path):
    parts = [part for part in path.split("/") if part]
    if not parts:
        return ""
    return parts[-1].replace("-", " ")


def market_key(item):
    return f"{item.get('venue')}:{item.get('id')}"


def normalize_kalshi_market(market, source_url=""):
    ticker = market.get("ticker", "")
    event_ticker = market.get("event_ticker", "")
    series_ticker = market.get("series_ticker") or (event_ticker.split("-")[0] if event_ticker else ticker.split("-")[0])
    yes_ask = dollars_to_cents(market.get("yes_ask_dollars") or market.get("yes_ask"))
    yes_bid = dollars_to_cents(market.get("yes_bid_dollars") or market.get("yes_bid"))
    last = dollars_to_cents(market.get("last_price_dollars") or market.get("last_price"))
    yes_price = first_number(yes_ask, last, yes_bid)
    no_ask = dollars_to_cents(market.get("no_ask_dollars") or market.get("no_ask"))
    no_bid = dollars_to_cents(market.get("no_bid_dollars") or market.get("no_bid"))
    no_price = first_number(no_ask, 100 - yes_price if yes_price is not None else None, no_bid)
    return {
        "venue": "kalshi",
        "id": ticker,
        "ticker": ticker,
        "series_ticker": series_ticker,
        "title": market.get("title") or ticker,
        "event": event_ticker,
        "url": source_url or f"https://kalshi.com/markets/{ticker}",
        "yes_price": yes_price,
        "no_price": no_price,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "last_price": last,
        "volume": numeric(market.get("volume_24h_fp") or market.get("volume_fp")),
        "liquidity": numeric(market.get("liquidity_dollars")),
        "open_interest": numeric(market.get("open_interest_fp")),
        "status": market.get("status", ""),
        "updated_at": now_ts(),
    }


def normalize_poly_market(market, event=None):
    outcomes = parse_json_array(market.get("outcomes"))
    prices = parse_json_array(market.get("outcomePrices"))
    tokens = parse_json_array(market.get("clobTokenIds"))
    yes_index = 0
    no_index = 1
    for index, name in enumerate(outcomes):
        lowered = str(name).lower()
        if lowered == "yes":
            yes_index = index
        if lowered == "no":
            no_index = index
    yes_price = dollars_to_cents(prices[yes_index]) if yes_index < len(prices) else None
    no_price = dollars_to_cents(prices[no_index]) if no_index < len(prices) else None
    yes_bid = dollars_to_cents(market.get("bestBid"))
    yes_ask = dollars_to_cents(market.get("bestAsk"))
    if no_price is None and yes_price is not None:
        no_price = round(100 - yes_price, 4)
    return {
        "venue": "polymarket",
        "id": str(market.get("id") or market.get("conditionId") or market.get("slug")),
        "slug": market.get("slug", ""),
        "condition_id": market.get("conditionId", ""),
        "yes_token_id": str(tokens[yes_index]) if yes_index < len(tokens) else "",
        "no_token_id": str(tokens[no_index]) if no_index < len(tokens) else "",
        "title": market.get("question") or market.get("title") or "Polymarket market",
        "event": (event or {}).get("title") or "",
        "url": f"https://polymarket.com/event/{(event or {}).get('slug')}" if (event or {}).get("slug") else f"https://polymarket.com/market/{market.get('slug', '')}",
        "yes_price": yes_price,
        "no_price": no_price,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": round(100 - yes_ask, 4) if yes_ask is not None else None,
        "no_ask": round(100 - yes_bid, 4) if yes_bid is not None else None,
        "last_price": dollars_to_cents(market.get("lastTradePrice")),
        "volume": numeric(market.get("volume24hr") or market.get("volumeNum") or market.get("volume")),
        "liquidity": numeric(market.get("liquidityNum") or market.get("liquidity")),
        "open_interest": numeric(market.get("openInterest")),
        "status": "active" if market.get("active") and not market.get("closed") else "closed",
        "updated_at": now_ts(),
    }


def first_number(*values):
    for value in values:
        if isinstance(value, (int, float)) and math.isfinite(value):
            return value
    return None


def numeric(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def get_kalshi_market(ticker, source_url=""):
    data = http_json(f"https://external-api.kalshi.com/trade-api/v2/markets/{urllib.parse.quote(ticker)}")
    return normalize_kalshi_market(data.get("market", data), source_url=source_url)


def search_kalshi(query):
    intent = parse_link_or_text(query)
    q = intent["query"].strip()
    if q and (intent["kind"] in ("url", "ticker") or is_kalshi_ticker(q)):
        return [get_kalshi_market(q, source_url=intent.get("url", ""))]

    hits = []
    cursor = ""
    needle = q.lower()
    for _ in range(8):
        params = {"limit": "1000", "status": "open"}
        if cursor:
            params["cursor"] = cursor
        url = "https://external-api.kalshi.com/trade-api/v2/markets?" + urllib.parse.urlencode(params)
        data = http_json(url)
        for market in data.get("markets", []):
            haystack = " ".join(str(market.get(key, "")) for key in ("title", "subtitle", "ticker", "event_ticker", "yes_sub_title", "no_sub_title")).lower()
            if not needle or needle in haystack:
                hits.append(normalize_kalshi_market(market))
                if len(hits) >= 50:
                    return hits
        cursor = data.get("cursor") or ""
        if not cursor:
            break
    return hits


def search_polymarket(query):
    intent = parse_link_or_text(query)
    q = intent["query"].strip()
    urls = []
    if q:
        urls.append("https://gamma-api.polymarket.com/public-search?" + urllib.parse.urlencode({"q": q, "limit": 40}))
        urls.append("https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode({"limit": 100, "active": "true", "closed": "false", "archived": "false", "search": q}))
    else:
        urls.append("https://gamma-api.polymarket.com/markets?limit=100&active=true&closed=false&archived=false")

    last_error = None
    for url in urls:
        try:
            data = http_json(url)
            markets = normalize_polymarket_response(data, q)
            if markets:
                return markets[:50]
        except RuntimeError as error:
            last_error = error
    if last_error:
        raise last_error
    return []


def normalize_polymarket_response(data, query=""):
    results = []
    seen = set()
    needle = query.lower()

    def add_market(market, event=None):
        if not isinstance(market, dict):
            return
        if not market.get("outcomes") or not market.get("outcomePrices"):
            return
        if market.get("closed") or market.get("active") is False:
            return
        title = market.get("question") or market.get("title") or ""
        slug = market.get("slug", "")
        event_title = (event or {}).get("title", "")
        if needle and needle not in " ".join([title, slug, event_title]).lower():
            return
        normalized = normalize_poly_market(market, event)
        key = market_key(normalized)
        if key not in seen:
            seen.add(key)
            results.append(normalized)

    if isinstance(data, list):
        for market in data:
            add_market(market)
    elif isinstance(data, dict):
        for event in data.get("events", []):
            for market in event.get("markets", []):
                add_market(market, event)
        for market in data.get("markets", []):
            add_market(market)
        for market in data.get("data", []):
            add_market(market)
    return results


def current_for(item):
    if item.get("venue") == "kalshi":
        return get_kalshi_market(item.get("ticker") or item.get("id"), source_url=item.get("url", ""))
    if item.get("venue") == "polymarket":
        data = http_json(f"https://gamma-api.polymarket.com/markets/{urllib.parse.quote(str(item.get('id')))}")
        return normalize_poly_market(data)
    raise RuntimeError("Unknown venue")


def refresh_watchlist(force=False):
    global live_cache
    with live_lock:
        if not force and time.time() - live_cache["updated_at"] < POLL_SECONDS - 1:
            return live_cache
    with store_lock:
        store = load_store()
        items = list(store.get("watchlist", []))

    markets = []
    for item in items:
        enriched = dict(item)
        try:
            current = current_for(item)
            enriched.update(current)
            enriched["ok"] = True
            enriched["error"] = ""
        except Exception as error:
            enriched["ok"] = False
            enriched["error"] = str(error)
            enriched["updated_at"] = now_ts()
        markets.append(enriched)

    payload = {"updated_at": now_ts(), "markets": markets}
    with live_lock:
        live_cache = payload
    return payload


def get_history(venue, identifier, side, range_name, item=None):
    end = now_ts()
    days = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}.get(range_name, 7)
    start = end - days * 86400
    side = side if side in ("yes", "no") else "yes"

    if venue == "kalshi":
        ticker = identifier
        series = (item or {}).get("series_ticker") or ticker.split("-")[0]
        period = 1 if days <= 1 else 60 if days <= 30 else 1440
        params = urllib.parse.urlencode({"start_ts": start, "end_ts": end, "period_interval": period})
        url = f"https://external-api.kalshi.com/trade-api/v2/series/{urllib.parse.quote(series)}/markets/{urllib.parse.quote(ticker)}/candlesticks?{params}"
        data = http_json(url)
        points = []
        for candle in data.get("candlesticks", []):
            price = candle.get("price", {})
            yes_bid = candle.get("yes_bid", {})
            yes_ask = candle.get("yes_ask", {})
            close = dollars_to_cents(price.get("close_dollars"))
            if close is None:
                continue
            p = close if side == "yes" else round(100 - close, 4)
            points.append({
                "t": candle.get("end_period_ts"),
                "p": p,
                "bid": dollars_to_cents(yes_bid.get("close_dollars")),
                "ask": dollars_to_cents(yes_ask.get("close_dollars")),
                "volume": numeric(candle.get("volume_fp")),
                "open_interest": numeric(candle.get("open_interest_fp")),
            })
        return {"points": points, "source": "kalshi-candlesticks", "range": range_name}

    if venue == "polymarket":
        token_id = (item or {}).get("yes_token_id" if side == "yes" else "no_token_id")
        if not token_id:
            token_id = identifier
        interval = "1h" if days <= 7 else "6h" if days <= 30 else "1d"
        fidelity = 60 if interval == "1h" else 360 if interval == "6h" else 1440
        params = urllib.parse.urlencode({"market": token_id, "startTs": start, "endTs": end, "interval": interval, "fidelity": fidelity})
        data = http_json("https://clob.polymarket.com/prices-history?" + params)
        points = [{"t": row.get("t"), "p": round(float(row.get("p", 0)) * 100, 4)} for row in data.get("history", [])]
        return {"points": points, "source": "polymarket-prices-history", "range": range_name}

    return {"points": [], "source": "", "range": range_name}


def find_watch_item(venue, identifier):
    with store_lock:
        store = load_store()
        for item in store.get("watchlist", []):
            if item.get("venue") == venue and str(item.get("id")) == str(identifier):
                return item
    return None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}")

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/search":
            return self.api_search(parsed)
        if parsed.path == "/api/watchlist":
            return self.send_json(refresh_watchlist(force=True))
        if parsed.path == "/api/history":
            return self.api_history(parsed)
        if parsed.path == "/api/stream":
            return self.api_stream()
        if parsed.path == "/healthz":
            return self.send_json({"ok": True, "time": now_ts()})
        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/watchlist":
            return self.api_add_watch()
        self.send_error(404)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/watchlist":
            return self.api_delete_watch(parsed)
        self.send_error(404)

    def api_search(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        venue = (query.get("venue", ["all"])[0] or "all").lower()
        text = query.get("q", [""])[0]
        intent = parse_link_or_text(text)
        if intent.get("source"):
            venue = intent["source"]
        results = []
        errors = []
        try:
            if venue in ("all", "kalshi"):
                results.extend(search_kalshi(text))
        except Exception as error:
            errors.append({"venue": "kalshi", "error": str(error)})
        try:
            if venue in ("all", "polymarket"):
                results.extend(search_polymarket(text))
        except Exception as error:
            errors.append({"venue": "polymarket", "error": str(error)})
        self.send_json({"results": results, "errors": errors})

    def api_history(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        venue = query.get("venue", [""])[0]
        identifier = query.get("id", [""])[0]
        side = query.get("side", ["yes"])[0]
        range_name = query.get("range", ["7d"])[0]
        item = find_watch_item(venue, identifier)
        try:
            self.send_json(get_history(venue, identifier, side, range_name, item=item))
        except Exception as error:
            self.send_json({"points": [], "error": str(error)}, status=502)

    def api_add_watch(self):
        item = self.read_json()
        if not item.get("venue") or not item.get("id"):
            return self.send_json({"error": "venue and id are required"}, status=400)
        item = normalize_watch_item(item)
        with store_lock:
            store = load_store()
            watchlist = [entry for entry in store.get("watchlist", []) if market_key(entry) != market_key(item)]
            item["added_at"] = item.get("added_at") or now_ts()
            watchlist.insert(0, item)
            store["watchlist"] = watchlist[:200]
            save_store(store)
        refresh_watchlist(force=True)
        self.send_json({"ok": True, "item": item})

    def api_delete_watch(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        key = query.get("key", [""])[0]
        with store_lock:
            store = load_store()
            store["watchlist"] = [item for item in store.get("watchlist", []) if market_key(item) != key]
            save_store(store)
        refresh_watchlist(force=True)
        self.send_json({"ok": True})

    def api_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                payload = refresh_watchlist()
                chunk = f"event: live\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
                self.wfile.write(chunk)
                self.wfile.flush()
                time.sleep(POLL_SECONDS)
        except (BrokenPipeError, ConnectionResetError):
            return


def normalize_watch_item(item):
    allowed = {
        "venue", "id", "ticker", "series_ticker", "slug", "condition_id", "yes_token_id", "no_token_id",
        "title", "event", "url", "status", "added_at", "yes_price", "no_price"
    }
    normalized = {key: item.get(key) for key in allowed if key in item}
    normalized["venue"] = str(normalized.get("venue", "")).lower()
    normalized["id"] = str(normalized.get("id", ""))
    normalized["title"] = normalized.get("title") or normalized["id"]
    if normalized["venue"] == "kalshi":
        normalized["ticker"] = normalized.get("ticker") or normalized["id"]
        normalized["series_ticker"] = normalized.get("series_ticker") or normalized["ticker"].split("-")[0]
    return normalized


def main():
    parser = argparse.ArgumentParser(description="Local Kalshi/Polymarket market tracker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Local market tracker running at http://{args.host}:{args.port}/")
    print("For SSH tunneling: ssh -L 8765:127.0.0.1:8765 <host>")
    server.serve_forever()


if __name__ == "__main__":
    main()
