def calculate_vwap_metrics(candles_15m: list, curr_price: float):
    if not candles_15m:
        return 0.0, 0.0, False, False
    cum_pv = 0.0
    cum_vol = 0.0
    for _, h, l, c, v in candles_15m:
        tp = (h + l + c) / 3.0
        cum_pv += tp * v
        cum_vol += v
    vwap = cum_pv / cum_vol if cum_vol > 0 else curr_price
    diff_pct = (curr_price - vwap) / vwap * 100 if vwap > 0 else 0.0

    # Проверка вката (разворота обратно к VWAP) по последним двум 15m свечам
    rolling_back_down = False
    rolling_back_up = False
    if len(candles_15m) >= 2:
        prev_c15, curr_c15 = candles_15m[-2:]
        _, prev_h, prev_l, prev_cl, _ = prev_c15
        _, curr_h, curr_l, curr_cl, _ = curr_c15
        rolling_back_down = (curr_cl < prev_cl) and (curr_cl < prev_h)
        rolling_back_up = (curr_cl > prev_cl) and (curr_cl > prev_l)

    return round(vwap, 6), round(diff_pct, 2), rolling_back_down, rolling_back_up


def evaluate_all_9_setups(candles, oi_series, price, funding, retail_long, whale_long, candles_15m=None):
    hits = []
    retail_short = 100.0 - retail_long
    whale_short = 100.0 - whale_long

    o1, h1, l1, c1, v1 = candles[-1]
    o3, _, _, _, _ = candles[-3]
    price_chg_1h = (price - o1) / o1 * 100 if o1 else 0
    price_chg_3h = (price - o3) / o3 * 100 if o3 else 0

    oi_curr = oi_series[-1]
    oi_1h_ago = oi_series[-2]
    oi_3h_ago = oi_series[-4]
    oi_chg_1h = (oi_curr - oi_1h_ago) / oi_1h_ago * 100 if oi_1h_ago else 0
    oi_chg_3h = (oi_curr - oi_3h_ago) / oi_3h_ago * 100 if oi_3h_ago else 0
    oi_max_24h = max(oi_series)

    last_24 = candles[-24:]
    high_24h = max(h for _, h, _, _, _ in last_24)
    low_24h = min(l for _, _, l, _, _ in last_24)
    high_48h = max(h for _, h, _, _, _ in candles)
    low_48h = min(l for _, _, l, _, _ in candles)

    avg_vol_20 = sum(v for _, _, _, _, v in candles[-21:-1]) / 20
    avg_vol_3 = sum(v for _, _, _, _, v in candles[-3:]) / 3
    drop_from_high = (high_24h - price) / high_24h * 100 if high_24h else 0

    # --- НОВЫЙ СЕТАП #1: СУТОЧНЫЙ VWAP (15m) + ПЕРЕГРЕТОСТЬ + ВКАТ ---
    vwap_val, vwap_diff, back_dn, back_up = calculate_vwap_metrics(candles_15m or [], price)

    # Перегретость выше VWAP (>= +1.8%) + начало вката вниз -> ШОРТ
    if vwap_val > 0 and vwap_diff >= 1.8 and back_dn and (oi_chg_1h <= 1.0 or retail_long >= 55.0):
        heat_label = "сильная перегретость" if vwap_diff >= 3.2 else "перегретость"
        hits.append(
            f"🔴 #1 Вкат к VWAP ({heat_label} +{vwap_diff}% выше VWAP {vwap_val}) — лучше рассмотреть ШОРТ"
        )

    # Сильная перепроданность ниже VWAP (<= -1.8%) + откуп вверх -> ЛОНГ
    if vwap_val > 0 and vwap_diff <= -1.8 and back_up and (oi_chg_1h <= 1.0 or retail_short >= 55.0):
        heat_label = "сильная перепроданность" if vwap_diff <= -3.2 else "перепроданность"
        hits.append(
            f"🟢 #1 Вкат к VWAP ({heat_label} {vwap_diff}% ниже VWAP {vwap_val}) — лучше рассмотреть ЛОНГ"
        )

    # --- СЕТАП #2 ---
    if price_chg_1h >= 1.0 and oi_chg_1h >= 5.0 and funding < 0.0:
        hits.append("🟢 #2 Топливо для ЛОНГА (Цена↑ + ОИ↑ + Фандинг<0) — лучше рассмотреть ЛОНГ")

    # --- СЕТАП #3 ---
    pump_24h = (high_24h - low_24h) / low_24h * 100 if low_24h else 0
    flat_range_3h = (max(h for _, h, _, _, _ in candles[-3:]) - min(l for _, _, l, _, _ in candles[-3:])) / price * 100
    if pump_24h >= 5.0 and price > low_24h + (high_24h - low_24h) * 0.65 and flat_range_3h <= 2.5 and oi_chg_3h >= -0.5 and funding <= -0.05 and avg_vol_3 < avg_vol_20:
        hits.append("🟢 #3 Рост-проторговка-рост — лучше рассмотреть ЛОНГ")

    # --- СЕТАП #4 ---
    if price_chg_3h <= -3.0 and l1 <= low_24h * 1.003 and (oi_chg_1h >= 4.0 or oi_chg_3h >= 6.0) and funding <= -0.02 and v1 >= avg_vol_20 * 1.8:
        hits.append("🟢 #4 Контршорт от лимиток кита — лучше рассмотреть ЛОНГ")

    # --- СЕТАП #5 ---
    body1 = abs(c1 - o1)
    lower_wick = min(o1, c1) - l1
    if price_chg_3h <= -2.5 and l1 <= low_24h * 1.002 and lower_wick > body1 * 1.5 and oi_chg_1h <= -3.0 and funding <= 0.005 and v1 >= avg_vol_20 * 2.2:
        hits.append("🟢 #5 Капитуляция лонгов со шпильки — лучше рассмотреть ЛОНГ")

    # --- СЕТАП #6 ---
    if pump_24h >= 8.0 and 0.5 <= drop_from_high <= 4.5 and (oi_chg_1h <= -2.5 or oi_chg_3h <= -4.0) and avg_vol_3 < avg_vol_20 * 0.6 and retail_long >= 55.0:
        hits.append("🔴 #6 Сброс ОИ на истощении пампа — лучше рассмотреть ШОРТ")

    # --- СЕТАП #7 ---
    if h1 >= high_24h * 0.998 and oi_curr <= oi_max_24h * 0.985 and v1 < avg_vol_20 * 0.8 and funding > 0.005 and retail_long >= 56.0:
        hits.append("🔴 #7 Дивергенция ОИ на ложном перехае — лучше рассмотреть ШОРТ")

    # --- СЕТАП #8 ---
    drop_48h = (high_48h - low_48h) / high_48h * 100 if high_48h else 0
    if drop_48h >= 6.0 and price < low_48h + (high_48h - low_48h) * 0.38 and price_chg_3h >= 2.0 and oi_chg_1h <= 0.5 and oi_chg_3h <= 0.5 and avg_vol_3 < avg_vol_20 and -0.02 <= funding <= 0.01:
        hits.append("🔴 #8 Недобор ОИ на пустом отскоке — лучше рассмотреть ШОРТ")

    # --- СЕТАП #9 ---
    upper_wick = h1 - max(o1, c1)
    if price_chg_3h >= 5.0 and upper_wick > body1 * 1.3 and oi_chg_3h >= 6.0 and oi_chg_1h <= 0.5 and v1 >= avg_vol_20 * 2.5 and funding > 0.005 and retail_long >= 60.0:
        hits.append("🔴 #9 Капитуляция шортов на кульминации — лучше рассмотреть ШОРТ")

    return hits, price_chg_1h, oi_chg_1h, retail_long, whale_long, vwap_val, vwap_diff




