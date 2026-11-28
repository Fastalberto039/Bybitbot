import os
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

RAW_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if "/bot" in RAW_TOKEN:
    RAW_TOKEN = RAW_TOKEN.split("/bot", 1).pop(1).split("/", 1).pop(0).strip()
BOT_TOKEN = RAW_TOKEN
CHAT_ID = os.getenv("CHAT_ID", "").strip()

BINANCE_MIRRORS = [
    "https://binance.com",
    "https://binance.com",
    "https://binance.com",
    "https://binance.com",
    "https://binance.com",
]

LOOKBACK = 20
SPIKE_MULT = 1.1
EXTREME_MULT = 4.5
RANK_START = 19
RANK_END = 250
MIN_AVG_5M_USDT = 15_000
COOLDOWN_SEC = 900
MAX_WORKERS = 20
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
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: BOT_TOKEN или CHAT_ID пустые!")
        return False
            url = "https://" + f"api.telegram.org/bot{BOT_TOKEN}/sendMessage"


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
    return True
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
                selected = [t["symbol"] for t in usdt_pairs[RANK_START:RANK_END]]
                print(f"Mirror OK: {base_url} | Монет Топ 20-250: {len(selected)}")
                return base_url, selected
        except Exception as e:
            last_err = e
    raise RuntimeError(f"All mirrors failed: {last_err}")


def check_binance_symbol(base_url, symbol, last_sent_ts, now):
    if now - float(last_sent_ts) < COOLDOWN_SEC:
        return symbol, None, 0.0, None

    limit = LOOKBACK + 2
    url = f"{base_url}/fapi/v1/klines?symbol={symbol}&interval=5m&limit={limit}"
    candles = fetch_json(url)
    if not isinstance(candles, list) or len(candles) < limit:
        return symbol, None, 0.0, None

    live_candle = candles.pop()
    closed_candle = candles.pop()
    history = candles[-LOOKBACK:]

    avg_usdt = sum(float(c.pop(7)) for c in history) / LOOKBACK
    if avg_usdt < MIN_AVG_5M_USDT:
        return symbol, None, 0.0, None

    live_vol = float(live_candle.pop(7))
    closed_vol = float(closed_candle.pop(7))

    live_ratio = live_vol / avg_usdt
    closed_ratio = closed_vol / avg_usdt

    if live_ratio >= closed_ratio:
        target, vol_usdt, ratio, state = live_candle, live_vol, live_ratio, "🔥 ПРЯМО СЕЙЧАС (текущая 5m)"
    else:
        target, vol_usdt, ratio, state = closed_candle, closed_vol, closed_ratio, "✅ ЗАКРЫТАЯ 5m свеча"

    c = float(target.pop(4))
    l = float(target.pop(3))
    h = float(target.pop(2))
    o = float(target.pop(1))

    move_pct = ((c - o) / o) * 100
    candle_icon = "🟢" if c >= o else "🔴"
    header = "🚨 ЭКСТРЕМАЛЬНЫЙ ОБЪЁМ 5m" if ratio >= EXTREME_MULT else "⚡ АНОМАЛИЯ ОБЪЁМА 5m"

    text = (
        f"{header}: <b>{symbol}</b> (Binance Топ 20-250)\n"
        f"📊 Всплеск: <b>x{ratio:.1f}</b> от среднего 5m ({state})\n"
        f"💰 Объём 5m: <b>{vol_usdt:,.0f}$</b> (средний: {avg_usdt:,.0f}$)\n"
        f"{candle_icon} Свеча: <b>{move_pct:+.2f}%</b> | Цена: <code>{c}</code>\n"
        f"📏 Диапазон 5m: <code>{l}</code> — <code>{h}</code>"
    )

    if ratio >= SPIKE_MULT:
        return symbol, text, ratio, text
    return symbol, None, ratio, text


def main():
    t0 = time.time()
    now = t0
    cache = load_cache()
    cache = {k: v for k, v in cache.items() if now - float(v) < 7200}

    base_url, symbols = get_working_mirror_and_symbols()
    alerts = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(check_binance_symbol, base_url, sym, cache.get(sym, 0), now): sym
            for sym in symbols
        }
        for fut in as_completed(futures):
            try:
                sym, msg, ratio, _ = fut.result()
                if msg:
                    alerts.append((sym, msg))
            except Exception:
                continue

    elapsed = time.time() - t0
    print(f"Готово за {elapsed:.1f}с | Проверено: {len(symbols)} | Аномалий: {len(alerts)}")

    for sym, text in alerts:
        try:
            if send_telegram(text):
                cache[sym] = now
                time.sleep(0.3)
        except Exception as e:
            print(f"TG Error ({sym}): {e}")

    save_cache(cache)


if __name__ == "__main__":
    main()




