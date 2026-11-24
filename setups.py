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


def analyze_15m_ema(candles_15m: list, curr_price: float, oi_chg_1h: float, retail_long: float, whale_long: float):
    if not candles_15m or len(candles_15m) < 32:
        return "", None
    closes = [float(cl) for _, _, _, cl, *_ in candles_15m]
    vols = [float(v) for _, _, _, _, v, *_ in candles_15m]

    ema7 = calc_ema(closes[-35:], 7)
    ema14 = calc_ema(closes[-45:], 14)
    ema28 = calc_ema(closes[-60:], 28)

    prev_ema7 = calc_ema(closes[-36:-1], 7)
    prev_ema14 = calc_ema(closes[-46:-1], 14)
    prev_ema28 = calc_ema(closes[-61:-1], 28)

    prev_close = closes[-2]
    avg_vol = sum(vols[-6:-1]) / 5.0 if len(vols) >= 6 else vols[-1]
    last_vol = vols[-1]
    max_recent_vol = max(last_vol, vols[-2]) if len(vols) >= 2 else last_vol

    ema_top = max(ema7, ema14, ema28)
    ema_bot = min(ema7, ema14, ema28)
    prev_top = max(prev_ema7, prev_ema14, prev_ema28)
    prev_bot = min(prev_ema7, prev_ema14, prev_ema28)

    spread_now = (ema_top - ema_bot) / ema_bot * 100 if ema_bot > 0 else 99.0
    spread_prev = (prev_top - prev_bot) / prev_bot * 100 if prev_bot > 0 else 99.0
    dist_from_28 = (curr_price - ema28) / ema28 * 100 if ema28 > 0 else 0.0
    zone_l = f"{round(ema_bot, 6)}–{round(ema_top, 6)}"

    retail_short = 100.0 - retail_long
    whale_short = 100.0 - whale_long
    oi_sign = "+" if oi_chg_1h > 0 else ""

    squeeze_signal = None
    has_scissors = (retail_short >= 55.0 and whale_long >= 53.0) or (retail_long >= 55.0 and whale_short >= 53.0)
    if (spread_now <= 0.85 or spread_prev <= 0.85) and has_scissors:
        whale_side = f"{round(whale_long,1)}% ЛОНГ" if whale_long >= 50 else f"{round(whale_short,1)}% ШОРТ"
        retail_side = f"{round(retail_long,1)}% ЛОНГ" if retail_long >= 50 else f"{round(retail_short,1)}% ШОРТ"
        squeeze_signal = (
            f"🪢 #9 ПЕРЕСЕЧЕНИЕ/СБЛИЖЕНИЕ EMA7/14/28 (зона {zone_l}, ширина {round(spread_now, 2)}%) | "
            f"Киты {whale_side} vs Толпа {retail_side} | ОИ {oi_sign}{round(oi_chg_1h, 2)}%"
        )

    if curr_price > ema_top:
        if (prev_close <= prev_ema28 * 1.003 or dist_from_28 <= 1.35) and max_recent_vol >= avg_vol * 0.85:
            note = f"🚀 15m СТАРТ ЛОНГА: выход над пучком EMA7/14/28 (зона {zone_l}, +{round(dist_from_28, 2)}%)"
        else:
            note = f"⏳ 15m ЛОНГ уже в пути (+{round(dist_from_28, 2)}% от EMA28) — не догонять, ждать ретест EMA {zone_l}"
    elif curr_price < ema_bot:
        if (prev_close >= prev_ema28 * 0.997 or dist_from_28 >= -1.35) and max_recent_vol >= avg_vol * 0.85:
            note = f"🔻 15m СТАРТ ШОРТА: пробой вниз под пучок EMA7/14/28 (зона {zone_l}, {round(dist_from_28, 2)}%)"
        else:
            note = f"⏳ 15m ШОРТ уже в пути ({round(dist_from_28, 2)}% от EMA28) — ждать откат к зоне EMA {zone_l}"
    else:
        note = f"🔄 15m СЖАТИЕ внутри пучка EMA7/14/28 ({zone_l}) — пружина сжимается, ждать выход"

    return note, squeeze_signal


def calculate_vwap_bands(candles_15m: list, curr_price: float):
    if not candles_15m:
        return 0.0, 0.0, 0.0, False, False
    cum_pv, cum_pv2, cum_vol = 0.0, 0.0, 0.0
    for _, h, l, c, v, *_ in candles_15m:
        tp = (h + l + c) / 3.0
        cum_pv += tp * v
        cum_pv2 += (tp * tp) * v
        cum_vol += v
    if cum_vol <= 0:
        return curr_price, 0.0, 0.0, False, False
    vwap = cum_pv / cum_vol
    std_dev = math.sqrt(max((cum_pv2 / cum_vol) - (vwap * vwap), 0.0))
    diff_pct = (curr_price - vwap) / vwap * 100 if vwap > 0 else 0.0
    z_score = (curr_price - vwap) / std_dev if std_dev > 0 else 0.0
    return round(vwap, 6), round(diff_pct, 2), round(z_score, 2), False, False


def calculate_poc_levels(candles: list, curr_price: float):
    above_poc, below_poc = None, None
    best_up_vol, best_dn_vol = 0.0, 0.0
    for o, h, l, c, v, *_ in candles[:-2]:
        tp = (h + l + c) / 3.0
        dist_pct = abs(tp - curr_price) / curr_price * 100 if curr_price else 0
        if 0.8 <= dist_pct <= 7.0:
            if tp > curr_price and v > best_up_vol:
                best_up_vol, above_poc = v, (round(tp, 6), round(dist_pct, 2))
            elif tp < curr_price and v > best_dn_vol:
                best_dn_vol, below_poc = v, (round(tp, 6), round(dist_pct, 2))
    return above_poc, below_poc


def evaluate_all_9_setups(candles, oi_series, price, funding, retail_long, whale_long, candles_15m=None):
    hits = []
    retail_short = 100.0 - retail_long
    last_c = candles[-1]
    o1, h1, l1, c1, v1 = last_c[:5]
    o3 = float(next(iter(candles[-3])))
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
    oi_chg_1h = (oi_curr - oi_1h_ago) / oi_1h_ago * 100 if oi_1h_ago else 0
    oi_usd = oi_curr if oi_curr > 100_000 else oi_curr * price
    nd_usd = oi_usd * ((whale_long - (100.0 - whale_long)) / 100.0)

    last_24 = candles[-24:]
    low_24h = min(l for _, _, l, _, _, *_ in last_24)
    avg_vol_20 = sum(v for _, _, _, _, v, *_ in candles[-21:-1]) / 20
    body1, lower_wick = abs(c1 - o1), min(o1, c1) - l1

    vwap_val, vwap_diff, _, _, _ = calculate_vwap_bands(candles_15m or [], price)
    up_poc, dn_poc = calculate_poc_levels(candles, price)
    ema_15m_note, squeeze_signal = analyze_15m_ema(candles_15m or [], price, oi_chg_1h, retail_long, whale_long)

    # #3 Выкуп пролива от суточного дна
    if price_chg_3h <= -3.0 and l1 <= low_24h * 1.004 and v1 >= avg_vol_20 * 1.6 and ((c1 > o1) or (lower_wick > body1 * 1.2)):
        poc_p, poc_d = up_poc if up_poc else (None, None)
        poc_t = f", цель POC {poc_p} (+{poc_d}%)" if poc_p else ""
        hits.append(f"🟢 #3 Выкуп пролива от дна (объём + откуп{poc_t}) — лучше рассмотреть ЛОНГ")

    # #7 Чистые «Ножницы» (Толпа против Китов)
    if (retail_long >= 58.0 and whale_long <= 45.0) or (retail_long >= 65.0 and whale_long <= 48.0):
        hits.append(f"🔴 #7 Перекос в ЛОНГ (Толпа {round(retail_long,1)}% в лонгах против Китов {round(100-whale_long,1)}% в шортах) — лучше рассмотреть ШОРТ")

    if (retail_short >= 58.0 and whale_long >= 55.0) or (retail_short >= 65.0 and whale_long >= 52.0):
        hits.append(f"🟢 #7 Перекос в ШОРТ (Толпа {round(retail_short,1)}% в шортах против Китов {round(whale_long,1)}% в лонгах) — лучше рассмотреть ЛОНГ")

    # #8 Дивергенция CVD
    if price_chg_3h >= 2.5 and cvd_pct <= -6.0 and dn_poc:
        poc_p, poc_d = dn_poc
        hits.append(f"🔴 #8 Дивергенция CVD (Цена↑, но дельта {round(cvd_pct,1)}% — скрытые продажи, цель POC {poc_p} (-{poc_d}%)) — лучше рассмотреть ШОРТ")

    if price_chg_3h <= -2.5 and cvd_pct >= 6.0 and up_poc:
        poc_p, poc_d = up_poc
        hits.append(f"🟢 #8 Дивергенция CVD (Цена↓, но дельта +{round(cvd_pct,1)}% — лимитный выкуп, цель POC {poc_p} (+{poc_d}%)) — лучше рассмотреть ЛОНГ")

    # #9 Пересечение/сближение EMA7/14/28 + перекос Толпа против Китов + значение ОИ
    if squeeze_signal:
        hits.append(squeeze_signal)

    if hits and ema_15m_note and not any("#9" in h for h in hits):
        hits.append(ema_15m_note)

    return hits, price_chg_1h, oi_chg_1h, retail_long, whale_long, vwap_val, vwap_diff, up_poc, dn_poc, cvd_4h_usd, cvd_pct, nd_usd

















