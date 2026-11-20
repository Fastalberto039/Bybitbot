def calculate_vwap_metrics(candles_15m: list, curr_price: float):
    if not candles_15m:
        return 0.0, 0.0, False, False
    cum_pv = 0.0
    cum_vol = 0.0
    for _, h, l, c, v, *_ in candles_15m:
        tp = (h + l + c) / 3.0
        cum_pv += tp * v
        cum_vol += v
    vwap = cum_pv / cum_vol if cum_vol > 0 else curr_price
    diff_pct = (curr_price - vwap) / vwap * 100 if vwap > 0 else 0.0

    rolling_back_down = False
    rolling_back_up = False
    if len(candles_15m) >= 2:
        prev_c15, curr_c15 = candles_15m[-2:]
        _, prev_h, prev_l, prev_cl, *_ = prev_c15
        _, curr_h, curr_l, curr_cl, *_ = curr_c15
        rolling_back_down = (curr_cl < prev_cl) and (curr_cl < prev_h)
        rolling_back_up = (curr_cl > prev_cl) and (curr_cl > prev_l)

    return round(vwap, 6), round(diff_pct, 2), rolling_back_down, rolling_back_up


def calculate_poc_levels(candles: list, curr_price: float):
    above_poc = None
    below_poc = None
    best_up_vol = 0.0
    best_dn_vol = 0.0

    for o, h, l, c, v, *_ in candles[:-2]:
        tp = (h + l + c) / 3.0
        dist_pct = abs(tp - curr_price) / curr_price * 100 if curr_price else 0
        if 0.8 <= dist_pct <= 4.5:
            if tp > curr_price and v > best_up_vol:
                best_up_vol = v
                above_poc = (round(tp, 6), round(dist_pct, 2))
            elif tp < curr_price and v > best_dn_vol:
                best_dn_vol = v
                below_poc = (round(tp, 6), round(dist_pct, 2))

    return above_poc, below_poc


def evaluate_all_9_setups(candles, oi_series, price, funding, retail_long, whale_long, candles_15m=None):
    hits = []
    retail_short = 100.0 - retail_long
    whale_short = 100.0 - whale_long

    last_c = candles[-1]
    o1, h1, l1, c1, v1 = last_c[:5]
    tb1 = last_c[-1] if len(last_c) >= 6 else v1 * 0.5
    delta1 = (2 * tb1 - v1) / v1 * 100 if v1 > 0 else 0.0

    o3 = next(iter(candles[-3]))
    price_chg_1h = (price - o1) / o1 * 100 if o1 else 0
    price_chg_3h = (price - o3) / o3 * 100 if o3 else 0

    oi_curr = oi_series[-1]
    oi_1h_ago = oi_series[-2]
    oi_3h_ago = oi_series[-4]
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

    vwap_val, vwap_diff, back_dn, back_up = calculate_vwap_metrics(candles_15m or [], price)
    up_poc, dn_poc = calculate_poc_levels(candles, price)

    bear_absorption = (delta1 >= 8.0 and c1 <= o1) or (h1 - max(o1, c1) > abs(c1 - o1) * 1.2 and delta1 > 0)
    bull_absorption = (delta1 <= -8.0 and c1 >= o1) or (min(o1, c1) - l1 > abs(c1 - o1) * 1.2 and delta1 < 0)

    # --- #1 ВКАТ / ПРОБОЙ VWAP (15m) + ПЕРЕГРЕТОСТЬ + POC ---
    if vwap_val > 0 and vwap_diff >= 1.8 and back_dn and (oi_chg_1h <= 1.0 or retail_long >= 55.0 or bear_absorption):
        heat_label = "сильная перегретость" if vwap_diff >= 3.2 else "перегретость"
        poc_p, poc_d = dn_poc if dn_poc else (None, None)
        poc_txt = f", цель POC {poc_p} (-{poc_d}%)" if poc_p else ""
        abs_txt = " + поглощение" if bear_absorption else ""
        hits.append(f"🔴 #1 Вкат к VWAP ({heat_label} +{vwap_diff}%{abs_txt}{poc_txt}) — лучше рассмотреть ШОРТ")

    if vwap_val > 0 and vwap_diff <= -1.8 and back_up and (oi_chg_1h <= 1.0 or retail_short >= 55.0 or bull_absorption):
        heat_label = "сильная перепроданность" if vwap_diff <= -3.2 else "перепроданность"
        poc_p, poc_d = up_poc if up_poc else (None, None)
        poc_txt = f", цель POC {poc_p} (+{poc_d}%)" if poc_p else ""
        abs_txt = " + поглощение" if bull_absorption else ""
        hits.append(f"🟢 #1 Вкат к VWAP ({heat_label} {vwap_diff}%{abs_txt}{poc_txt}) — лучше рассмотреть ЛОНГ")

    # --- #2 ТОПЛИВО ДЛЯ ЛОНГА ---
    if price_chg_1h >= 1.0 and oi_chg_1h >= 5.0 and funding < 0.0:
        poc_p, _ = up_poc if up_poc else (None, None)
        poc_txt = f" (цель POC {poc_p})" if poc_p else ""
        hits.append(f"🟢 #2 Топливо для ЛОНГА (Цена↑ + ОИ↑ + Фандинг<0){poc_txt} — лучше рассмотреть ЛОНГ")

    # --- #3 КОНТРШОРТ ОТ ЛИМИТОК КИТА ---
    if price_chg_3h <= -3.0 and l1 <= low_24h * 1.003 and (oi_chg_1h >= 4.0 or oi_chg_3h >= 6.0) and funding <= -0.02 and (v1 >= avg_vol_20 * 1.8 or bull_absorption):
        hits.append("🟢 #3 Контршорт от лимиток кита (поглощение продаж) — лучше рассмотреть ЛОНГ")

    # --- #4 СБРОС ОИ НА ИСТОЩЕНИИ ПАМПА ---
    if pump_24h >= 8.0 and 0.5 <= drop_from_high <= 4.5 and (oi_chg_1h <= -2.5 or oi_chg_3h <= -4.0) and avg_vol_3 < avg_vol_20 * 0.6 and retail_long >= 55.0:
        poc_p, _ = dn_poc if dn_poc else (None, None)
        poc_txt = f" (цель POC {poc_p})" if poc_p else ""
        hits.append(f"🔴 #4 Сброс ОИ на истощении пампа{poc_txt} — лучше рассмотреть ШОРТ")

    # --- #5 ДИВЕРГЕНЦИЯ ОИ + ПОГЛОЩЕНИЕ НА ПЕРЕХАЕ ---
    if h1 >= high_24h * 0.998 and (oi_curr <= oi_max_24h * 0.985 or bear_absorption) and funding > 0.005 and retail_long >= 56.0:
        poc_p, _ = dn_poc if dn_poc else (None, None)
        poc_txt = f" (цель POC {poc_p})" if poc_p else ""
        hits.append(f"🔴 #5 Дивергенция ОИ / Поглощение на перехае{poc_txt} — лучше рассмотреть ШОРТ")

    # --- #6 КАПИТУЛЯЦИЯ ШОРТОВ НА КУЛЬМИНАЦИИ ---
    body1 = abs(c1 - o1)
    upper_wick = h1 - max(o1, c1)
    if price_chg_3h >= 5.0 and upper_wick > body1 * 1.3 and oi_chg_3h >= 6.0 and oi_chg_1h <= 0.5 and v1 >= avg_vol_20 * 2.5 and funding > 0.005 and retail_long >= 60.0:
        hits.append("🔴 #6 Капитуляция шортов на кульминации — лучше рассмотреть ШОРТ")

    return hits, price_chg_1h, oi_chg_1h, retail_long, whale_long, vwap_val, vwap_diff, up_poc, dn_poc





