import os
import time
from datetime import datetime, timezone
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ИСПРАВЛЕНО: добавлен поддомен fapi для работы с фьючерсами Binance
BASE_URL = "https://binance.com"

# Исключаем стейблкоины и индексные контракты
EXCLUDED_SYMBOLS = {
    "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "USDPUSDT",
    "FDUSDUSDT", "BTCDOMUSDT", "DEFIUSDT"
}


def send_telegram(text: str):
    """Отправка сообщения в Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Ошибка: не заданы TELEGRAM_TOKEN или TELEGRAM_CHAT_ID")
        return
    # ИСПРАВЛЕНО: правильный формат URL для Telegram Bot API
    url = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=10)
    if not resp.ok:
        print(f"Ошибка Telegram API: {resp.text}")


def get_top_10_symbols() -> list:
    """Топ-10 самых популярных фьючерсов USDT-M по объему торгов за 24ч."""
    resp = requests.get(f"{BASE_URL}/fapi/v1/ticker/24hr", timeout=10)
    resp.raise_for_status()
    data = resp.json()

    usdt_pairs = [
        item for item in data
        if item["symbol"].endswith("USDT") and item["symbol"] not in EXCLUDED_SYMBOLS
    ]
    usdt_pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return [item["symbol"] for item in usdt_pairs[:10]]


def calculate_daily_vwap(symbol: str) -> float:
    """Точный суточный VWAP от 00:00 UTC по 15-минутным свечам."""
    now_utc = datetime.now(timezone.utc)
    start_of_day = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    start_ms = int(start_of_day.timestamp() * 1000)

    resp = requests.get(
        f"{BASE_URL}/fapi/v1/klines",
        params={"symbol": symbol, "interval": "15m", "startTime": start_ms, "limit": 100},
        timeout=10,
    )
    resp.raise_for_status()
    klines = resp.json()

    cum_pv = 0.0
    cum_vol = 0.0

    for k in klines:
        high = float(k[2])
        low = float(k[3])
        close = float(k[4])
        volume = float(k[5])
        typical_price = (high + low + close) / 3.0

        cum_pv += typical_price * volume
        cum_vol += volume

    return cum_pv / cum_vol if cum_vol > 0 else 0.0


def get_market_metrics(symbol: str) -> dict:
    """Сбор цены, VWAP (15m), ОИ (1H), фандинга и L/S толпы и китов."""
    # 1. Последние 3 свечи 1H
    klines_resp = requests.get(
        f"{BASE_URL}/fapi/v1/klines",
        params={"symbol": symbol, "interval": "1h", "limit": 3},
        timeout=10,
    )
    klines_resp.raise_for_status()
    klines = klines_resp.json()

    prev_high = float(klines[-2][2])
    prev_close = float(klines[-2][4])
    curr_high = float(klines[-1][2])
    curr_close = float(klines[-1][4])
    price_change_pct = ((curr_close - prev_close) / prev_close) * 100

    # 2. Точный суточный VWAP по 15-минуткам
    vwap = calculate_daily_vwap(symbol)
    if vwap > 0:
        vwap_diff_pct = ((curr_close - vwap) / vwap) * 100
        price_str = f"{curr_close:.8f}".rstrip("0").rstrip(".")
        decimals = len(price_str.split(".")[1]) if "." in price_str else 2
        vwap_rounded = round(vwap, max(decimals, 2))
    else:
        vwap_diff_pct = 0.0
        vwap_rounded = curr_close

    # 3. Открытый интерес (1H)
    oi_resp = requests.get(
        f"{BASE_URL}/futures/data/openInterestHist",
        params={"symbol": symbol, "period": "1h", "limit": 2},
        timeout=10,
    )
    oi_resp.raise_for_status()
    oi_hist = oi_resp.json()
    oi_prev = float(oi_hist[-2]["sumOpenInterestValue"])
    oi_curr = float(oi_hist[-1]["sumOpenInterestValue"])
    oi_change_pct = ((oi_curr - oi_prev) / oi_prev) * 100

    # 4. Текущий фандинг
    prem_resp = requests.get(
        f"{BASE_URL}/fapi/v1/premiumIndex",
        params={"symbol": symbol},
        timeout=10,
    )
    prem_resp.raise_for_status()
    premium = prem_resp.json()
    funding_pct = float(premium["lastFundingRate"]) * 100

    # 5. Толпа L/S (Global Long/Short Account Ratio)
    crowd_resp = requests.get(
        f"{BASE_URL}/futures/data/globalLongShortAccountRatio",
        params={"symbol": symbol, "period": "1h", "limit": 1},
        timeout=10,
    )
    crowd_resp.raise_for_status()
    crowd_ls = crowd_resp.json()
    crowd_long = round(float(crowd_ls[-1]["longAccount"]) * 100, 1)
    crowd_short = round(float(crowd_ls[-1]["shortAccount"]) * 100, 1)

    # 6. Киты L/S (Top Trader Long/Short Position Ratio)
    whales_resp = requests.get(
        f"{BASE_URL}/futures/data/topLongShortPositionRatio",
        params={"symbol": symbol, "period": "1h", "limit": 1},
        timeout=10,
    )
    whales_resp.raise_for_status()
    whales_ls = whales_resp.json()
    whales_long = round(float(whales_ls[-1]["longAccount"]) * 100, 1)
    whales_short = round(float(whales_ls[-1]["shortAccount"]) * 100, 1)

    return {
        "symbol": symbol,
        "price": curr_close,
        "price_change": round(price_change_pct, 2),
        "vwap": vwap_rounded,
        "vwap_diff": round(vwap_diff_pct, 2),
        "oi_change": round(oi_change_pct, 2),
        "funding": round(funding_pct, 4),
        "crowd_long": crowd_long,
        "crowd_short": crowd_short,
        "whales_long": whales_long,
        "whales_short": whales_short,
        "false_breakout_high": curr_high > prev_high and curr_close < prev_high,
    }


def check_setups(m: dict) -> tuple:
    """
    Проверка условий (сетапов) #1–#8.
    Возвращает список сработавших сетапов и дословный вердикт (ЛОНГ или ШОРТ).
    """
    triggered = []
    long_votes = 0
    short_votes = 0

    # --- СЕТАПЫ В ЛОНГ ---

    # #1 Набор в ЛОНГ выше VWAP
    if m["price_change"] >= 1.5 and m["oi_change"] >= 2.0 and 0 < m["vwap_diff"] < 3.5:
        triggered.append("🟢 #1 Набор в ЛОНГ выше VWAP (Цена↑ + ОИ↑)")
        long_votes += 1

    # #2 Топливо для ЛОНГА
    if m["price_change"] >= 1.0 and m["oi_change"] >= 2.0 and m["funding"] < 0:
        triggered.append("🟢 #2 Топливо для ЛОНГА (Цена↑ + ОИ↑ + Фандинг<0)")
        long_votes += 1

    # #3 Шорт-сквиз
    if m["price_change"] >= 2.0 and m["oi_change"] <= -1.5 and m["vwap_diff"] > 0:
        triggered.append("🟢 #3 Шорт-сквиз (Цена↑ + ОИ↓)")
        long_votes += 1

    # #4 Киты в ЛОНГ против толпы
    if m["crowd_short"] >= 55.0 and m["whales_long"] >= 55.0:
        triggered.append("🟢 #4 Киты в ЛОНГ против толпы")
        long_votes += 1

    # --- СЕТАПЫ В ШОРТ ---

    # #5 Набор в ШОРТ ниже VWAP
    if m["price_change"] <= -1.5 and m["oi_change"] >= 2.0 and m["vwap_diff"] < 0:
        triggered.append("🔴 #5 Набор в ШОРТ ниже VWAP (Цена↓ + ОИ↑)")
        short_votes += 1

    # #6 Топливо для ШОРТА
    if m["price_change"] <= -1.0 and m["oi_change"] >= 2.0 and m["funding"] >= 0.02:
        triggered.append("🔴 #6 Топливо для ШОРТА (Цена↓ + ОИ↑ + Фандинг↑)")
        short_votes += 1

    # #7 Дивергенция ОИ (ШОРТ на ложном перехае)
    if m["price_change"] >= 0.8 and m["oi_change"] < 0 and (m["false_breakout_high"] or m["crowd_long"] >= 65.0):
        triggered.append("🔴 #7 Дивергенция ОИ (ШОРТ на ложном перехае)")
        short_votes += 1

    # #8 Перекупленность от VWAP + падение ОИ (откат вниз к среднему)
    if m["vwap_diff"] >= 3.5 and m["oi_change"] < 0:
        triggered.append("🔴 #8 Перегрев выше VWAP (Откат вниз к среднему)")
        short_votes += 1

    # --- ФОРМИРУЕМ ДОСЛОВНЫЙ ВЕРДИКТ ---
    if long_votes > 0 and short_votes == 0:
        verdict = "🟢 <b>ИТОГ: Лучше рассмотреть ЛОНГ</b>"
    elif short_votes > 0 and long_votes == 0:
        verdict = "🔴 <b>ИТОГ: Лучше рассмотреть ШОРТ</b>"
    elif long_votes > 0 and short_votes > 0:
        verdict = "⚖️ <b>ИТОГ: Сигналы противоречат друг другу (лучше пропустить)</b>"
    else:
        verdict = ""

    return triggered, verdict


def format_message(m: dict, setups: list, verdict: str) -> str:
    """Форматирование карточки сигнала для Telegram."""
    setups_text = "\n".join([f"• {s}" for s in setups])
    vwap_sign = "+" if m["vwap_diff"] > 0 else ""
    vwap_pos = "выше" if m["vwap_diff"] >= 0 else "ниже"

    return (
        f"⚡️ <b>СИГНАЛ: {m['symbol']} (1Ч) | Binance Futures</b>\n"
        f"{verdict}\n\n"
        f"💰 <b>Цена:</b> {m['price']} ({m['price_change']}% за 1ч)\n"
        f"⚖️ <b>VWAP (день):</b> {m['vwap']} ({vwap_sign}{m['vwap_diff']}% — {vwap_pos})\n"
        f"📈 <b>ОИ (1ч):</b> {m['oi_change']}% | 💸 <b>Фандинг:</b> {m['funding']}%\n"
        f"👥 <b>Толпа (L/S):</b> {m['crowd_long']}% / {m['crowd_short']}% | "
        f"🐋 <b>Киты:</b> {m['whales_long']}% / {m['whales_short']}%\n\n"
        f"<b>Сработавшие сетапы:</b>\n"
        f"{setups_text}"
    )


def main():
    symbols = get_top_10_symbols()
    print(f"Проверяем топ-10 монет: {symbols}")

    for sym in symbols:
        try:
            metrics = get_market_metrics(sym)
            setups, verdict = check_setups(metrics)
            if setups:
                send_telegram(format_message(metrics, setups, verdict))
                time.sleep(0.5)
        except Exception as e:
            print(f"Ошибка при обработке {sym}: {e}")


if __name__ == "__main__":
    main()











