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
SPIKE_MULT = 3.0
EXTREME_MULT = 5.0
RANK_START = 19
RANK_END = 250
MIN_AVG_5M_USDT = 15_000
MIN_BODY_RATIO = 0.45
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
        return False
    host = "api.telegram.org"
    url = f"https://{host}/bot{BOT_TOKEN}/sendMessage"
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
        return symbol, None

    limit = LOOKBACK + 2
    url = f"{base_url}/fapi/v1/klines?symbol={symbol}&interval=5m&limit={limit}"
    candles = fetch_json(url)
    if not isinstance(candles, list) or len(candles) < limit:
        return symbol, None

    live_candle = candles.pop()
    closed_candle = candles.pop()
    history = candles[-LOOKBACK:]

    highs_20 = [float(x[2]) for x in history]
    lows_20 = [float(x[3]) for x in history]
    vols_20 = [float(x[7]) for x in history]
    avg_usdt = sum(vols_20) / LOOKBACK
    if avg_usdt < MIN_AVG_5M_USDT:
        return symbol, None

    range_hi = max(highs_20)
    range_lo = min(lows_20)

    live_vol = float(live_candle[7])
    closed_vol = float(closed_candle[7])
    live_ratio = live_vol / avg_usdt
    closed_ratio = closed_vol / avg_usdt

    if live_ratio >= closed_ratio:
        target, vol_usdt, ratio, state = live_candle, live_vol, live_ratio, "🔥 ПРЯМО СЕЙЧАС (5m)"
    else:
        target, vol_usdt, ratio, state = closed_candle, closed_vol, closed_ratio, "✅ ЗАКРЫТАЯ 5m"

    if ratio < SPIKE_MULT:
        return symbol, None

    taker_buy_usdt = float(target[10])
    c = float(target[4])
    l = float(target[3])
    h = float(target[2])
    o = float(target[1])

    full_range = h - l
    body_ratio = (abs(c - o) / full_range) if full_range > 0 else 0.0
    if body_ratio < MIN_BODY_RATIO:
        return symbol, None

    buy_pct = (taker_buy_usdt / vol_usdt * 100.0) if vol_usdt > 0 else 50.0
    is_green = c >= o
    aggr_pct = buy_pct if is_green else (100.0 - buy_pct)
    if aggr_pct < 58.0:
        return symbol, None

    is_break_hi = is_green and c > range_hi
    is_break_lo = (not is_green) and c < range_lo

    oi_chg = 0.0
    oi_txt = "нет данных"
    try:
        oi_data = fetch_json(f"{base_url}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=4")
        if isinstance(oi_data, list) and len(oi_data) >= 2:
            oi_first = float(next(iter(oi_data))["sumOpenInterestValue"])
            oi_last = float(oi_data.pop()["sumOpenInterestValue"])
            if oi_first > 0:
                oi_chg = (oi_last - oi_first) / oi_first * 100.0
                oi_note = "новые деньги!" if oi_chg >= 0.5 else ("ликвидации" if oi_chg <= -0.5 else "ровно")
                oi_txt = f"{oi_chg:+.2f}% ({oi_note})"
    except Exception:
        pass

    if (is_break_hi or is_break_lo) and oi_chg >= 0.5 and ratio >= 3.0 and aggr_pct >= 60.0:
        pattern_title = "🚀 ПАТТЕРН А: ПРОБОЙ НА НОВЫХ ДЕНЬГАХ (Вход по тренду / на ретесте)"
        action_hint = "Входим по направлению пробоя (или лимиткой на 30% отката свечи), если до 24ч хай/лоя есть запас >1.5%."
    elif (is_break_hi or is_break_lo) and oi_chg <= -0.5 and ratio >= 4.0 and aggr_pct >= 60.0:
        pattern_title = "🪤 ПАТТЕРН Б: ВЫНОС ЛИКВИДАЦИЙ (Не догонять! Ждём ложный пробой)"
        action_hint = "Пробой на закрытии стопов! По рынку НЕ входим — ждём возврат цены обратно за пробитый уровень для входа в контртренд."
    elif (not is_break_hi and not is_break_lo) and oi_chg >= 0.8 and ratio >= 3.0 and aggr_pct >= 62.0:
        pattern_title = "🧨 ПАТТЕРН В: НАБОР КРУПНОГО ИГРОКА В ДИАПАЗОНЕ (Ранний выход)"
        action_hint = "Крупный участник заливает новые деньги до пробоя уровня — готовимся к выходу из 20-свечного диапазона."
    else:
        return symbol, None

    if is_green:
        side = "🟢 ЛОНГ"
        sl = round(l * 0.9985, 6)
        sl_pct = max((c - sl) / c * 100.0, 0.35)
        tp1 = round(c * (1.0 + sl_pct * 2.0 / 100.0), 6)
        tp2 = round(c * (1.0 + sl_pct * 3.0 / 100.0), 6)
    else:
        side = "🔴 ШОРТ"
        sl = round(h * 1.0015, 6)
        sl_pct = max((sl - c) / c * 100.0, 0.35)
        tp1 = round(c * (1.0 - sl_pct * 2.0 / 100.0), 6)
        tp2 = round(c * (1.0 - sl_pct * 3.0 / 100.0), 6)

    pos_usdt = min(round(2.20 / (sl_pct / 100.0)), 250)
    move_pct = ((c - o) / o) * 100.0
    aggr_word = "покупки" if is_green else "продажи"

    text = (
        f"<b>{pattern_title}</b>\n"
        f"🪙 Монета: <b>{symbol}</b> ({side} | {state})\n"
        f"📊 Объём: <b>x{ratio:.1f}</b> ({vol_usdt:,.0f}$ / ср. {avg_usdt:,.0f}$) | Свеча: <b>{move_pct:+.2f}%</b>\n"
        f"⚖️ Агрессия: <b>{aggr_word} {aggr_pct:.0f}%</b> | 📈 <b>ОИ (15m): {oi_txt}</b>\n"
        f"💡 <b>Действие:</b> {action_hint}\n"
        f"📐 <b>План (риск $2.20 → ~{pos_usdt} USDT):</b>\n"
        f"• Вход: <code>{c}</code> | SL: <code>{sl}</code> (-{sl_pct:.2f}%)\n"
        f"• TP1 (50% в БУ): <code>{tp1}</code> | TP2: <code>{tp2}</code>"
    )
    return symbol, text



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
                sym, msg = fut.result()
                if msg:
                    alerts.append((sym, msg))
            except Exception:
                continue

    elapsed = time.time() - t0
    print(f"Готово за {elapsed:.1f}с | Проверено: {len(symbols)} | Умных аномалий: {len(alerts)}")

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






