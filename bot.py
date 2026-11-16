import os
import asyncio
import aiohttp
from aiogram import Bot

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Настройки стратегии (1 Час)
OI_CHANGE_THRESHOLD = 8.0      # Рост ОИ от 8% за 1 час
RETAIL_SKEW_THRESHOLD = 60.0   # Перекос толпы > 60% в одну сторону
WHALE_SKEW_THRESHOLD = 55.0    # Перевес китов > 55% в противоположную сторону
HEATMAP_PROXIMITY_PCT = 1.5    # Подход к желто-оранжевой зоне ближе 1.5%

BYBIT_BASE = "https://bybit.com"
BINANCE_BASE = "https://binance.com"

async def fetch_json(session: aiohttp.ClientSession, url: str, params: dict = None):
    try:
        async with session.get(url, params=params, timeout=10) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"Ошибка запроса {url}: {e}")
    return None

async def get_top_10_symbols(session: aiohttp.ClientSession) -> list[dict]:
    """Получает топ-10 фьючерсов Bybit по обороту за 24ч."""
    data = await fetch_json(session, f"{BYBIT_BASE}/v5/market/tickers", {"category": "linear"})
    if not data or data.get("retCode") != 0:
        return []

    tickers = [
        item for item in data["result"]["list"]
        if item["symbol"].endswith("USDT") and not item["symbol"].endswith("PERP")
    ]
    tickers.sort(key=lambda x: float(x.get("turnover24h", 0)), reverse=True)
    return tickers[:10]

async def check_open_interest(session: aiohttp.ClientSession, symbol: str) -> float | None:
    """Условие 1: Изменение открытого интереса за 1 час (%)."""
    data = await fetch_json(
        session,
        f"{BYBIT_BASE}/v5/market/open-interest",
        {"category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 2}
    )
    if not data or data.get("retCode") != 0 or len(data["result"]["list"]) < 2:
        return None

    oi_list = data["result"]["list"]
    curr_oi = float(oi_list[0]["openInterest"])
    prev_oi = float(oi_list[1]["openInterest"])
    if prev_oi == 0:
        return None

    return ((curr_oi - prev_oi) / prev_oi) * 100

async def check_whales_vs_retail(session: aiohttp.ClientSession, symbol: str) -> dict | None:
    """Условие 2: Соотношение Лонг/Шорт (Толпа на Bybit vs Киты на Binance)."""
    bybit_data = await fetch_json(
        session,
        f"{BYBIT_BASE}/v5/market/account-ratio",
        {"category": "linear", "symbol": symbol, "period": "1h", "limit": 1}
    )
    binance_data = await fetch_json(
        session,
        f"{BINANCE_BASE}/futures/data/topLongShortPositionRatio",
        {"symbol": symbol, "period": "1h", "limit": 1}
    )

    if not bybit_data or bybit_data.get("retCode") != 0 or not bybit_data["result"]["list"]:
        return None
    if not binance_data or not isinstance(binance_data, list) or len(binance_data) == 0:
        return None

    retail_buy = float(bybit_data["result"]["list"][0]["buyRatio"]) * 100
    retail_sell = float(bybit_data["result"]["list"][0]["sellRatio"]) * 100

    whale_buy = float(binance_data[0]["longAccount"]) * 100
    whale_sell = float(binance_data[0]["shortAccount"]) * 100

    return {
        "retail_long": round(retail_buy, 1),
        "retail_short": round(retail_sell, 1),
        "whale_long": round(whale_buy, 1),
        "whale_short": round(whale_sell, 1),
    }

async def check_liquidation_heatmap(session: aiohttp.ClientSession, symbol: str, curr_price: float) -> dict | None:
    """Условие 3: Расчет желто-оранжевых кластеров ликвидаций (плечи 10x, 25x, 50x) по 1H свечам."""
    data = await fetch_json(
        session,
        f"{BYBIT_BASE}/v5/market/kline",
        {"category": "linear", "symbol": symbol, "interval": "60", "limit": 48}
    )
    if not data or data.get("retCode") != 0:
        return None

    candles = data["result"]["list"]
    liq_zones_above = []
    liq_zones_below = []

    for c in candles:
        high_p = float(c[2])
        low_p = float(c[3])
        vol = float(c[5])

        for lev_offset in [0.005, 0.018, 0.038]:
            zone_up = high_p * (1 + lev_offset)
            zone_down = low_p * (1 - lev_offset)
            if zone_up > curr_price:
                liq_zones_above.append((zone_up, vol))
            if zone_down < curr_price:
                liq_zones_below.append((zone_down, vol))

    def find_hottest_cluster(zones, is_above: bool):
        if not zones:
            return None
        zones.sort(key=lambda x: x[0], reverse=not is_above)
        vol_threshold = sorted([z[1] for z in zones])[int(len(zones) * 0.8)]
        hot_zones = [z for z in zones if z[1] >= vol_threshold]
        if not hot_zones:
            return None
        nearest_price = hot_zones[0][0]
        dist_pct = abs(nearest_price - curr_price) / curr_price * 100
        if dist_pct <= HEATMAP_PROXIMITY_PCT:
            return {"price": round(nearest_price, 4), "dist_pct": round(dist_pct, 2)}
        return None

    return {
        "above": find_hottest_cluster(liq_zones_above, True),
        "below": find_hottest_cluster(liq_zones_below, False),
    }

async def scan_market():
    bot = Bot(token=BOT_TOKEN)
    async with aiohttp.ClientSession() as session:
        top_10 = await get_top_10_symbols(session)
        print(f"Топ-10 монет Bybit: {[t['symbol'] for t in top_10]}")

        for ticker in top_10:
            symbol = ticker["symbol"]
            curr_price = float(ticker["lastPrice"])

            oi_change = await check_open_interest(session, symbol)
            if oi_change is None or oi_change < OI_CHANGE_THRESHOLD:
                continue

            ratios = await check_whales_vs_retail(session, symbol)
            if not ratios:
                continue

            heatmap = await check_liquidation_heatmap(session, symbol, curr_price)
            if not heatmap:
                continue

            is_long_setup = (
                ratios["retail_short"] >= RETAIL_SKEW_THRESHOLD
                and ratios["whale_long"] >= WHALE_SKEW_THRESHOLD
                and heatmap["above"] is not None
            )

            is_short_setup = (
                ratios["retail_long"] >= RETAIL_SKEW_THRESHOLD
                and ratios["whale_short"] >= WHALE_SKEW_THRESHOLD
                and heatmap["below"] is not None
            )

            if is_long_setup or is_short_setup:
                direction = "🟢 ЛОНГ (Сквиз шортов)" if is_long_setup else "🔴 ШОРТ (Сквиз лонгов)"
                zone_info = heatmap["above"] if is_long_setup else heatmap["below"]

                msg = (
                    f"🔥 **СИГНАЛ 3/3: {symbol} (1Ч)**\n"
                    f"**Сценарий:** {direction}\n"
                    f"**Цена:** `{curr_price}`\n\n"
                    f"1️⃣ **Открытый интерес:** `+{round(oi_change, 2)}%` за 1ч\n"
                    f"2️⃣ **Перекос (Киты vs Толпа):**\n"
                    f"• Толпа: `Лонг {ratios['retail_long']}%` / `Шорт {ratios['retail_short']}%`\n"
                    f"• Киты: `Лонг {ratios['whale_long']}%` / `Шорт {ratios['whale_short']}%`\n"
                    f"3️⃣ **Тепловая карта (Желто-оранжевая зона):**\n"
                    f"• Магнит ликвидности: `{zone_info['price']}` (дистанция `{zone_info['dist_pct']}%`)"
                )
                await bot.send_message(CHAT_ID, msg, parse_mode="Markdown")

            await asyncio.sleep(0.5)

    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(scan_market())
