import os
import asyncio
import aiohttp
from aiogram import Bot
from setups import evaluate_all_9_setups

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TOP_N_COINS = 50
WATCHLIST = ["APTUSDT", "ARBUSDT", "OPUSDT", "DOTUSDT", "ATOMUSDT", "ZROUSDT", "ZKUSDT", "STRKUSDT"]
STOCK_BLACKLIST = {
    "MUUUSDT", "KORUUSDT", "SOXLUSDT", "SOXSUSDT", "TQQQUSDT", "SQQQUSDT",
    "NVDAUSDT", "TSLAUSDT", "AAPLUSDT", "AMZNUSDT", "MSFTUSDT", "GOOGLUSDT",
    "GOOGUSDT", "METAUSDT", "COINUSDT", "MSTRUSDT", "HOODUSDT", "PLTRUSDT",
    "AMDUSDT", "INTCUSDT", "NFLXUSDT", "BABAUSDT", "QQQUSDT", "SPYUSDT",
    "GLDUSDT", "SLVUSDT", "XAUUSDT", "XAGUSDT", "CRCLUSDT", "IBITUSDT", "ETHAUSDT"
}
BINANCE_URLS = ["https://binance.com", "https://binance.com", "https://binance.com"]
BYBIT_URLS = ["https://bybit.com", "https://bytick.com"]

def is_valid_crypto(symbol: str, allowed_symbols: set | None = None) -> bool:
    if not symbol.endswith("USDT") or "_" in symbol or symbol.endswith("PERP"):
        return False
    if symbol in STOCK_BLACKLIST or "STOCK" in symbol:
        return False
    if allowed_symbols is not None and symbol not in allowed_symbols:
        return False
    return True

async def fetch_json(session, url, params=None):
    try:
        async with session.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=10) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception:
        pass
    return None

async def get_active_source(session):
    for base in BINANCE_URLS:
        if await fetch_json(session, f"{base}/fapi/v1/ping") is not None:
            return base
    return "BYBIT"

async def fetch_bybit(session, endpoint, params=None):
    for base in BYBIT_URLS:
        data = await fetch_json(session, f"{base}{endpoint}", params)
        if data and data.get("retCode") == 0:
            return data
    return None

async def get_binance_crypto_set(session, source):
    info = await fetch_json(session, f"{source}/fapi/v1/exchangeInfo")
    if not info or "symbols" not in info:
        return None
    allowed = set()
    for s in info["symbols"]:
        if s.get("status") != "TRADING" or s.get("contractType") != "PERPETUAL":
            continue
        underlying = s.get("underlyingType", "COIN")
        sub_types = s.get("underlyingSubType", [])
        if underlying == "COIN" and not any(x in ("STOCK", "ETF", "EQUITY", "TRADFI", "COMMODITY") for x in sub_types):
            allowed.add(s["symbol"])
    return allowed if allowed else None

async def get_universe(session, source):
    universe = []
    if source != "BYBIT":
        allowed_set = await get_binance_crypto_set(session, source)
        tickers = await fetch_json(session, f"{source}/fapi/v1/ticker/24hr")
        premiums = await fetch_json(session, f"{source}/fapi/v1/premiumIndex")
        if isinstance(tickers, list) and isinstance(premiums, list):
            fund_map = {p["symbol"]: float(p.get("lastFundingRate", 0)) * 100 for p in premiums}
            pairs = [t for t in tickers if is_valid_crypto(t["symbol"], allowed_set)]
            pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
            top = pairs[:TOP_N_COINS]
            top_names = {x["symbol"] for x in top}
            extra = [x for x in pairs if x["symbol"] in WATCHLIST and x["symbol"] not in top_names]
            for item in top + extra:
                sym = item["symbol"]
                universe.append({"symbol": sym, "price": float(item["lastPrice"]), "funding": fund_map.get(sym, 0.0), "source": source})
            if universe:
                return universe
    data = await fetch_bybit(session, "/v5/market/tickers", {"category": "linear"})
    if data:
        pairs = [x for x in data["result"]["list"] if is_valid_crypto(x["symbol"])]
        pairs.sort(key=lambda x: float(x.get("turnover24h", 0)), reverse=True)
        top = pairs[:TOP_N_COINS]
        top_names = {x["symbol"] for x in top}
        extra = [x for x in pairs if x["symbol"] in WATCHLIST and x["symbol"] not in top_names]
        for item in top + extra:
            universe.append({"symbol": item["symbol"], "price": float(item["lastPrice"]), "funding": float(item.get("fundingRate", 0)) * 100, "source": "BYBIT"})
    return universe

async def get_market_history(session, symbol, source):
    candles, oi_series, r_long, w_long = [], [], 50.0, 50.0
    if source != "BYBIT":
        kl = await fetch_json(session, f"{source}/fapi/v1/klines", {"symbol": symbol, "interval": "1h", "limit": 48})
        oi = await fetch_json(session, f"{source}/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "limit": 24})
        gl = await fetch_json(session, f"{source}/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": "1h", "limit": 1})
        tp = await fetch_json(session, f"{source}/futures/data/topLongShortPositionRatio", {"symbol": symbol, "period": "1h", "limit": 1})
        if isinstance(kl, list) and len(kl) >= 24 and isinstance(oi, list) and len(oi) >= 6:
            for c in kl:
                _, o, h, l, cl, v, *_ = c
                candles.append((float(o), float(h), float(l), float(cl), float(v)))
            oi_series = [float(x["sumOpenInterestValue"]) for x in oi]
            if isinstance(gl, list) and gl:
                r_long = float(next(iter(gl))["longAccount"]) * 100
            if isinstance(tp, list) and tp:
                w_long = float(next(iter(tp))["longAccount"]) * 100
            return candles, oi_series, r_long, w_long
    kl_b = await fetch_bybit(session, "/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": "60", "limit": 48})
    oi_b = await fetch_bybit(session, "/v5/market/open-interest", {"category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 24})
    ac_b = await fetch_bybit(session, "/v5/market/account-ratio", {"category": "linear", "symbol": symbol, "period": "1h", "limit": 1})
    if kl_b and oi_b and len(kl_b["result"]["list"]) >= 24 and len(oi_b["result"]["list"]) >= 6:
        for c in reversed(kl_b["result"]["list"]):
            _, o, h, l, cl, v, *_ = c
            candles.append((float(o), float(h), float(l), float(cl), float(v)))
        oi_series = [float(x["openInterest"]) for x in reversed(oi_b["result"]["list"])]
        if ac_b and ac_b["result"]["list"]:
            r_long = float(next(iter(ac_b["result"]["list"]))["buyRatio"]) * 100
        tp_b = await fetch_json(session, "https://binance.com/futures/data/topLongShortPositionRatio", {"symbol": symbol, "period": "1h", "limit": 1})
        w_long = float(next(iter(tp_b))["longAccount"]) * 100 if isinstance(tp_b, list) and tp_b else 100.0 - r_long
        return candles, oi_series, r_long, w_long
    return None, None, 50.0, 50.0

async def scan_market():
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(CHAT_ID, "✅ Тест пройден! Связь с Telegram работает.")
    async with aiohttp.ClientSession() as session:
        source = await get_active_source(session)
        universe = await get_universe(session, source)
        print(f"Источник: {source} | Криптомонет к проверке: {len(universe)}")
        for item in universe:
            candles, oi_series, rl, wl = await get_market_history(session, item["symbol"], item["source"])
            if not candles or not oi_series:
                continue
            hits, p_chg, oi_chg, rl, wl = evaluate_all_9_setups(candles, oi_series, item["price"], item["funding"], rl, wl)
            if hits:
                src_label = "Binance Futures" if item["source"] != "BYBIT" else "Bybit Futures (резерв)"
                msg = [
                    f"⚡️ **СИГНАЛ: {item['symbol']} (1Ч)** | _{src_label}_",
                    f"💰 **Цена:** `{item['price']}` (`{round(p_chg, 2)}%` за 1ч)",
                    f"📈 **ОИ (1ч):** `{round(oi_chg, 2)}%` | 💸 **Фандинг:** `{round(item['funding'], 4)}%`",
                    f"👥 **Толпа (L/S):** `{round(rl, 1)}% / {round(100-rl, 1)}%` | 🐋 **Киты:** `{round(wl, 1)}% / {round(100-wl, 1)}%`",
                    "", "**Сработавшие сетапы:**"
                ] + [f"• {h}" for h in hits]
                await bot.send_message(CHAT_ID, "\n".join(msg), parse_mode="Markdown")
            await asyncio.sleep(0.25)
    await bot.session.close()
    print("Сканирование 9 сетапов завершено.")

if __name__ == "__main__":
    asyncio.run(scan_market())









