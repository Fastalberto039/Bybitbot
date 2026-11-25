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


def analyze_15m_ema(candles_15m: list, curr_price: float, oi_chg_1h: float, regime: str, cvd_4h_usd: float, retail_long: float):
    if not candles_15m or len(candles_15m) < 32:
        return "", None, None
    closes = [float(cl) for _, _, _, cl, *_ in candles_15m]
    vols = [float(v) for _, _, _, _, v, *_ in candles_15m]

    ema7 = calc_ema(closes[-35:], 7)
    ema14 = calc_ema(closes[-45:], 14)
    ema28 = calc_ema(closes[-60:], 28)

    avg_vol = sum(vols[-6:-1]) / 5.0 if len(vols) >= 6 else vols[-1]
    last_vol = vols[-1]

    ema_top = max(ema7, ema14, ema28)
    ema_bot = min(ema7, ema14, ema28)
    spread_now = (ema_top - ema_bot) / ema_bot * 100 if ema_bot > 0 else 99.0
    dist_from_28 = (curr_price - ema28) / ema28 * 100 if ema28 > 0 else 0.0
    zone_l = f"{round(ema_bot, 6)}–{round(ema_top, 6)}"
    ema_bounds = (ema_bot, ema_top)
    oi_sign = "+" if oi_chg_1h > 0 else ""
    retail_short = 100.0 - retail_long

    signal = None

    # 1. РЕЖИМ: СЛОМ ТРЕНДА (#12) — самый ранний разворот на реальных деньгах
    if regime == "REV_UP" and ema7 > ema14 > ema28 and abs(curr_price - ema14) / ema14 * 100 <= 0.4 and oi_chg_1h >= 1.5 and cvd_4h_usd >= 150_000:
        signal = f"🔄 #12 СЛОМ ТРЕНДА ВВЕРХ (пробой 1Ч EMA28 + ретест 15m EMA14 {round(ema14, 6)}) — лучше рассмотреть ЛОНГ (ОИ +{round(oi_chg_1h, 2)}%, CVD {fmt_usd(cvd_4h_usd)})"
    elif regime == "REV_DOWN" and ema7 < ema14 < ema28 and abs(curr_price - ema14) / ema14 * 100 <= 0.4 and oi_chg_1h >= 1.5 and cvd_4h_usd <= -150_000:
        signal = f"🔄 #12 СЛОМ ТРЕНДА ВНИЗ (пробой 1Ч EMA28 + ретест 15m EMA14 {round(ema14, 6)}) — лучше рассмотреть ШОРТ (ОИ +{round(oi_chg_1h, 2)}%, CVD {fmt_usd(cvd_4h_usd)})"

    # 2. РЕЖИМ: ВОСХОДЯЩИЙ ТРЕНД — только откаты к EMA14 с притоком ОИ >= +0.8%, CVD >= +80K$ и шортами толпы >= 55%
    elif regime == "UP":
        if ema7 > ema14 > ema28 and abs(curr_price - ema14) / ema14 * 100 <= 0.25 and last_vol < avg_vol and oi_chg_1h >= 0.8 and cvd_4h_usd >= 80_000 and retail_short >= 55.0:
            signal = f"🎯 #11 ОТКАТ ПО ТРЕНДУ ВВЕРХ на 15m EMA14 ({round(ema14, 6)}) — лучше рассмотреть ЛОНГ (ОИ +{round(oi_chg_1h, 2)}%, CVD {fmt_usd(cvd_4h_usd)}, Толпа {round(retail_short,1)}% ШОРТ)"

    # 3. РЕЖИМ: НИСХОДЯЩИЙ ТРЕНД — только откаты под EMA14 с притоком ОИ >= +0.8%, CVD <= -80K$ и лонгами толпы >= 55%
    elif regime == "DOWN":
        if ema7 < ema14 < ema28 and abs(curr_price - ema14) / ema14 * 100 <= 0.25 and last_vol < avg_vol and oi_chg_1h >= 0.8 and cvd_4h_usd <= -80_000 and retail_long >= 55.0:
            signal = f"🎯 #11 ОТКАТ ПО ТРЕНДУ ВНИЗ под 15m EMA14 ({round(ema14, 6)}) — лучше рассмотреть ШОРТ (ОИ +{round(oi_chg_1h, 2)}%, CVD {fmt_usd(cvd_4h_usd)}, Толпа {round(retail_long,1)}% ЛОНГ)"

    # 4. РЕЖИМ: БОКОВИК (ФЛЭТ) — только сверхсжатие EMA <= 0.55% с набором ОИ >= +1.5% против толпы >= 60%
    elif regime == "FLAT" and spread_now <= 0.55 and oi_chg_1h >= 1.5:
        if retail_short >= 60.0 and cvd_4h_usd >= 60_000 and curr_price >= ema_bot:
            signal = f"📦 #9 СЖАТИЕ ВО ФЛЭТЕ → ЛОНГ (зона {zone_l}, ОИ +{round(oi_chg_1h, 2)}%, CVD {fmt_usd(cvd_4h_usd)}, Толпа {round(retail_short,1)}% ШОРТ)"
        elif retail_long >= 60.0 and cvd_4h_usd <= -60_000 and curr_price <= ema_top:
            signal = f"📦 #9 СЖАТИЕ ВО ФЛЭТЕ → ШОРТ (зона {zone_l}, ОИ +{round(oi_chg_1h, 2)}%, CVD {fmt_usd(cvd_4h_usd)}, Толпа {round(retail_long,1)}% ЛОНГ)"

    if curr_price > ema_top:
        note = f"🚀 15m выше пучка EMA ({zone_l}, +{round(dist_from_28, 2)}%)"
    elif curr_price < ema_bot:
        note = f"🔻 15m ниже пучка EMA ({zone_l}, {round(dist_from_28, 2)}%)"
    else:
        note = f"🔄 15m внутри пучка EMA ({zone_l})"

    return note, signal, ema_bounds


def calculate_vwap_bands(candles_15m: list, curr_price: float):
    return curr_price, 0.0, 0.0, False, False


def calculate_poc_levels(candles: list, curr_price: float):
    above_poc, below_poc = None, None
    best_up_vol, best_dn_vol = 0.0, 0.0
    for o, h, l, c, v, *_ in candles[:-2]:
        tp = (h + l + c) / 3.0
        dist_pct = abs(tp - curr_price) / curr_price * 100 if curr_price else 0
        if 1.2 <= dist_pct <= 7.0:
            if tp > curr_price and v > best_up_vol:
                best_up_vol, above_poc = v, (round(tp, 6), round(dist_pct, 2))
            elif tp < curr_price and v > best_dn_vol:
                best_dn_vol, below_poc = v, (round(tp, 6), round(dist_pct, 2))
    return above_poc, below_poc


def evaluate_all_9_setups(candles, oi_series, price, funding, retail_long, whale_long, candles_15m=None):
    hits = []
    if abs(funding) >= 0.08 or len(candles) < 36:
        return hits, 0.0, 0.0, retail_long, whale_long, price, 0.0, None, None, 0.0, 0.0, 0.0, None

    closes_1h = [float(cl) for _, _, _, cl, *_ in candles]
    ema28_1h = calc_ema(closes_1h[-40:], 28)
    ema48_1h = calc_ema(closes_1h, 48)
    ema_gap_pct = abs(ema28_1h - ema48_1h) / ema48_1h * 100 if ema48_1h > 0 else 0.0

    # ДЕТЕКТОР 3 РЕЖИМОВ РЫНКА (+ переходный слом тренда)
    if ema_gap_pct < 0.45:
        regime = "FLAT"
        regime_label = "📦 Режим рынка: БОКОВИК (ФЛЭТ)"
    elif price > ema28_1h and ema28_1h > ema48_1h:
        regime = "UP"
        regime_label = "📈 Режим рынка: ВОСХОДЯЩИЙ ТРЕНД"
    elif price < ema28_1h and ema28_1h < ema48_1h:
        regime = "DOWN"
        regime_label = "📉 Режим рынка: НИСХОДЯЩИЙ ТРЕНД"
    elif price > ema28_1h and ema28_1h <= ema48_1h:
        regime = "REV_UP"
        regime_label = "🔄 Режим рынка: СЛОМ ТРЕНДА ВВЕРХ"
    else:
        regime = "REV_DOWN"
        regime_label = "🔄 Режим рынка: СЛОМ ТРЕНДА ВНИЗ"

    o1 = float(candles[-1][0])
    o3 = float(candles[-3][0])
    price_chg_1h = (price - o1) / o1 * 100 if o1 else 0
    price_chg_3h = (price - o3) / o3 * 100 if o3 else 0

    cvd_4h_usd = 0.0
    vol_4h_usd = 0.0
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

    if main_signal:
        hits.append(regime_label)
        hits.append(main_signal)

    # Флаг по тренду (#10) — только при мощном наборе ОИ >= +1.5% и CVD >= 80K$
    last_4 = candles[-4:]
    high_4h = max(float(h) for _, h, _, _, *_ in last_4)
    low_4h = min(float(l) for _, _, l, _, *_ in last_4)
    range_4h_pct = (high_4h - low_4h) / low_4h * 100 if low_4h > 0 else 99.0
    if range_4h_pct <= 2.8 and abs(price_chg_3h) <= 1.5 and (oi_chg_1h >= 1.5 or oi_chg_3h >= 3.0):
        if regime == "UP" and cvd_4h_usd >= 80_000:
            if not hits:
                hits.append(regime_label)
            hits.append(f"🟢 #10 Бычий флаг по тренду (коридор {round(low_4h,6)}–{round(high_4h,6)}, ОИ +{round(oi_chg_1h,2)}%, CVD {fmt_usd(cvd_4h_usd)}) — лучше рассмотреть ЛОНГ")
        elif regime == "DOWN" and cvd_4h_usd <= -80_000:
            if not hits:
                hits.append(regime_label)
            hits.append(f"🔴 #10 Медвежий флаг по тренду (коридор {round(low_4h,6)}–{round(high_4h,6)}, ОИ +{round(oi_chg_1h,2)}%, CVD {fmt_usd(cvd_4h_usd)}) — лучше рассмотреть ШОРТ")

    if hits and ema_15m_note:
        hits.append(ema_15m_note)

    return hits, price_chg_1h, oi_chg_1h, retail_long, whale_long, price, 0.0, up_poc, dn_poc, cvd_4h_usd, cvd_pct, nd_usd, ema_bounds

























