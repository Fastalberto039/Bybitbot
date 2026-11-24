import os, json, time, asyncio, aiohttp
from aiogram import Bot
from setups import evaluate_all_9_setups, fmt_usd, calc_ema

BOT_TOKEN, CHAT_ID = os.getenv("BOT_TOKEN"), os.getenv("CHAT_ID")
RANK_START, RANK_END, MIN_VOL_USD, COOLDOWN_SEC = 20, 250, 8_000_000, 7200
CACHE_FILE = "sent_cache.json"
WATCHLIST = ["APTUSDT", "ARBUSDT", "OPUSDT", "DOTUSDT", "ATOMUSDT", "ZROUSDT", "ZKUSDT", "STRKUSDT", "1000LUNCUSDT", "INITUSDT"]
STOCK_BLACKLIST = {"XAUUSDT", "XAGUSDT", "NVDAUSDT", "TSLAUSDT", "AAPLUSDT", "COINUSDT", "MSTRUSDT", "QQQUSDT", "SPYUSDT", "GLDUSDT", "SLVUSDT"}
BINANCE_URLS = ["https://binance.com", "https://binance.com", "https://binance.me"]
BYBIT_URLS = ["https://bybit.com", "https://bytick.com"]


def load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) if os.path.exists(CACHE_FILE) else {}
    except Exception:
        return {}


def save_cache(cache: dict):
    now = time.time()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in cache.items() if now - v < COOLDOWN_SEC * 2}, f)


def is_valid_crypto(symbol: str, allowed: set | None = None) -> bool:
    if not symbol.endswith("USDT") or "_" in symbol or symbol.endswith("PERP") or symbol in STOCK_BLACKLIST or "STOCK" in symbol:
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


async def get_btc_context(session, source):
    try:
        kl = await fetch_json(session, f"{source}/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "15m", "limit": 65}) if source != "BYBIT" else None
        if isinstance(kl, list) and len(kl) >= 35:
            closes = [float(x[4]) for x in kl]
        else:
            kl_b = await fetch_bybit(session, "/v5/market/kline", {"category": "linear", "symbol": "BTCUSDT", "interval": "15", "limit": 65})
            closes = [float(x[4]) for x in reversed(kl_b["result"]["list"])] if kl_b and len(kl_b["result"]["list"]) >= 35 else []
        if closes:
            curr, prev_1h = closes[-1], closes[-5]
            return {"price": curr, "chg_1h": (curr - prev_1h) / prev_1h * 100 if prev_1h else 0.0, "above_ema": curr >= calc_ema(closes[-60:], 28)}
    except Exception:
        pass
    return None


async def get_universe(session, source):
    universe, seen = [], set()
    if source != "BYBIT":
        info = await fetch_json(session, f"{source}/fapi/v1/exchangeInfo")
        allowed = {s["symbol"] for s in (info or {}).get("symbols", []) if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL" and s.get("underlyingType", "COIN") == "COIN" and not any(x in ("STOCK", "ETF", "EQUITY", "TRADFI", "COMMODITY") for x in s.get("underlyingSubType", []))} or None
        tickers, premiums = await asyncio.gather(fetch_json(session, f"{source}/fapi/v1/ticker/24hr"), fetch_json(session, f"{source}/fapi/v1/premiumIndex"))
        if isinstance(tickers, list) and isinstance(premiums, list):
            f_map = {p["symbol"]: float(p.get("lastFundingRate", 0)) * 100 for p in premiums}
            ranked = sorted([t for t in tickers if is_valid_crypto(t["symbol"], allowed) and float(t.get("quoteVolume", 0)) >= MIN_VOL_USD], key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
            skip = {x["symbol"] for x in ranked[:RANK_START]}
            for item in ranked[RANK_START:RANK_END] + [x for x in ranked if x["symbol"] in WATCHLIST and x["symbol"] not in skip]:
                if item["symbol"] not in seen:
                    seen.add(item["symbol"])
                    universe.append({"symbol": item["symbol"], "price": float(item["lastPrice"]), "funding": f_map.get(item["symbol"], 0.0), "source": source})
            if universe:
                return universe
    data = await fetch_bybit(session, "/v5/market/tickers", {"category": "linear"})
    if data:
        ranked = sorted([x for x in data["result"]["list"] if is_valid_crypto(x["symbol"]) and float(x.get("turnover24h", 0)) >= MIN_VOL_USD], key=lambda x: float(x.get("turnover24h", 0)), reverse=True)
        skip = {x["symbol"] for x in ranked[:RANK_START]}
        for item in ranked[RANK_START:RANK_END] + [x for x in ranked if x["symbol"] in WATCHLIST and x["symbol"] not in skip]:
            if item["symbol"] not in seen:
                seen.add(item["symbol"])
                universe.append({"symbol": item["symbol"], "price": float(item["lastPrice"]), "funding": float(item.get("fundingRate", 0)) * 100, "source": "BYBIT"})
    return universe
async def get_market_history(session, symbol, source):
    candles, c15, oi_s, rl, wl = [], [], [], 50.0, 50.0
    if source != "BYBIT":
        kl, kl15, oi, gl, tp = await asyncio.gather(
            fetch_json(session, f"{source}/fapi/v1/klines", {"symbol": symbol, "interval": "1h", "limit": 48}),
            fetch_json(session, f"{source}/fapi/v1/klines", {"symbol": symbol, "interval": "15m", "limit": 100}),
            fetch_json(session, f"{source}/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "limit": 24}),
            fetch_json(session, f"{source}/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": "1h", "limit": 1}),
            fetch_json(session, f"{source}/futures/data/topLongShortPositionRatio", {"symbol": symbol, "period": "1h", "limit": 1}),
        )
        if isinstance(kl, list) and len(kl) >= 24 and isinstance(oi, list) and len(oi) >= 6:
            candles = [(float(o), float(h), float(l), float(cl), float(v), float(tb)) for _, o, h, l, cl, v, _, _, _, tb, *_ in kl]
            c15 = [(float(o), float(h), float(l), float(cl), float(v)) for _, o, h, l, cl, v, *_ in kl15] if isinstance(kl15, list) else []
            oi_s = [float(x["sumOpenInterestValue"]) for x in oi]
            rl = float(gl[0]["longAccount"]) * 100 if isinstance(gl, list) and gl else 50.0
            wl = float(tp[0]["longAccount"]) * 100 if isinstance(tp, list) and tp else 50.0
            return candles, c15, oi_s, rl, wl
    kl_b, kl15_b, oi_b, ac_b = await asyncio.gather(
        fetch_bybit(session, "/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": "60", "limit": 48}),
        fetch_bybit(session, "/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": "15", "limit": 100}),
        fetch_bybit(session, "/v5/market/open-interest", {"category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 24}),
        fetch_bybit(session, "/v5/market/account-ratio", {"category": "linear", "symbol": symbol, "period": "1h", "limit": 1}),
    )
    if kl_b and oi_b and len(kl_b["result"]["list"]) >= 24 and len(oi_b["result"]["list"]) >= 6:
        candles = [(float(o), float(h), float(l), float(cl), float(v), float(v) * 0.5) for _, o, h, l, cl, v, *_ in reversed(kl_b["result"]["list"])]
        c15 = [(float(o), float(h), float(l), float(cl), float(v)) for _, o, h, l, cl, v, *_ in reversed(kl15_b["result"]["list"])] if kl15_b and kl15_b["result"]["list"] else []
        oi_s = [float(x["openInterest"]) for x in reversed(oi_b["result"]["list"])]
        rl = float(ac_b["result"]["list"][0]["buyRatio"]) * 100 if ac_b and ac_b["result"]["list"] else 50.0
        tp_b = await fetch_json(session, "https://binance.com", {"symbol": symbol, "period": "1h", "limit": 1})
        wl = float(tp_b[0]["longAccount"]) * 100 if isinstance(tp_b, list) and tp_b else 100.0 - rl
        return candles, c15, oi_s, rl, wl
    return None, None, None, 50.0, 50.0


def explain_context(p_chg, oi_chg, rl, wl, cvd_usd, nd_usd, funding):
    rs = 100.0 - rl
    crowd_txt = f"{round(rl,1)}% ЛОНГ (перегруз лонгов)" if rl >= 58.0 else (f"{round(rs,1)}% ШОРТ (топливо для сквиза)" if rs >= 58.0 else f"{round(rl,1)}% ЛОНГ / {round(rs,1)}% ШОРТ")
    whale_txt = f"{round(wl,1)}% ЛОНГ" if wl >= 54.0 else (f"{round(100-wl,1)}% ШОРТ" if wl <= 46.0 else "50/50 нейтрально")
    if p_chg > 0.3 and cvd_usd > 0:
        cvd_txt = f"{fmt_usd(cvd_usd)} → Цена↑ + CVD↑ (рост на покупках)"
    elif p_chg > 0.3 and cvd_usd < 0:
        cvd_txt = f"{fmt_usd(cvd_usd)} → ⚠️ Цена↑ + CVD↓ (скрытые продажи в рост!)"
    elif p_chg < -0.3 and cvd_usd < 0:
        cvd_txt = f"{fmt_usd(cvd_usd)} → Цена↓ + CVD↓ (продавцы давят)"
    elif p_chg < -0.3 and cvd_usd > 0:
        cvd_txt = f"{fmt_usd(cvd_usd)} → 💡 Цена↓ + CVD↑ (лимитный откуп)"
    else:
        cvd_txt = f"{fmt_usd(cvd_usd)} (нейтральная дельта)"
    nd_txt = f"{fmt_usd(nd_usd)} ({'доминируют шорты' if nd_usd < 0 else 'доминируют лонги'})"
    return crowd_txt, whale_txt, cvd_txt, nd_txt


def build_trade_plan(ema_bounds, has_l, has_s, up_poc, dn_poc):
    if not ema_bounds:
        return ""
    ema_bot, ema_top = ema_bounds
    if has_l and not has_s:
        e, sl = round(ema_top, 6), round(ema_bot * 0.992, 6)
        tp1, tp2 = round(e * 1.018, 6), (up_poc[0] if up_poc else round(e * 1.036, 6))
        return f"\n📐 <b>План (ЛОНГ по узлу):</b>\n• Вход/ретест: <code>{e}</code> | SL: <code>{sl}</code> (-{round((e-sl)/e*100,2)}%)\n• TP (50/50): <code>{tp1}</code> → <code>{tp2}</code>"
    if has_s and not has_l:
        e, sl = round(ema_bot, 6), round(ema_top * 1.008, 6)
        tp1, tp2 = round(e * 0.982, 6), (dn_poc[0] if dn_poc else round(e * 0.964, 6))
        return f"\n📐 <b>План (ШОРТ по узлу):</b>\n• Вход/ретест: <code>{e}</code> | SL: <code>{sl}</code> (-{round((sl-e)/e*100,2)}%)\n• TP (50/50): <code>{tp1}</code> → <code>{tp2}</code>"
    return ""


def format_alert(item, hits, p_chg, oi_chg, rl, wl, up_poc, dn_poc, cvd_usd, nd_usd, candles, ema_bounds, btc_ctx):
    src = "Binance Futures" if item["source"] != "BYBIT" else "Bybit Futures"
    has_l, has_s = any("рассмотреть ЛОНГ" in h for h in hits), any("рассмотреть ШОРТ" in h for h in hits)
    verdict = "🟢 <b>ИТОГ: Лучше рассмотреть ЛОНГ</b>" if (has_l and not has_s) else ("🔴 <b>ИТОГ: Лучше рассмотреть ШОРТ</b>" if (has_s and not has_l) else "🪢 <b>ИТОГ: Снайперский сетап</b>")
    pocs = ([f"⬆️ {up_poc[0]} (+{up_poc[1]}%)"] if up_poc else []) + ([f"⬇️ {dn_poc[0]} (-{dn_poc[1]}%)"] if dn_poc else [])
    oi_sign = "+" if oi_chg > 0 else ""
    h24, l24 = max(h for _, h, _, _, _, *_ in candles[-24:]), min(l for _, _, l, _, _, *_ in candles[-24:])
    crowd_txt, whale_txt, cvd_txt, nd_txt = explain_context(p_chg, oi_chg, rl, wl, cvd_usd, nd_usd, item["funding"])
    btc_line = ""
    if btc_ctx:
        bs = "+" if btc_ctx["chg_1h"] > 0 else ""
        warn = (has_l and not has_s and (btc_ctx["chg_1h"] <= -0.5 or not btc_ctx["above_ema"])) or (has_s and not has_l and btc_ctx["chg_1h"] >= 0.5 and btc_ctx["above_ema"])
        ico = "⚠️" if warn else "🧭"
        btc_line = f"{ico} <b>Поводырь BTC:</b> {btc_ctx['price']} ({bs}{round(btc_ctx['chg_1h'], 2)}% за 1ч, {'выше' if btc_ctx['above_ema'] else 'ниже'} 15m EMA28)"
    plan_txt = build_trade_plan(ema_bounds, has_l, has_s, up_poc, dn_poc)
    lines = [
        f"⚡️ <b>СИГНАЛ: {item['symbol']} (15m/1Ч | Топ 20-250)</b> | {src}", verdict,
    ] + ([btc_line] if btc_line else []) + [
        f"💰 <b>Цена:</b> {item['price']} ({round(p_chg, 2)}% за 1ч) | 💸 <b>Фандинг:</b> {round(item['funding'], 4)}%",
        f"🎯 <b>Цели POC:</b> {' | '.join(pocs) if pocs else 'нет близких'} | 📈 <b>ОИ:</b> {oi_sign}{round(oi_chg, 2)}%",
        f"💥 <b>Ликвидации (25x):</b> шортов > <code>{round(l24*1.04, 6)}</code> | лонгов < <code>{round(h24*0.96, 6)}</code>",
        f"👥 <b>Толпа:</b> {crowd_txt} | 🐋 <b>Киты:</b> {whale_txt}",
        f"📊 <b>CVD (4ч):</b> {cvd_txt} | ⚖️ <b>Net Delta:</b> {nd_txt}",
        "", "<b>Сработавшие сетапы:</b>"
    ] + [f"• {h.replace('<', 'ниже ')}" for h in hits] + ([plan_txt] if plan_txt else []) + [
        "", f"🔎 <a href='https://coinglass.com_{item['symbol']}'>Открыть {item['symbol']} на CoinGlass</a>"
    ]
    return "\n".join(lines)


async def process_symbol(sem, session, bot, item, cache, lock, stats, btc_ctx):
    async with sem:
        sym = item["symbol"]
        candles, c15, oi_s, rl, wl = await get_market_history(session, sym, item["source"])
        if not candles or not oi_s:
            return
        hits, p_chg, oi_chg, rl, wl, _, _, up_poc, dn_poc, cvd_usd, _, nd_usd, ema_bounds = evaluate_all_9_setups(candles, oi_s, item["price"], item["funding"], rl, wl, c15)
        if not hits:
            return
        is_ema9 = any("#9" in h for h in hits)
        if is_ema9:
            stats["ema9"] += 1
        cache_key = f"{sym}_EMA9" if is_ema9 else sym
        if time.time() - cache.get(cache_key, 0) < COOLDOWN_SEC:
            return
        await bot.send_message(CHAT_ID, format_alert(item, hits, p_chg, oi_chg, rl, wl, up_poc, dn_poc, cvd_usd, nd_usd, candles, ema_bounds, btc_ctx), parse_mode="HTML", disable_web_page_preview=True)
        async with lock:
            cache[cache_key] = time.time()
            stats["sent"] += 1


async def scan_market():
    bot = Bot(token=BOT_TOKEN)
    cache, lock, stats = load_cache(), asyncio.Lock(), {"ema9": 0, "sent": 0}
    async with aiohttp.ClientSession() as session:
        source = await get_active_source(session)
        btc_ctx, universe = await asyncio.gather(get_btc_context(session, source), get_universe(session, source))
        await asyncio.gather(*(process_symbol(asyncio.Semaphore(10), session, bot, item, cache, lock, stats, btc_ctx) for item in universe))
        print(f"Источник: {source} | Монет (20-250): {len(universe)} | Найдено #9: {stats['ema9']} | Отправлено: {stats['sent']}")
    save_cache(cache)
    await bot.session.close()


asyncio.run(scan_market())























