import math


def fmt_usd(val: float) -> str:
    sign = "+" if val > 0 else ("-" if val < 0 else "")
    v = abs(val)
    if v >= 1_000_000_000:
        return f"{sign}{round(v / 1_000_000_000, 2)}B$"
    if v >= 1_000_000:
        return f"{sign}{round(v / 1_000_000, 2)}M$"
    if v >= 1_000:
        return f"{sign}{round(v / 1_000, 1)}K$"
    return f"{sign}{round(v, 0)}$"


def calc_ema(values: list, period: int) -> float:
    if not values:
        return 0.0
    k = 2.0 / (period + 1)
    ema = float(values[0])
    for v in values[1:]:
        ema = float(v) * k + ema * (1.0 - k)
    return ema


def detect_15m_trendline(candles_15m: list, curr_price: float):
    if not candles_15m or len(candles_15m) < 25:
        return None, 0.0
    data = candles_15m[-37:-1]
    n = len(data)
    highs = [float(x[1]) for x in data]
    lows = [float(x[2]) for x in data]
    closes = [float(x[3]) for x in data]
    sh, sl = [], []
    for i in range(2, n - 2):
        if highs[i] > highs[i-1] and highs[i] >= highs[i-2] and highs[i] > highs[i+1] and highs[i] >= highs[i+2]:
            sh.append((i, highs[i]))
        if lows[i] < lows[i-1] and lows[i] <= lows[i-2] and lows[i] < lows[i+1] and lows[i] <= lows[i+2]:
            sl.append((i, lows[i]))
    if len(sh) >= 2:
        (i1, p1), (i2, p2) = sh[-2], sh[-1]
        if i2 - i1 >= 4 and p2 < p1:
            slope = (p2 - p1) / (i2 - i1)
            lvl = p2 + slope * (n - i2)
            if all(closes[k] <= (p2 + slope * (k - i2)) * 1.0025 for k in range(i2 + 1, n - 1)):
                if lvl > 0 and 0 < (curr_price - lvl) / lvl * 100 <= 1.2:
                    return "LONG", lvl
    if len(sl) >= 2:
        (i1, p1), (i2, p2) = sl[-2], sl[-1]
        if i2 - i1 >= 4 and p2 > p1:
            slope = (p2 - p1) / (i2 - i1)
            lvl = p2 + slope * (n - i2)
            if all(closes[k] >= (p2 + slope * (k - i2)) * 0.9975 for k in range(i2 + 1, n - 1)):
                if lvl > 0 and 0 < (lvl - curr_price) / lvl * 100 <= 1.2:
                    return "SHORT", lvl
    return None, 0.0
def analyze_setup_13(c15: list, c5: list, price: float, oi_1h: float, cvd: float, rl: float):
    if not c5 or len(c5) < 8 or oi_1h < 0.6:
        return None, None
    side, lvl = detect_15m_trendline(c15, price)
    if not side:
        return None, None
    o5, _, _, cl5, v5 = [float(x) for x in c5[-2][:5]]
    ma5 = sum(float(x[4]) for x in c5[-7:-2]) / 5.0
    if ma5 <= 0 or (v5 / ma5) < 1.6:
        return None, None
    mult, rs = round(v5 / ma5, 1), 100.0 - rl
    if side == "LONG" and cl5 > o5 and cvd >= 50_000 and rs >= 55.0:
        return f"⚡ #13 ПРОБОЙ 15m НАКЛОННОЙ ({round(lvl, 6)}) + 5m объём {mult}x MA5 — лучше рассмотреть ЛОНГ (ОИ +{round(oi_1h, 2)}%, CVD {fmt_usd(cvd)}, Толпа {round(rs, 1)}% ШОРТ)", (lvl * 0.996, lvl)
    if side == "SHORT" and cl5 < o5 and cvd <= -50_000 and rl >= 55.0:
        return f"⚡ #13 ПРОБОЙ 15m НАКЛОННОЙ ({round(lvl, 6)}) + 5m объём {mult}x MA5 — лучше рассмотреть ШОРТ (ОИ +{round(oi_1h, 2)}%, CVD {fmt_usd(cvd)}, Толпа {round(rl, 1)}% ЛОНГ)", (lvl, lvl * 1.004)
    return None, None


def analyze_15m_ema(candles_15m: list, curr_price: float, oi_chg_1h: float, regime: str, cvd_4h_usd: float, retail_long: float):
    if not candles_15m or len(candles_15m) < 32:
        return "", None, None
    closes = [float(cl) for _, _, _, cl, *_ in candles_15m]
    vols = [float(v) for _, _, _, _, v, *_ in candles_15m]
    ema7, ema14, ema28 = calc_ema(closes[-35:], 7), calc_ema(closes[-45:], 14), calc_ema(closes[-60:], 28)
    avg_vol = sum(vols[-6:-1]) / 5.0 if len(vols) >= 6 else vols[-1]
    last_vol = vols[-1]
    ema_top, ema_bot = max(ema7, ema14, ema28), min(ema7, ema14, ema28)
    spread_now = (ema_top - ema_bot) / ema_bot * 100 if ema_bot > 0 else 99.0
    dist_from_28 = (curr_price - ema28) / ema28 * 100 if ema28 > 0 else 0.0
    zone_l, ema_bounds = f"{round(ema_bot, 6)}–{round(ema_top, 6)}", (ema_bot, ema_top)
    oi_sign, retail_short, signal = ("+" if oi_chg_1h > 0 else ""), 100.0 - retail_long, None

    if regime == "REV_UP" and ema7 > ema14 > ema28 and abs(curr_price - ema14) / ema14 * 100 <= 0.45 and oi_chg_1h >= 1.2 and cvd_4h_usd >= 120_000:
        signal = f"🔄 #12 СЛОМ ТРЕНДА ВВЕРХ (пробой 1Ч EMA28 + ретест 15m EMA14 {round(ema14, 6)}) — лучше рассмотреть ЛОНГ (ОИ +{round(oi_chg_1h, 2)}%, CVD {fmt_usd(cvd_4h_usd)})"
    elif regime == "REV_DOWN" and ema7 < ema14 < ema28 and abs(curr_price - ema14) / ema14 * 100 <= 0.45 and oi_chg_1h >= 1.2 and cvd_4h_usd <= -120_000:
        signal = f"🔄 #12 СЛОМ ТРЕНДА ВНИЗ (пробой 1Ч EMA28 + ретест 15m EMA14 {round(ema14, 6)}) — лучше рассмотреть ШОРТ (ОИ +{round(oi_chg_1h, 2)}%, CVD {fmt_usd(cvd_4h_usd)})"
    elif regime == "UP" and ema7 > ema14 > ema28 and abs(curr_price - ema14) / ema14 * 100 <= 0.30 and last_vol < avg_vol and oi_chg_1h >= 0.7 and cvd_4h_usd >= 70_000 and retail_short >= 55.0:
        signal = f"🎯 #11 ОТКАТ ПО ТРЕНДУ ВВЕРХ на 15m EMA14 ({round(ema14, 6)}) — лучше рассмотреть ЛОНГ (ОИ +{round(oi_chg_1h, 2)}%, CVD {fmt_usd(cvd_4h_usd)}, Толпа {round(retail_short,1)}% ШОРТ)"
    elif regime == "DOWN" and ema7 < ema14 < ema28 and abs(curr_price - ema14) / ema14 * 100 <= 0.30 and last_vol < avg_vol and oi_chg_1h >= 0.7 and cvd_4h_usd <= -70_000 and retail_long >= 55.0:
        signal = f"🎯 #11 ОТКАТ ПО ТРЕНДУ ВНИЗ под 15m EMA14 ({round(ema14, 6)}) — лучше рассмотреть ШОРТ (ОИ +{round(oi_chg_1h, 2)}%, CVD {fmt_usd(cvd_4h_usd)}, Толпа {round(retail_long,1)}% ЛОНГ)"

    if not signal and spread_now <= 0.55 and oi_chg_1h >= 0.5:
        signal = f"🪢 #9 ТУГОЕ ПЕРЕПЛЕТЕНИЕ EMA7/14/28 (ширина {round(spread_now, 2)}%, ОИ {oi_sign}{round(oi_chg_1h, 2)}%) — внутри НЕ входить! Ждать закрытие 15m свечи на объёме: ЛОНГ выше {round(ema_top * 1.002, 6)} | ШОРТ ниже {round(ema_bot * 0.998, 6)}"

    note = f"🚀 15m выше пучка EMA ({zone_l}, +{round(dist_from_28, 2)}%)" if curr_price > ema_top else (f"🔻 15m ниже пучка EMA ({zone_l}, {round(dist_from_28, 2)}%)" if curr_price < ema_bot else f"🔄 15m внутри пучка EMA ({zone_l})")
    return note, signal, ema_bounds


def calculate_vwap_bands(candles_15m: list, curr_price: float):
    return curr_price, 0.0, 0.0, False, False


def calculate_poc_levels(candles: list, curr_price: float):
    above_poc, below_poc, best_up_vol, best_dn_vol = None, None, 0.0, 0.0
    for o, h, l, c, v, *_ in candles[:-2]:
        tp = (h + l + c) / 3.0
        dist_pct = abs(tp - curr_price) / curr_price * 100 if curr_price else 0
        if 1.2 <= dist_pct <= 7.0:
            if tp > curr_price and v > best_up_vol:
                best_up_vol, above_poc = v, (round(tp, 6), round(dist_pct, 2))
            elif tp < curr_price and v > best_dn_vol:
                best_dn_vol, below_poc = v, (round(tp, 6), round(dist_pct, 2))
    return above_poc, below_poc


def evaluate_all_9_setups(candles, oi_series, price, funding, retail_long, whale_long, candles_15m=None, candles_5m=None):
    hits = []
    if abs(funding) >= 0.08 or len(candles) < 36:
        return hits, 0.0, 0.0, retail_long, whale_long, price, 0.0, None, None, 0.0, 0.0, 0.0, None

    closes_1h = [float(cl) for _, _, _, cl, *_ in candles]
    ema28_1h, ema48_1h = calc_ema(closes_1h[-40:], 28), calc_ema(closes_1h, 48)
    ema_gap_pct = abs(ema28_1h - ema48_1h) / ema48_1h * 100 if ema48_1h > 0 else 0.0

    if ema_gap_pct < 0.45:
        regime, regime_label = "FLAT", "📦 Режим рынка: БОКОВИК (ФЛЭТ)"
    elif price > ema28_1h and ema28_1h > ema48_1h:
        regime, regime_label = "UP", "📈 Режим рынка: ВОСХОДЯЩИЙ ТРЕНД"
    elif price < ema28_1h and ema28_1h < ema48_1h:
        regime, regime_label = "DOWN", "📉 Режим рынка: НИСХОДЯЩИЙ ТРЕНД"
    elif price > ema28_1h and ema28_1h <= ema48_1h:
        regime, regime_label = "REV_UP", "🔄 Режим рынка: СЛОМ ТРЕНДА ВВЕРХ"
    else:
        regime, regime_label = "REV_DOWN", "🔄 Режим рынка: СЛОМ ТРЕНДА ВНИЗ"

    o1, o3 = float(candles[-1][0]), float(candles[-3][0])
    price_chg_1h = (price - o1) / o1 * 100 if o1 else 0
    price_chg_3h = (price - o3) / o3 * 100 if o3 else 0

    cvd_4h_usd, vol_4h_usd = 0.0, 0.0
    for item_c in candles[-4:]:
        _, _, _, cl_i, vol_i = item_c[:5]
        tb_i = float(item_c[-1]) if len(item_c) >= 6 else float(vol_i) * 0.5
        cvd_4h_usd += ((2.0 * tb_i) - float(vol_i)) * float(cl_i)
        vol_4h_usd += float(vol_i) * float(cl_i)
    cvd_pct = (cvd_4h_usd / vol_4h_usd * 100) if vol_4h_usd > 0 else 0.0

    oi_curr, oi_1h_ago = oi_series[-1], oi_series[-2]
    oi_3h_ago = oi_series[-4] if len(oi_series) >= 4 else oi_1h_ago
    oi_chg_1h = (oi_curr - oi_1h_ago) / oi_1h_ago * 100 if oi_1h_ago else 0
    oi_chg_3h = (oi_curr - oi_3h_ago) / oi_3h_ago * 100 if oi_3h_ago else 0
    oi_usd = oi_curr if oi_curr > 100_000 else oi_curr * price
    nd_usd = oi_usd * ((whale_long - (100.0 - whale_long)) / 100.0)

    up_poc, dn_poc = calculate_poc_levels(candles, price)
    ema_15m_note, main_signal, ema_bounds = analyze_15m_ema(candles_15m or [], price, oi_chg_1h, regime, cvd_4h_usd, retail_long)
    sig_13, b13 = analyze_setup_13(candles_15m or [], candles_5m or [], price, oi_chg_1h, cvd_4h_usd, retail_long)

    if sig_13:
        hits.extend([regime_label, sig_13])
        ema_bounds = b13 or ema_bounds
    if main_signal:
        if not hits:
            hits.append(regime_label)
        hits.append(main_signal)

    last_4 = candles[-4:]
    high_4h, low_4h = max(float(h) for _, h, _, _, *_ in last_4), min(float(l) for _, _, l, _, *_ in last_4)
    range_4h_pct = (high_4h - low_4h) / low_4h * 100 if low_4h > 0 else 99.0
    if range_4h_pct <= 2.8 and abs(price_chg_3h) <= 1.5 and (oi_chg_1h >= 1.3 or oi_chg_3h >= 2.8):
        if regime == "UP" and cvd_4h_usd >= 70_000:
            if not hits:
                hits.append(regime_label)
            hits.append(f"🟢 #10 Бычий флаг по тренду (коридор {round(low_4h,6)}–{round(high_4h,6)}, ОИ +{round(oi_chg_1h,2)}%, CVD {fmt_usd(cvd_4h_usd)}) — лучше рассмотреть ЛОНГ")
        elif regime == "DOWN" and cvd_4h_usd <= -70_000:
            if not hits:
                hits.append(regime_label)
            hits.append(f"🔴 #10 Медвежий флаг по тренду (коридор {round(low_4h,6)}–{round(high_4h,6)}, ОИ +{round(oi_chg_1h,2)}%, CVD {fmt_usd(cvd_4h_usd)}) — лучше рассмотреть ШОРТ")

    if hits and ema_15m_note:
        hits.append(ema_15m_note)
    return hits, price_chg_1h, oi_chg_1h, retail_long, whale_long, price, 0.0, up_poc, dn_poc, cvd_4h_usd, cvd_pct, nd_usd, ema_bounds



























