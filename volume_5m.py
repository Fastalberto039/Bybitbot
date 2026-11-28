import os
import json
import time
import urllib.request

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

BINANCE_MIRRORS = [
    "https://binance.com",
    "https://binance.com",
    "https://binance.com",
    "https://binance.com",
    "https://binance.com",
]

LOOKBACK = 20
SPIKE_MULT = 3.0
EXTREME_MULT = 5.0
TOP_COINS = 150
MIN_AVG_5M_USDT = 25_000
COOLDOWN_SEC = 900
CACHE_FILE = "volume_cache.json"


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://telegram.org{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def get_working_mirror_and_symbols():
    last_err = None
    for base_url in BINANCE_MIRRORS:
        try:
            tickers = fetch_json(f"{base_url}/fapi/v1/ticker/24hr")
            if isinstance(tickers, list) and len(tickers) > 0:
                usdt_pairs = [
                    t for t in tickers
                    if str(t.get("symbol", "")).endswith("USDT")
                    and "_" not in str(t.get("symbol", ""))
                ]
                usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume") or 0), reverse=True)
                print(f"Mirror OK: {base_url}")
                return base_url, [t["symbol"] for t in usdt_pairs[:TOP_COINS]]
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Mirrors failed: {last_err}")


def check_binance_symbol(base_url, symbol, cache, now):
    if now - float(cache.get(symbol, 0)) < COOLDOWN_SEC:
        return None

    limit = LOOKBACK + 2
    url = f"{base_url}/fapi/v1/klines?symbol={symbol}&interval=5m&limit={limit}"
    candles = fetch_json(url)
    if not isinstance(candles, list) or len(candles) < limit:
        return None

    history = candles[:-2][-LOOKBACK:]
    avg_usdt = sum(float(c) for c in history) / LOOKBACK
    if avg_usdt < MIN_AVG_5M_USDT:
        return None

    live_candle = candles[-1]
    closed_candle = candles[-2]

    live_ratio = float(live_candle) / avg_usdt
    closed_ratio = float(closed_candle) / avg_usdt

    if live_ratio >= SPIKE_MULT:
        target, ratio, state = live_candle, live_ratio, "🔥 ПРЯМО СЕЙЧАС (текущая 5m)"
    elif closed_ratio >= SPIKE_MULT:
        target, ratio, state = closed_candle, closed_ratio, "✅ ЗАКРЫТАЯ 5m свеча"
    else:
        return None

    o = float(target)
    h = float(target)
    l = float(target)
    c = float(target)
    vol_usdt = float(target)

    move_pct = ((c - o) / o) * 100
    candle_icon = "🟢" if c >= o else "🔴"
    header = "🚨 ЭКСТРЕМАЛЬНЫЙ ОБЪЁМ 5m" if ratio >= EXTREME_MULT else "⚡ АНОМАЛИЯ ОБЪЁМА 5m"

    cache[symbol] = now
    return (
        f"{header}: <b>{symbol}</b> (Binance)\n"
        f"📊 Всплеск: <b>x{ratio:.1f}</b> от среднего 5m ({state})\n"
        f"💰 Объём 5m: <b>{vol_usdt:,.0f}$</b> (средний: {avg_usdt:,.0f}$)\n"
        f"{candle_icon} Свеча: <b>{move_pct:+.2f}%</b> | Цена: <code>{c}</code>\n"
        f"📏 Диапазон 5m: <code>{l}</code> — <code>{h}</code>"
    )


def main():
    now = time.time()
    cache = load_cache()
    cache = {k: v for k, v in cache.items() if now - float(v) < 7200}

    base_url, symbols = get_working_mirror_and_symbols()
    alerts = []
    for sym in symbols:
        try:
            msg = check_binance_symbol(base_url, sym, cache, now)
            if msg:
                alerts.append(msg)
            time.sleep(0.05)
        except Exception:
            continue

    for text in alerts:
        try:
            send_telegram(text)
            time.sleep(0.4)
        except Exception:
            pass

    save_cache(cache)


if __name__ == "__main__":
    main()
