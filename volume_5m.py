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

LOOKBACK = 20                 # Средний объём за 20 закрытых 5m свечей (100 мин)
SPIKE_MULT = 2.2              # Порог аномалии: в 2.2x от среднего (оптимально для выходных)
EXTREME_MULT = 4.0            # Экстремальный всплеск: в 4.0x и выше
RANK_START = 19               # С 20-го места по обороту (как в основном боте Топ 20-250)
RANK_END = 250                # По 250-е место по обороту
MIN_AVG_5M_USDT = 15_000      # Средний 5m объём > $15,000
COOLDOWN_SEC = 900            # Анти-спам: 15 минут на монету
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
        print("ERROR: BOT_TOKEN или CHAT_ID пустые!")
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
                selected = [t["symbol"] for t in usdt_pairs[RANK_START:RANK_END]]
                print(f"Mirror OK: {base_url} | Монет Топ 20-250: {len(selected)}")
                return base_url, selected
        except Exception as e:
            last_err = e
            print(f"Mirror fail {base_url}: {e}")
    raise RuntimeError(f"All mirrors failed: {last_err}")


def check_binance_symbol(base_url, symbol, cache, now):
    if now - float(cache.get(symbol, 0)) < COOLDOWN_SEC:
        return None, 0.0, None

    limit = LOOKBACK + 2
    url = f"{base_url}/fapi/v1/klines?symbol={symbol}&interval=5m&limit={limit}"
    candles = fetch_json(url)
    if not isinstance(candles, list) or len(candles) < limit:
        return None, 0.0, None

    history = candles[:-2][-LOOKBACK:]
    avg_usdt = sum(float(c[7]) for c in history) / LOOKBACK
    if avg_usdt < MIN_AVG_5M_USDT:
        return None, 0.0, None

    live_candle = candles[-1]
    closed_candle = candles[-2]

    live_ratio = float(live_candle[7]) / avg_usdt
    closed_ratio = float(closed_candle[7]) / avg_usdt

    if live_ratio >= closed_ratio:
        target, ratio, state = live_candle, live_ratio, "🔥 ПРЯМО СЕЙЧАС (текущая 5m)"
    else:
        target, ratio, state = closed_candle, closed_ratio, "✅ ЗАКРЫТАЯ 5m свеча"

    o = float(target[1])
    h = float(target[2])
    l = float(target[3])
    c = float(target[4])
    vol_usdt = float(target[7])

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
        cache[symbol] = now
        return text, ratio, text
    return None, ratio, text


def main():
    now = time.time()
    cache = load_cache()
    is_first_run = not os.path.exists(CACHE_FILE)
    cache = {k: v for k, v in cache.items() if now - float(v) < 7200}

    base_url, symbols = get_working_mirror_and_symbols()
    alerts = []
    best_ratio = 0.0
    best_preview = None

    for sym in symbols:
        try:
            msg, ratio, preview = check_binance_symbol(base_url, sym, cache, now)
            if ratio > best_ratio and preview:
                best_ratio = ratio
                best_preview = preview
            if msg:
                alerts.append(msg)
            time.sleep(0.04)
        except Exception:
            continue

    print(f"Проверено: {len(symbols)} | Аномалий >= x{SPIKE_MULT}: {len(alerts)} | Макс. всплеск: x{best_ratio:.2f}")

    # При самом первом запуске (пока нет volume_cache.json), если рынок тихий — пришлём лидер-всплеск для проверки связи
    if is_first_run and not alerts and best_preview:
        alerts.append("🧪 <b>ТЕСТ ПЕРВОГО ЗАПУСКА (самый высокий всплеск прямо сейчас):</b>\n\n" + best_preview)

    for text in alerts:
        try:
            send_telegram(text)
            time.sleep(0.4)
        except Exception as e:
            print(f"TG Error: {e}")

    save_cache(cache)


if __name__ == "__main__":
    main()

