import os
import asyncio
from datetime import datetime, timezone
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
BINANCE_URLS = ["https://binance.com", "https://binance.com", "https://binance.me"]
BYBIT_URLS = ["https://bybit.com", "https://bytick.com"]


def is_valid_crypto(symbol: str, allowed: set | None = None) -> bool:
    if not symbol.endswith("USDT") or "_" in symbol or symbol.endswith("PERP"):
        return False
    if symbol in STOCK_BLACKLIST or "STOCK" in symbol:
        return False
    return allowed is None or symbol in allowed


async def fetch_json(session, url, params=None):
    try:
        async with session.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=10) as r:
            return await r.json() if r.status == 200 else None
    except Exception:
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


async def get_universe(session, source):
    universe = []
    if source != "BYBIT":
        info = await fetch_json(session, f"{source}/fapi/v1/exchangeInfo")
        allowed = {
            s["symbol"] for s in (info or {}).get("symbols", [])
            if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL"
            and s.get("underlyingType", "COIN") == "COIN"
            and not any(x in ("STOCK", "ETF", "EQUITY", "TRADFI", "COMMODITY") for x in s.get("underlyingSubType", []))
        } or None
        tickers = await fetch_json(session, f"{source}/fapi/v1/ticker/24hr")
        premiums = await fetch_json(session, f"{source}/fapi/v1/premiumIndex")
        if isinstance(tickers, list) and isinstance(premiums, list):
            f_map = {p["symbol"]: float(p.get("lastFundingRate", 0)) * 100 for p in premiums}
            pairs = sorted([t for t in tickers if is_valid_crypto(t["symbol"], allowed)], key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
            top = pairs[:TOP_N_COINS]
            names = {x["symbol"] for x in top}
            for item in top + [x for x in pairs if x["symbol"] in WATCHLIST and x["symbol"] not in names]:
                sym = item["symbol"]
                universe.append({"symbol": sym, "price": float(item["lastPrice"]), "funding": f_map.get(sym, 0.0), "source": source})
            if universe:
                return universe
    data = await fetch_bybit(session, "/v5/market/tickers", {"category": "linear"})
    if data:
        pairs = sorted([x for x in data["result"]["list"] if is_valid_crypto(x["symbol"])], key=lambda x: float(x.get("turnover24h", 0)), reverse=True)
        top = pairs[:TOP_N_COINS]
        names = {x["symbol"] for x in top}
        for item in top + [x for x in pairs if x["symbol"] in WATCHLIST and x["symbol"] not in names]:
            universe.append({"symbol": item["symbol"], "price": float(item["lastPrice"]), "funding": float(item.get("fundingRate", 0)) * 100, "source": "BYBIT"})
    return universe


async def get_market_history(session, symbol, source):
    candles, c15, oi_s, rl, wl = [], [], [], 50.0, 50.0
    now = datetime.now(timezone.utc)
    start_ms = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() * 1000)
    if source != "BYBIT":
        kl = await fetch_json(session, f"{source}/fapi/v1/klines", {"symbol": symbol, "interval": "1h", "limit": 48})
        kl15 = await fetch_json(session, f"{source}/fapi/v1/klines", {"symbol": symbol, "interval": "15m", "startTime": start_ms, "limit": 100})
        oi = await fetch_json(session, f"{source}/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "limit": 24})
        gl = await fetch_json(session, f"{source}/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": "1h", "limit": 1})
        tp = await fetch_json(session, f"{source}/futures/data/topLongShortPositionRatio", {"symbol": symbol, "period": "1h", "limit": 1})
        if isinstance(kl, list) and len(kl) >= 24 and isinstance(oi, list) and len(oi) >= 6:
            candles = [(float(o), float(h), float(l), float(cl), float(v), float(tb)) for _, o, h, l, cl, v, _, _, _, tb, *_ in kl]
            if isinstance(kl15, list):
                c15 = [(float(o), float(h), float(l), float(cl), float(v)) for _, o, h, l, cl, v, *_ in kl15]
            oi_s = [float(x["sumOpenInterestValue"]) for x in oi]
            if isinstance(gl, list) and gl:
                rl = float(next(iter(gl))["longAccount"]) * 100
            if isinstance(tp, list) and tp:
                wl = float(next(iter(tp))["longAccount"]) * 100
            return candles, c15, oi_s, rl, wl
    kl_b = await fetch_bybit(session, "/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": "60", "limit": 48})
    kl15_b = await fetch_bybit(session, "/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": "15", "start": start_ms, "limit": 100})
    oi_b = await fetch_bybit(session, "/v5/market/open-interest", {"category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 24})
    ac_b = await fetch_bybit(session, "/v5/market/account-ratio", {"category": "linear", "symbol": symbol, "period": "1h", "limit": 1})
    if kl_b and oi_b and len(kl_b["result"]["list"]) >= 24 and len(oi_b["result"]["list"]) >= 6:
        candles = [(float(o), float(h), float(l), float(cl), float(v), float(v) * 0.5) for _, o, h, l, cl, v, *_ in reversed(kl_b["result"]["list"])]
        if kl15_b and kl15_b["result"]["list"]:
            c15 = [(float(o), float(h), float(l), float(cl), float(v)) for _, o, h, l, cl, v, *_ in reversed(kl15_b["result"]["list"])]
        oi_s = [float(x["openInterest"]) for x in reversed(oi_b["result"]["list"])]
        if ac_b and ac_b["result"]["list"]:
            rl = float(next(iter(ac_b["result"]["list"]))["buyRatio"]) * 100
        tp_b = await fetch_json(session, "https://binance.com/futures/data/topLongShortPositionRatio", {"symbol": symbol, "period": "1h", "limit": 1})
        wl = float(next(iter(tp_b))["longAccount"]) * 100 if isinstance(tp_b, list) and tp_b else 100.0 - rl
        return candles, c15, oi_s, rl, wl
    return None, None, None, 50.0, 50.0


def format_alert(item, hits, p_chg, oi_chg, rl, wl, vwap_val, vwap_diff, up_poc, dn_poc):
    src = "Binance Futures" if item["source"] != "BYBIT" else "Bybit Futures"
    has_l, has_s = any("ЛОНГ" in h for h in hits), any("ШОРТ" in h for h in hits)
    verdict = "🟢 <b>ИТОГ: Лучше рассмотреть ЛОНГ</b>" if (has_l and not has_s) else ("🔴 <b>ИТОГ: Лучше рассмотреть ШОРТ</b>" if (has_s and not has_l) else "⚖️ <b>ИТОГ: Противоречие</b>")
    heat = "🔥 сильная перегретость" if vwap_diff >= 3.0 else ("⚠️ перегретость" if vwap_diff >= 1.5 else ("❄️ сильная перепроданность" if vwap_diff <= -3.0 else ("⚠️ перепроданность" if vwap_diff <= -1.5 else "у VWAP")))
    pocs = ([f"⬆️ {up_poc[:1]} (+{up_poc[1:]}%)"] if up_poc else []) + ([f"⬇️ {dn_poc[:1]} (-{dn_poc[1:]}%)"] if dn_poc else [])
    sign = "+" if vwap_diff > 0 else ""
    lines = [
        f"⚡️ <b>СИГНАЛ: {item['symbol']} (1Ч)</b> | {src}", verdict,
        f"💰 <b>Цена:</b> {item['price']} ({round(p_chg, 2)}% за 1ч)",
        f"⚖️ <b>VWAP:</b> {vwap_val} ({sign}{vwap_diff}% — {heat})",
        f"🎯 <b>Цели POC:</b> {' | '.join(pocs) if pocs else 'нет близких'}",
        f"📈 <b>ОИ:</b> {round(oi_chg, 2)}% | 💸 <b>Фандинг:</b> {round(item['funding'], 4)}%",
        f"👥 <b>Толпа:</b> {round(rl, 1)}%/{round(100-rl, 1)}% | 🐋 <b>Киты:</b> {round(wl, 1)}%/{round(100-wl, 1)}%",
        "", "<b>Сработавшие сетапы:</b>"
    ] + [f"• {h}" for h in hits]
    return "\n".join(lines)


async def scan_market():
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(CHAT_ID, "✅ Тест пройден! Связь с Telegram работает.")
    async with aiohttp.ClientSession() as session:
        source = await get_active_source(session)
        universe = await get_universe(session, source)
        print(f"Источник: {source} | Монет: {len(universe)}")
        for item in universe:
            candles, c15, oi_s, rl, wl = await get_market_history(session, item["symbol"], item["source"])
            if not candles or not oi_s:
                continue
            hits, p_chg, oi_chg, rl, wl, vwap_val, vwap_diff, up_poc, dn_poc = evaluate_all_9_setups(candles, oi_s, item["price"], item["funding"], rl, wl, c15)
            if hits:
                await bot.send_message(CHAT_ID, format_alert(item, hits, p_chg, oi_chg, rl, wl, vwap_val, vwap_diff, up_poc, dn_poc), parse_mode="HTML")
            await asyncio.sleep(0.25)
    await bot.session.close()


asyncio.run(scan_market())















