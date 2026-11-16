def check_heatmap_zones(candles: list, curr_price: float):
    above, below = [], []
    for _, h, l, _, v in candles:
        for off in (0.005, 0.018, 0.038):
            up, dn = h * (1 + off), l * (1 - off)
            if up > curr_price:
                above.append((up, v))
            if dn < curr_price:
                below.append((dn, v))
    def nearest_hot(zones, is_up):
        if not zones:
            return None
        zones.sort(key=lambda x: x[0], reverse=not is_up)
        vols = sorted([v for _, v in zones])
        thresh = vols[int(len(vols) * 0.8)]
        hot = [p for p, v in zones if v >= thresh]
        if not hot:
            return None
        target = hot[0]
        dist = abs(target - curr_price) / curr_price * 100
        return (round(target, 4), round(dist, 2)) if dist <= 1.5 else None
    return nearest_hot(above, True), nearest_hot(below, False)

def evaluate_all_9_setups(candles, oi_series, price, funding, retail_long, whale_long):
    hits = []
    retail_short = 100.0 - retail_long
    whale_short = 100.0 - whale_long
    o1, h1, l1, c1, v1 = candles[-1]
    price_chg_1h = (price - o1) / o1 * 100 if o1 else 0
    price_chg_3h = (price - candles[-3][0]) / candles[-3][0] * 100
    oi_curr = oi_series[-1]
    oi_1h_ago = oi_series[-2]
    oi_3h_ago = oi_series[-4]
    oi_chg_1h = (oi_curr - oi_1h_ago) / oi_1h_ago * 100 if oi_1h_ago else 0
    oi_chg_3h = (oi_curr - oi_3h_ago) / oi_3h_ago * 100 if oi_3h_ago else 0
    oi_max_24h = max(oi_series)
    last_24 = candles[-24:]
    high_24h = max(x[1] for x in last_24)
    low_24h = min(x[2] for x in last_24)
    high_48h = max(x[1] for x in candles)
    low_48h = min(x[2] for x in candles)
    avg_vol_20 = sum(x[4] for x in candles[-21:-1]) / 20
    avg_vol_3 = sum(x[4] for x in candles[-3:]) / 3
    up_zone, dn_zone = check_heatmap_zones(candles, price)
    if oi_chg_1h >= 5.0 and retail_short >= 60.0 and whale_long >= 55.0 and up_zone:
        hits.append(f"🟢 #1 Перекос+Карта ЛОНГ (цель {up_zone[0]}, {up_zone[1]}%)")
    if oi_chg_1h >= 5.0 and retail_long >= 60.0 and whale_short >= 55.0 and dn_zone:
        hits.append(f"🔴 #1 Перекос+Карта ШОРТ (цель {dn_zone[0]}, {dn_zone[1]}%)")
    if price_chg_1h >= 1.0 and oi_chg_1h >= 5.0 and funding < 0.0:
        hits.append("🟢 #2 Топливо для ЛОНГА (Цена↑ + ОИ↑ + Фандинг<0)")
    pump_24h = (high_24h - low_24h) / low_24h * 100 if low_24h else 0
    flat_range_3h = (max(x[1] for x in candles[-3:]) - min(x[2] for x in candles[-3:])) / price * 100
    if pump_24h >= 5.0 and price > low_24h + (high_24h - low_24h) * 0.65 and flat_range_3h <= 2.5 and oi_chg_3h >= -0.5 and funding <= -0.05 and avg_vol_3 < avg_vol_20:
        hits.append("🟢 #3 Рост-проторговка-рост (ЛОНГ)")
    if price_chg_3h <= -3.0 and l1 <= low_24h * 1.003 and (oi_chg_1h >= 4.0 or oi_chg_3h >= 6.0) and funding <= -0.02 and v1 >= avg_vol_20 * 1.8:
        hits.append("🟢 #4 Контршорт (ЛОНГ от лимиток кита)")
    body1 = abs(c1 - o1)
    lower_wick = min(o1, c1) - l1
    if price_chg_3h <= -2.5 and l1 <= low_24h * 1.002 and lower_wick > body1 * 1.5 and oi_chg_1h <= -3.0 and funding <= 0.005 and v1 >= avg_vol_20 * 2.2:
        hits.append("🟢 #5 Капитуляция лонгов (ЛОНГ со шпильки)")
    drop_from_high = (high_24h - price) / high_24h * 100 if high_24h else 0
    if pump_24h >= 8.0 and 0.5 <= drop_from_high <= 4.5 and (oi_chg_1h <= -2.5 or oi_chg_3h <= -4.0) and avg_vol_3 < avg_vol_20 * 0.6 and retail_long >= 55.0:
        hits.append("🔴 #6 Сброс ОИ (ШОРТ на истощении пампа)")
    if h1 >= high_24h * 0.998 and oi_curr <= oi_max_24h * 0.985 and v1 < avg_vol_20 * 0.8 and funding > 0.005 and retail_long >= 56.0:
        hits.append("🔴 #7 Дивергенция ОИ (ШОРТ на ложном перехае)")
    drop_48h = (high_48h - low_48h) / high_48h * 100 if high_48h else 0
    if drop_48h >= 6.0 and price < low_48h + (high_48h - low_48h) * 0.38 and price_chg_3h >= 2.0 and oi_chg_3h <= 0.5 and avg_vol_3 < avg_vol_20 and -0.02 <= funding <= 0.01:
        hits.append("🔴 #8 Недобор ОИ (ШОРТ на пустом отскоке)")
    upper_wick = h1 - max(o1, c1)
    if price_chg_3h >= 5.0 and upper_wick > body1 * 1.3 and oi_chg_3h >= 6.0 and oi_chg_1h <= 0.5 and v1 >= avg_vol_20 * 2.5 and funding > 0.005 and retail_long >= 60.0:
        hits.append("🔴 #9 Капитуляция шортов (ШОРТ на кульминации)")
    return hits, price_chg_1h, oi_chg_1h, retail_long, whale_long
