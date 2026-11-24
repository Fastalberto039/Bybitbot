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
    ema = float(next(iter(values)))
    for v in values[1:]:
        ema = float(v) * k + ema * (1.0 - k)
    return ema


def analyze_15m_ema(candles_15m: list, curr_price: float, oi_chg_1h: float, trend_dir: str, cvd_4h_usd: float):
    if not candles_15m or len(candles_15m) < 32:
        return "", None, None
    closes = [float(cl) for _, _, _, cl, *_ in candles_15m]
    vols = [float(v) for _, _, _, _, v, *_ in candles_15m]

    ema7 = calc_ema(closes[-35:], 7)
    ema14 = calc_ema(closes[-45:], 14)
    ema28 = calc_ema(closes[-60:], 28)

    prev_close = closes[-2]
    avg_vol = sum(vols[-6:-1]) / 5.0 if len(vols) >= 6 else vols[-1]
    last_vol = vols[-1]

    ema_top = max(ema7, ema14, ema28)
    ema_bot = min(ema7, ema14, ema28)
    spread_now = (ema_top - ema_bot) / ema_bot * 100 if ema_bot > 0 else 99.0
    dist_from_28 = (curr_price - ema28) / ema28 * 100 if ema28 > 0 else 0.0
    zone_l = f"{round(ema_bot, 6)}–{round(ema_top, 6)}"
    ema_bounds = (ema_bot, ema_top)
    oi_sign = "+" if oi_chg_1h > 0 else ""

    squeeze_signal = None
    # Сжатие 15m EMA работает ТОЛЬКО по направлению старшего тренда!
    if spread_now <= 0.75:
        if trend_dir == "UP" and curr_price >= ema_bot * 0.998 and oi_chg_1h >= 0.3 and cvd_4h_usd > 0:
            squeeze_signal = f"🪢 #9 СБЛИЖЕНИЕ EMA ПО ТРЕНДУ ВВЕРХ (рассмотреть ЛОНГ, зона {zone_l}, ширина {round(spread_now, 2)}%) | ОИ {oi_sign}{round(oi_chg_1h, 2)}% | CVD↑"
        elif trend_dir == "DOWN" and curr_price <= ema_top * 1.002 and cvd_4h_usd < 0:
            squeeze_signal = f"🪢 #9 СБЛИЖЕНИЕ EMA ПО ТРЕНДУ ВНИЗ (рассмотреть ШОРТ, зона {zone_l}, ширина {round(spread_now, 2)}%) | ОИ {oi_sign}{round(oi_chg_1h, 2)}% | CVD↓"

    # Модель Б: Первый откат к EMA14 по старшему тренду после импульса
    pullback_signal = None
    if trend_dir == "UP" and ema7 > ema14 > ema28 and abs(curr_price - ema14) / ema14 * 100 <= 0.35 and last_vol < avg_vol and cvd_4h_usd > 0 and oi_chg_1h >= 0.2:
        pullback_signal = f"🎯 #11 ОТКАТ ПО ТРЕНДУ НА 15m EMA14 ({round(ema14, 6)}) — лучше рассмотреть ЛОНГ (ОИ {oi_sign}{round(oi_chg_1h, 2)}%, CVD↑)"
    elif trend_dir == "DOWN" and ema7 < ema14 < ema28 and abs(curr_price - ema14) / ema14 * 100 <= 0.35 and last_vol < avg_vol and cvd_4h_usd < 0:
        pullback_signal = f"🎯 #11 ОТКАТ ПО ТРЕНДУ ПОД 15m EMA14 ({round(ema14, 6)}) — лучше рассмотреть ШОРТ (ОИ {oi_sign}{round(oi_chg_1h, 2)}%, CVD↓)"

    if pullback_signal:
        squeeze_signal = pullback_signal

    if curr_price > ema_top:
        note = f"🚀 15m выше пучка EMA ({zone_l}, +{round(dist_from_28, 2)}%)"
    elif curr_price < ema_bot:
        note = f"🔻 15m ниже пучка EMA ({zone_l}, {round(dist_from_28, 2)}%)"
    else:
        note = f"🔄 15m внутри пучка EMA ({zone_l})"

    return note, squeeze_signal, ema_bounds


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

    # Определяем СТАРШИЙ ТРЕНД по 1Ч свечам (EMA28 и EMA48 — аналог 4Ч структуры)
    closes_1h = [float(c[3]) for c in candles]
    ema28_1h = calc_ema(closes_1h[-40:], 28)
    ema48_1h = calc_ema(closes_1h, 48)
    if price > ema28_1h and ema28_1h > ema48_1h:
        trend_dir = "UP"
    elif price < ema28_1h and ema28_1h < ema48_1h:
        trend_dir = "DOWN"
    else:
        # В пиле без тренда на старшем ТФ вообще не входим!
        return hits, 0.0, 0.0, retail_long, whale_long, price, 0.0, None, None, 0.0, 0.0, 0.0, None

    last_c = candles[-1]
    o1 = float(last_c[0])
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
    ema_15m_note, squeeze_signal, ema_bounds = analyze_15m_ema(candles_15m or [], price, oi_chg_1h, trend_dir, cvd_4h_usd)

    if squeeze_signal:
        hits.append(squeeze_signal)

    # #10 Проторговка (флаг) ПО СТАРШЕМУ ТРЕНДУ с набором ОИ и подтверждением CVD
    last_4 = candles[-4:]
    high_4h = max(float(x[1]) for x in last_4)
    low_4h = min(float(x[2]) for x in last_4)
    range_4h_pct = (high_4h - low_4h) / low_4h * 100 if low_4h > 0 else 99.0
    if range_4h_pct <= 3.0 and abs(price_chg_3h) <= 1.8 and (oi_chg_1h >= 1.0 or oi_chg_3h >= 2.0):
        if trend_dir == "UP" and cvd_4h_usd > 0:
            hits.append(f"🟢 #10 Бычий флаг по тренду 1Ч/4Ч (коридор {round(low_4h,6)}–{round(high_4h,6)}, ОИ +{round(oi_chg_1h,2)}%, CVD↑) — лучше рассмотреть ЛОНГ")
        elif trend_dir == "DOWN" and cvd_4h_usd < 0:
            hits.append(f"🔴 #10 Медвежий флаг по тренду 1Ч/4Ч (коридор {round(low_4h,6)}–{round(high_4h,6)}, ОИ +{round(oi_chg_1h,2)}%, CVD↓) — лучше рассмотреть ШОРТ")

    if hits and ema_15m_note and not any("#9" in h for h in hits):
        hits.append(ema_15m_note)

    return hits, price_chg_1h, oi_chg_1h, retail_long, whale_long, price, 0.0, up_poc, dn_poc, cvd_4h_usd, cvd_pct, nd_usd, ema_bounds






















