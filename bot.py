import os, json, time, asyncio, aiohttp
from aiogram import Bot
from setups import evaluate_all_9_setups, fmt_usd, calc_ema

BOT_TOKEN, CHAT_ID = os.getenv("BOT_TOKEN"), os.getenv("CHAT_ID")
RANK_START, RANK_END, MIN_VOL_USD, COOLDOWN_SEC = 20, 250, 5_000_000, 7200
CACHE_FILE = "sent_cache.json"
BLACKLIST = {"XAUUSDT", "XAGUSDT", "NVDAUSDT", "TSLAUSDT", "AAPLUSDT", "COINUSDT", "MSTRUSDT", "QQQUSDT", "SPYUSDT", "GLDUSDT", "SLVUSDT"}
BINANCE_URLS = ["https://binance.com", "https://binance.com", "https://binance.com", "https://binance.com"]
BYBIT_URLS = ["https://bybit.com", "https://bytick.com"]


def load_cache():
    try:
        return json.load(open(CACHE_FILE, encoding="utf-8")) if os.path.exists(CACHE_FILE) else {}
    except Exception:
        return {}


def save_cache(c):
    now = time.time()
    json.dump({k: v for k, v in c.items() if now - v < COOLDOWN_SEC * 2}, open(CACHE_FILE, "w", encoding="utf-8"))


def valid_sym(s, ok=None):
    return s.endswith("USDT") and "_" not in s and not s.endswith("PERP") and s not in BLACKLIST and "STOCK" not in s and (ok is None or s in ok)


async def fjson(s, url, p=None):
    try:
        async with s.get(url, params=p, headers={"User-Agent": "Mozilla/5.0"}, timeout=10) as r:
            return await r.json() if r.status == 200 else None
    except Exception:
        return None


async def get_src(s):
    for u in BINANCE_URLS:
        if await fjson(s, f"{u}/fapi/v1/ping") is not None:
            return u
    return "BYBIT"


async def fbybit(s, ep, p=None):
    for u in BYBIT_URLS:
        d = await fjson(s, f"{u}{ep}", p)
        if d and d.get("retCode") == 0:
            return d
    return None


async def get_btc(s, src):
    try:
        kl = await fjson(s, f"{src}/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "15m", "limit": 65}) if src != "BYBIT" else None
        if isinstance(kl, list) and len(kl) >= 35:
            cl = [float(x.pop(4)) for x in kl]
        else:
            kb = await fbybit(s, "/v5/market/kline", {"category": "linear", "symbol": "BTCUSDT", "interval": "15", "limit": 65})
            cl = [float(x.pop(4)) for x in reversed(kb["result"]["list"])] if kb and len(kb["result"]["list"]) >= 35 else []
        if cl:
            c, p = cl[-1], cl[-5]
            return {"price": c, "chg_1h": (c - p) / p * 100 if p else 0.0, "above_ema": c >= calc_ema(cl[-60:], 28)}
    except Exception:
        pass
    return None
async def get_uni(s, src):
    if src != "BYBIT":
        info = await fjson(s, f"{src}/fapi/v1/exchangeInfo")
        ok = {x["symbol"] for x in (info or {}).get("symbols", []) if x.get("status") == "TRADING" and x.get("contractType") == "PERPETUAL" and x.get("underlyingType", "COIN") == "COIN"} or None
        tk, pr = await asyncio.gather(fjson(s, f"{src}/fapi/v1/ticker/24hr"), fjson(s, f"{src}/fapi/v1/premiumIndex"))
        if isinstance(tk, list) and isinstance(pr, list):
            fm = {p["symbol"]: float(p.get("lastFundingRate", 0)) * 100 for p in pr}
            rk = sorted([t for t in tk if valid_sym(t["symbol"], ok) and float(t.get("quoteVolume", 0)) >= MIN_VOL_USD], key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
            uni = [{"symbol": it["symbol"], "price": float(it["lastPrice"]), "funding": fm.get(it["symbol"], 0.0), "source": src} for it in rk[RANK_START:RANK_END]]
            if uni:
                return uni
    d = await fbybit(s, "/v5/market/tickers", {"category": "linear"})
    if d:
        rk = sorted([x for x in d["result"]["list"] if valid_sym(x["symbol"]) and float(x.get("turnover24h", 0)) >= MIN_VOL_USD], key=lambda x: float(x.get("turnover24h", 0)), reverse=True)
        return [{"symbol": it["symbol"], "price": float(it["lastPrice"]), "funding": float(it.get("fundingRate", 0)) * 100, "source": "BYBIT"} for it in rk[RANK_START:RANK_END]]
    return []


async def get_hist(s, sym, src):
    if src != "BYBIT":
        kl, k15, oi, gl, tp = await asyncio.gather(
            fjson(s, f"{src}/fapi/v1/klines", {"symbol": sym, "interval": "1h", "limit": 48}),
            fjson(s, f"{src}/fapi/v1/klines", {"symbol": sym, "interval": "15m", "limit": 100}),
            fjson(s, f"{src}/futures/data/openInterestHist", {"symbol": sym, "period": "1h", "limit": 24}),
            fjson(s, f"{src}/futures/data/globalLongShortAccountRatio", {"symbol": sym, "period": "1h", "limit": 1}),
            fjson(s, f"{src}/futures/data/topLongShortPositionRatio", {"symbol": sym, "period": "1h", "limit": 1}),
        )
        if isinstance(kl, list) and len(kl) >= 24 and isinstance(oi, list) and len(oi) >= 6:
            c = [(float(o), float(h), float(l), float(cl), float(v), float(tb)) for _, o, h, l, cl, v, _, _, _, tb, *_ in kl]
            c15 = [(float(o), float(h), float(l), float(cl), float(v)) for _, o, h, l, cl, v, *_ in k15] if isinstance(k15, list) else []
            return c, c15, [float(x["sumOpenInterestValue"]) for x in oi], float(next(iter(gl))["longAccount"]) * 100 if gl else 50.0, float(next(iter(tp))["longAccount"]) * 100 if tp else 50.0
    kb, k15b, oib, acb = await asyncio.gather(
        fbybit(s, "/v5/market/kline", {"category": "linear", "symbol": sym, "interval": "60", "limit": 48}),
        fbybit(s, "/v5/market/kline", {"category": "linear", "symbol": sym, "interval": "15", "limit": 100}),
        fbybit(s, "/v5/market/open-interest", {"category": "linear", "symbol": sym, "intervalTime": "1h", "limit": 24}),
        fbybit(s, "/v5/market/account-ratio", {"category": "linear", "symbol": sym, "period": "1h", "limit": 1}),
    )
    if kb and oib and len(kb["result"]["list"]) >= 24 and len(oib["result"]["list"]) >= 6:
        c = [(float(o), float(h), float(l), float(cl), float(v), float(v) * 0.5) for _, o, h, l, cl, v, *_ in reversed(kb["result"]["list"])]
        c15 = [(float(o), float(h), float(l), float(cl), float(v)) for _, o, h, l, cl, v, *_ in reversed(k15b["result"]["list"])] if k15b else []
        rl = float(next(iter(acb["result"]["list"]))["buyRatio"]) * 100 if acb and acb["result"]["list"] else 50.0
        tpb = await fjson(s, "https://binance.com", {"symbol": sym, "period": "1h", "limit": 1})
        return c, c15, [float(x["openInterest"]) for x in reversed(oib["result"]["list"])], rl, float(next(iter(tpb))["longAccount"]) * 100 if tpb else 100.0 - rl
    return None, None, None, 50.0, 50.0
def strict_ok(hits, p_chg, oi_chg, rl, wl, up_poc, dn_poc, cvd, nd, fund, btc):
    txt = " | ".join(hits)
    if "внутри НЕ входить" in txt or "БОКОВИК (ФЛЭТ)" in txt:
        return False
    hl, hs = any("рассмотреть ЛОНГ" in h for h in hits), any("рассмотреть ШОРТ" in h for h in hits)
    if hl == hs:
        return False
    if hl:
        if "НИСХОДЯЩИЙ" in txt or "ниже пучка" in txt or oi_chg < 1.5 or wl < 58.0 or cvd <= 0 or nd <= 0 or fund >= 0.025 or p_chg >= 5.0:
            return False
        if (btc and (not btc["above_ema"] or btc["chg_1h"] <= -0.35)) or not up_poc or up_poc.pop(1) < 1.4:
            return False
        return True
    if "ВОСХОДЯЩИЙ" in txt or "выше пучка" in txt or cvd >= 0 or p_chg <= -5.0 or not dn_poc or dn_poc.pop(1) < 1.4:
        return False
    if btc and btc["above_ema"] and btc["chg_1h"] >= 0.35:
        return False
    return ((100.0 - wl) >= 58.0 and nd < 0) or (rl >= 60.0 and fund >= 0.015)


def fmt_msg(it, hits, p_chg, oi_chg, rl, wl, up_poc, dn_poc, cvd, nd, candles, eb, btc):
    hl = any("рассмотреть ЛОНГ" in h for h in hits)
    v = "🟢 <b>ИТОГ: ЧИСТЫЙ ЛОНГ</b>" if hl else "🔴 <b>ИТОГ: ЧИСТЫЙ ШОРТ</b>"
    pocs = ([f"⬆️ {up_poc.pop(0)}"] if up_poc else []) + ([f"⬇️ {dn_poc.pop(0)}"] if dn_poc else [])
    h24, l24 = max(h for _, h, _, _, _, *_ in candles[-24:]), min(l for _, _, l, _, _, *_ in candles[-24:])
    bline = f"🧭 <b>BTC:</b> {btc['price']} ({btc['chg_1h']:+.2f}%, {'выше' if btc['above_ema'] else 'ниже'} EMA28)" if btc else ""
    plan = ""
    if eb:
        e, sl = (round(eb.pop(1), 6), round(eb.pop(0) * 0.992, 6)) if hl else (round(eb.pop(0), 6), round(eb.pop(1) * 1.008, 6))
        tp1, tp2 = (round(e * 1.018, 6), round(e * 1.036, 6)) if hl else (round(e * 0.982, 6), round(e * 0.964, 6))
        plan = f"\n📐 <b>План:</b> вход <code>{e}</code> | SL <code>{sl}</code> | TP <code>{tp1}</code> → <code>{tp2}</code>"
    return "\n".join([
        f"⚡️ <b>СНАЙПЕР: {it['symbol']}</b> | {'Binance' if it['source'] != 'BYBIT' else 'Bybit'}", v, bline,
        f"💰 <b>Цена:</b> {it['price']} ({p_chg:+.2f}%) | 💸 <b>Фандинг:</b> {round(it['funding'], 4)}%",
        f"🎯 <b>POC:</b> {' | '.join(pocs)} | 📈 <b>ОИ:</b> {oi_chg:+.2f}%",
        f"💥 <b>Ликв. 25x:</b> шортов > <code>{round(l24*1.04, 6)}</code> | лонгов < <code>{round(h24*0.96, 6)}</code>",
        f"👥 <b>Толпа:</b> {round(rl,1)}% Л / {round(100-rl,1)}% Ш | 🐋 <b>Киты:</b> {round(wl,1)}% Л",
        f"📊 <b>CVD:</b> {fmt_usd(cvd)} | ⚖️ <b>Net Delta:</b> {fmt_usd(nd)}", "", "<b>Сетапы:</b>"
    ] + [f"• {h.replace('<', 'ниже ')}" for h in hits] + ([plan] if plan else []))


async def worker(sem, s, bot, it, cache, lock, st, btc):
    async with sem:
        c, c15, oi, rl, wl = await get_hist(s, it["symbol"], it["source"])
        if not c or not oi:
            return
        hits, p_chg, oi_chg, rl, wl, _, _, up_poc, dn_poc, cvd, _, nd, eb = evaluate_all_9_setups(c, oi, it["price"], it["funding"], rl, wl, c15)
        if not hits or not strict_ok(hits, p_chg, oi_chg, rl, wl, up_poc, dn_poc, cvd, nd, it["funding"], btc):
            return
        key = f"{it['symbol']}_EMA9" if any("#9" in h for h in hits) else it["symbol"]
        if time.time() - cache.get(key, 0) < COOLDOWN_SEC:
            return
        await bot.send_message(CHAT_ID, fmt_msg(it, hits, p_chg, oi_chg, rl, wl, up_poc, dn_poc, cvd, nd, c, eb, btc), parse_mode="HTML", disable_web_page_preview=True)
        async with lock:
            cache[key] = time.time()
            st["sent"] += 1


async def main():
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(CHAT_ID, "✅ Снайпер-бот 15m на связи и готов к работе!")
    cache, lock, st = load_cache(), asyncio.Lock(), {"sent": 0}
    async with aiohttp.ClientSession() as s:
        src = await get_src(s)
        btc, uni = await asyncio.gather(get_btc(s, src), get_uni(s, src))
        await asyncio.gather(*(worker(asyncio.Semaphore(10), s, bot, it, cache, lock, st, btc) for it in uni))
        print(f"Источник: {src} | Проверено: {len(uni)} | Отправлено: {st['sent']}")
    save_cache(cache)
    await bot.session.close()


asyncio.run(main())


















































