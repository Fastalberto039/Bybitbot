import os
import asyncio
import aiohttp
from aiogram import Bot

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

TOP_N_COINS = 50
OI_CHANGE_THRESHOLD = 5.0
RETAIL_SKEW_THRESHOLD = 60.0
WHALE_SKEW_THRESHOLD = 55.0
HEATMAP_PROXIMITY_PCT = 1.5
PRICE_CHANGE_1H_MIN = 1.0
MAX_FUNDING_RATE = 0.0

WATCHLIST = [
    "APTUSDT", "ARBUSDT", "OPUSDT",
    "DOTUSDT", "ATOMUSDT", "ZROUSDT", "ZKUSDT", "STRKUSDT"
]

BYBIT_URLS = ["https://bybit.com", "https://bytick.com"]
BINANCE_BASE = "https://binance.com"
OKX_BASE = "https://okx.com"

async def fetch_json(session: aiohttp.ClientSession, url: str, params: dict = None):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(url, params=params, headers=headers, timeout=10) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"Ошибка запроса {url}: {e}")
    return None

async def fetch_bybit(session: aiohttp.ClientSession, endpoint: str, params: dict = None):
    for base in BYBIT_URLS:
        data = await fetch_json(session, f"{base}{endpoint}", params)
        if data and data.get("retCode") == 0:
            return data
    return None

async def get_target_symbols(session: aiohttp.ClientSession) -> list[dict]:
    data = await fetch_bybit(session, "/v5/market/tickers", {"category": "linear"})
    if not data:
        return []
    all_usdt = [
        item for item in data["result"]["list"]
        if item["symbol"].endswith("USDT") and not item["symbol"].endswith("PERP")
    ]
    all_usdt.sort(key=lambda x: float(x.get("turnover24h", 0)), reverse=True)
    top_coins = all_usdt[:TOP_N_COINS]
    top_names = {item["symbol"] for item in top_coins}
    extra_coins = [
        item for item in all_usdt
        if item["symbol"] in WATCHLIST and item["symbol"] not in top_names
    ]
    return top_coins + extra_coins

async def check_open_interest(session: aiohttp.ClientSession, symbol: str) -> float | None:
    data = await fetch_bybit(
        session,
        "/v5/market/open-interest",
        {"category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 2}
    )
    if not data or len(data["result"]["list"]) < 2:
        return None
    curr_item, prev_item = data["result"]["list"][:2]
    curr_oi = float(curr_item["openInterest"])
    prev_oi = float(prev_item["openInterest"])
    if prev_oi == 0:
        return None
    return ((curr_oi - prev_oi) / prev_oi) * 100

async def get_1h_price_change_and_candles(session: aiohttp.ClientSession, symbol: str, curr_price: float):
    data = await fetch_bybit(
        session,
        "/v5/market/kline",
        {"category": "linear", "symbol": symbol, "interval": "60", "limit": 48}
    )
    if not data or not data["result"]["list"]:
        return None, []
    candles = data["result"]["list"]
    latest_candle = next(iter(candles))
    _, open_str, *_ = latest_candle
    open_1h = float(open_str)
    price_change_1h = ((curr_price - open_1h) / open_1h) * 100 if open_1h > 0 else 0.0
    return price_change_1h, candles

async def check_whales_vs_retail(session: aiohttp.ClientSession, symbol: str) -> dict | None:
    bybit_data = await fetch_bybit(
        session,
        "/v5/market/account-ratio",
        {"category": "linear", "symbol": symbol, "period": "1h", "limit": 1}
    )
    if not bybit_data or not bybit_data["result"]["list"]:
        return None
    first_retail = next(iter(bybit_data["result"]["list"]))
    retail_buy = float(first_retail["buyRatio"]) * 100
    retail_sell = float(first_retail["sellRatio"]) * 100
    whale_buy = None
    whale_sell = None
    binance_data = await fetch_json(
        session,
        f"{BINANCE_BASE}/futures/data/topLongShortPositionRatio",
        {"symbol": symbol, "period": "1h", "limit": 1}
    )
    if binance_data and isinstance(binance_data, list) and len(binance_data) > 0:
        first_whale = next(iter(binance_data))
        whale_buy = float(first_whale["longAccount"]) * 100
        whale_sell = float(first_whale["shortAccount"]) * 100
    else:
        coin = symbol.replace("USDT", "")
        okx_data = await fetch_json(
            session,
            f"{OKX_BASE}/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader",
            {"ccy": coin, "period": "1H"}
        )
        if okx_data and okx_data.get("code") == "0" and okx_data.get("data"):
            first_row = next(iter(okx_data["data"]))
            _, ratio_str, *_ = first_row
            ratio = float(ratio_str)
            whale_buy = (ratio / (ratio + 1)) * 100
            whale_sell = 100 - whale_buy
    if whale_buy is None:
        return None
    return {
        "retail_long": round(retail_buy, 1),
        "retail_short": round(retail_sell, 1),
        "whale_long": round(whale_buy, 1),
        "whale_short": round(whale_sell, 1),
    }

def analyze_liquidation_heatmap(candles: list, curr_price: float) -> dict:
    liq_zones_above = []
    liq_zones_below = []
    for c in candles:
        _, _, high_str, low_str, _, vol_str, *_ = c
        high_p = float(high_str)
        low_p = float(low_str)
        vol = float(vol_str)
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
        zones.sort(key=lambda item: next(iter(item)), reverse=not is_above)
        volumes = sorted([v for _, v in zones])
        cutoff_idx = int(len(volumes) * 0.8)
        vol_threshold = volumes[cutoff_idx] if cutoff_idx < len(volumes) else volumes[-1]
        hot_zones = [(p, v) for p, v in zones if v >= vol_threshold]
        if not hot_zones:
            return None
        nearest_price, _ = next(iter(hot_zones))
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
        targets = await get_target_symbols(session)
        print(f"Сканирую монеты ({len(targets)} шт.): {[t['symbol'] for t in targets]}")
        for ticker in targets:
            symbol = ticker["symbol"]
            curr_price = float(ticker["lastPrice"])
            funding_rate = float(ticker.get("fundingRate", 0)) * 100
            oi_change = await check_open_interest(session, symbol)
            if oi_change is None or oi_change < OI_CHANGE_THRESHOLD:
                await asyncio.sleep(0.2)
                continue
            price_change_1h, candles = await get_1h_price_change_and_candles(session, symbol, curr_price)
            if price_change_1h is None:
                continue
            strat2_hit = (price_change_1h >= PRICE_CHANGE_1H_MIN) and (funding_rate < MAX_FUNDING_RATE)
            strat1_dir = None
            strat1_zone = None
            ratios = await check_whales_vs_retail(session, symbol)
            if ratios and candles:
                heatmap = analyze_liquidation_heatmap(candles, curr_price)
                if (
                    ratios["retail_short"] >= RETAIL_SKEW_THRESHOLD
                    and ratios["whale_long"] >= WHALE_SKEW_THRESHOLD
                    and heatmap["above"] is not None
                ):
                    strat1_dir = "🟢 ЛОНГ (Сквиз шортов)"
                    strat1_zone = heatmap["above"]
                elif (
                    ratios["retail_long"] >= RETAIL_SKEW_THRESHOLD
                    and ratios["whale_short"] >= WHALE_SKEW_THRESHOLD
                    and heatmap["below"] is not None
                ):
                    strat1_dir = "🔴 ШОРТ (Сквиз лонгов)"
                    strat1_zone = heatmap["below"]
            if strat2_hit or strat1_dir:
                lines = [
                    f"🚨 **СИГНАЛ ПО {symbol} (1Ч)**",
                    f"💰 **Цена:** `{curr_price}` (`{round(price_change_1h, 2)}%` за 1ч)",
                    f"📈 **Открытый интерес:** `+{round(oi_change, 2)}%` за 1ч",
                    f"💸 **Фандинг:** `{round(funding_rate, 4)}%`",
                ]
                if strat2_hit:
                    lines.append("✅ **Сработала Стратегия 2 (Топливо для лонга):**")
                    lines.append("• Цена растет + ОИ растет + отрицательный фандинг.")
                if strat1_dir and ratios and strat1_zone:
                    lines.append(f"✅ **Сработала Стратегия 1 ({strat1_dir}):**")
                    lines.append(f"• Толпа: `Лонг {ratios['retail_long']}%` / `Шорт {ratios['retail_short']}%`")
                    lines.append(f"• Киты: `Лонг {ratios['whale_long']}%` / `Шорт {ratios['whale_short']}%`")
                    lines.append(f"• Тепловая карта: магнит `{strat1_zone['price']}` (до цели `{strat1_zone['dist_pct']}%`)")
                await bot.send_message(CHAT_ID, "\n".join(lines), parse_mode="Markdown")
            await asyncio.sleep(0.4)
    await bot.session.close()
    print("Сканирование Топ-50 + Watchlist успешно завершено.")

if __name__ == "__main__":
    asyncio.run(scan_market())




