import math


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
    back_dn, back_up = False, False
    if len(candles_15m) >= 2:
        _, prev_h, prev_l, prev_cl, *_ = candles_15m[-2]
        _, _, _, curr_cl, *_ = candles_15m[-1]
        back_dn = (curr_cl < prev_cl) and (curr_cl < prev_h)
        back_up = (curr_cl > prev_cl) and (curr_cl > prev_l)
    return round(vwap, 6), round(diff_pct, 2), round(z_score, 2), back_dn, back_up


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
    tb1 = last_c[-1] if len(last_c) >= 6 else v1 * 0.5
    delta1 = (2 * tb1 - v1) / v1 * 100 if v1 > 0 else 0.0
    o3 = next(iter(candles[-3]))
    price_chg_1h = (price - o1) / o1 * 100 if o1 else 0
    price_chg_3h = (price - o3) / o3 * 100 if o3 else 0

    oi_curr, oi_1h_ago, oi_3h_ago = oi_series[-1], oi_series[-2], oi_series[-4]
    oi_chg_1h = (oi_curr - oi_1h_ago) / oi_1h_ago * 100 if oi_1h_ago else 0
    oi_chg_3h = (oi_curr - oi_3h_ago) / oi_3h_ago * 100 if oi_3h_ago else 0
    oi_max_24h = max(oi_series)

    last_24 = candles[-24:]
    high_24h = max(h for _, h, _, _, _, *_ in last_24)
    low_24h = min(l for _, _, l, _, _, *_ in last_24)
    avg_vol_20 = sum(v for _, _, _, _, v, *_ in candles[-21:-1]) / 20
    avg_vol_3 = sum(v for _, _, _, _, v, *_ in candles[-3:]) / 3
    drop_from_high = (high_24h - price) / high_24h * 100 if high_24h else 0
    pump_24h = (high_24h - low_24h) / low_24h * 100 if low_24h else 0

    vwap_val, vwap_diff, z_score, back_dn, back_up = calculate_vwap_bands(candles_15m or [], price)
    up_poc, dn_poc = calculate_poc_levels(candles, price)
    body1, upper_wick, lower_wick = abs(c1 - o1), h1 - max(o1, c1), min(o1, c1) - l1
    bear_abs = (delta1 >= 10.0 and c1 <= o1) or (upper_wick > body1 * 1.4 and delta1 > 0)
    bull_abs = (delta1 <= -10.0 and c1 >= o1) or (lower_wick > body1 * 1.4 and delta1 < 0)

    # #1 VWAP 2σ + Вкат 15m + POC
    if vwap_val > 0 and z_score >= 2.0 and vwap_diff >= 2.0 and back_dn and dn_poc and (bear_abs or oi_chg_1h <= -1.0 or retail_long >= 56.0):
        poc_p, poc_d = dn_poc
        hits.append(f"🔴 #1 VWAP 2σ (+{vwap_diff}%, {z_score}σ, цель POC {poc_p} (-{poc_d}%)) — лучше рассмотреть ШОРТ")

    if vwap_val > 0 and z_score <= -2.0 and vwap_diff <= -2.0 and back_up and up_poc and (bull_abs or oi_chg_1h <= -1.0 or retail_short >= 56.0):
        poc_p, poc_d = up_poc
        hits.append(f"🟢 #1 VWAP 2σ ({vwap_diff}%, {z_score}σ, цель POC {poc_p} (+{poc_d}%)) — лучше рассмотреть ЛОНГ")

    # #2 Топливо для ЛОНГА
    if price_chg_1h >= 1.5 and oi_chg_1h >= 6.0 and funding <= -0.015 and up_poc:
        hits.append(f"🟢 #2 Топливо для ЛОНГА (Цена↑ + ОИ↑ + Фандинг ниже 0, цель POC {up_poc[0]}) — лучше рассмотреть ЛОНГ")

    # #3 Выкуп пролива от суточного дна
    if price_chg_3h <= -3.0 and l1 <= low_24h * 1.004 and v1 >= avg_vol_20 * 1.6 and ((c1 > o1) or (lower_wick > body1 * 1.2) or back_up):
        poc_t = f", цель POC {up_poc[0]} (+{up_poc[1]}%)" if up_poc else ""
        hits.append(f"🟢 #3 Выкуп пролива от дна (объём + откуп{poc_t}) — лучше рассмотреть ЛОНГ")

    # #4 Сброс ОИ на истощении пампа
    if pump_24h >= 9.0 and 1.0 <= drop_from_high <= 4.5 and oi_chg_1h <= -3.5 and avg_vol_3 < avg_vol_20 * 0.55 and retail_long >= 60.0 and dn_poc:
        hits.append(f"🔴 #4 Сброс ОИ на истощении пампа (цель POC {dn_poc[0]}) — лучше рассмотреть ШОРТ")

    # #5 Дивергенция ОИ + Поглощение на перехае
    if h1 >= high_24h * 0.998 and oi_curr <= oi_max_24h * 0.98 and bear_abs and funding > 0.008 and retail_long >= 60.0 and dn_poc:
        hits.append(f"🔴 #5 Дивергенция ОИ / Поглощение на перехае (цель POC {dn_poc[0]}) — лучше рассмотреть ШОРТ")

    # #6 Капитуляция шортов на кульминации
    if price_chg_3h >= 6.0 and upper_wick > body1 * 1.5 and oi_chg_3h >= 7.0 and oi_chg_1h <= 0.0 and v1 >= avg_vol_20 * 2.8 and funding > 0.01 and retail_long >= 63.0:
        hits.append("🔴 #6 Капитуляция шортов на кульминации — лучше рассмотреть ШОРТ")

    # #7 Сильные перекосы («Ножницы» толпа vs киты / экстремальный фандинг)
    if retail_long >= 68.0 and (whale_long <= 52.0 or funding >= 0.04):
        hits.append(f"🔴 #7 Перекос в ЛОНГ (Толпа {round(retail_long,1)}% в лонгах, фандинг {round(funding,3)}%) — перегрев покупателей, ШОРТ от сопротивления")

    if (retail_short >= 66.0 and whale_long >= 50.0) or funding <= -0.15:
        hits.append(f"🟢 #7 Перекос в ШОРТ / Фандинг (Толпа {round(retail_short,1)}% в шортах, фандинг {round(funding,3)}%) — топливо для сквиза, ЛОНГ при откупе")

    return hits, price_chg_1h, oi_chg_1h, retail_long, whale_long, vwap_val, vwap_diff, up_poc, dn_poc









