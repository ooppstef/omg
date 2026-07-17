import adata
import math
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from stocks.stock_models import StockCurrentPrice
from stocks.industry_models import IndustryConcept
from stocks.util import finite_float, recent_date

get_recent_date = recent_date
_finite_float = finite_float


def east_top_list(top=30, print_result=True):
    # 东方财富网热门排行（同花顺接口暂不可用）
    res_df = adata.sentiment.hot.pop_rank_100_east()
    stock_current_prices = []
    for _, row in res_df.iterrows():
        stock_current_prices.append(
            StockCurrentPrice(
                row["stock_code"],
                row["short_name"],
                row["price"],
                row["change_pct"],
                "0",
            )
        )
    stock_current_prices = stock_current_prices[:top]
    if print_result:
        for stock in stock_current_prices:
            print(stock)
    return stock_current_prices


def hot_concept_ths(top=8, print_result=True):
    res_df = adata.sentiment.hot.hot_concept_20_ths()
    industry_concepts = []
    for _, row in res_df.iterrows():
        industry_concepts.append(IndustryConcept.from_dict(row))
    industry_concepts = industry_concepts[:top]
    if print_result:
        for industry_concept in industry_concepts:
            print(industry_concept)
    return industry_concepts


def _calculate_strength_scores(excess_returns, amounts):
    """绝对强度 = 超额收益 × (1 + log(成交额/中位数成交额 + 1))"""
    median_amount = amounts.median()
    if median_amount == 0:
        median_amount = 1

    def calc(excess, amount):
        ratio = amount / median_amount
        multiplier = math.log(ratio + 1) if ratio > 0 else 0
        return round(excess * (1 + multiplier), 2)

    return pd.Series(
        [calc(excess_returns[i], amounts[i]) for i in excess_returns.index],
        index=excess_returns.index,
    )


def _get_benchmark_returns(start_date, days=10, verbose=True):
    """获取沪深300过去N天涨幅"""
    try:
        df = adata.stock.market.get_market_index(index_code='000300', start_date=start_date)
        if df is None or df.empty:
            return {}
        df = df.sort_values('trade_date').tail(days)
        result = {}
        for trade_date, change_pct in zip(df["trade_date"], df["change_pct"]):
            value = _finite_float(change_pct)
            if value is not None:
                result[str(trade_date)[:10]] = value
        return result
    except Exception as e:
        if verbose:
            print(f"获取沪深300基准失败: {e}")
        return {}


# 排除列表（指数类/非行业概念）
CONCEPT_EXCLUDE = {
    '融资融券', '深股通', '沪股通', '标准普尔', '富时罗素', 'MSCI',
    '同花顺中特估100', '同花顺出海50', '同花顺新质50', '同花顺果指数', '同花顺漂亮100',
    '中国AI 50', 'ST板块', '摘帽',
    '新股与次新股', '科创次新股', '注册制次新股',
    '举牌', '股权转让(并购重组)',
    '证金持股', '国家大基金持股',
    '参股保险', '参股券商', '参股银行',
    '高股息精选', '独角兽概念',
}


def concept_strength_top10(days=10, top=10, print_result=True, exclude=None):
    """
    计算概念板块强度Top10
    强度 = 超额收益 × (1 + log(成交额/当日中位数成交额 + 1))
    exclude: 额外排除的板块名称集合
    """
    exclude_set = CONCEPT_EXCLUDE | set(exclude or [])
    start_date = get_recent_date(days + 15)
    
    # 获取概念列表和基准
    if print_result:
        print("获取概念列表...")
    concept_df = adata.stock.info.all_concept_code_ths()
    if concept_df is None or concept_df.empty:
        if print_result:
            print("获取概念列表失败")
        return None
    
    benchmark = _get_benchmark_returns(start_date, days, print_result)
    if not benchmark:
        if print_result:
            print("获取沪深300基准数据失败，无法计算超额收益")
        return None
    benchmark_dates = set(benchmark.keys())
    
    # 批量获取概念数据（多线程并发）
    all_data = []
    # 过滤排除列表
    rows = [row for _, row in concept_df.iterrows() 
            if row.get('name') not in exclude_set and not pd.isna(row.get('index_code'))]
    total = len(rows)
    
    def fetch_concept(row):
        code = row.get('index_code')
        name = row.get('name')
        try:
            df = adata.stock.market.get_market_concept_ths(index_code=code)
            if df is None or df.empty:
                return []
            df = df[df['trade_date'] >= start_date].sort_values('trade_date').tail(days)
            results = []
            for _, r in df.iterrows():
                date = str(r['trade_date'])[:10]
                if date not in benchmark_dates:
                    continue
                change_pct = _finite_float(r.get("change_pct"))
                amount = _finite_float(r.get("amount"))
                if change_pct is None or amount is None:
                    continue
                results.append({
                    'code': code, 'name': name, 'date': date,
                    'change_pct': change_pct,
                    'amount': amount,
                })
            return results
        except Exception as e:
            return [('error', name, code, str(e))]
    
    completed = 0
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(fetch_concept, row): row for row in rows}
        for future in as_completed(futures):
            completed += 1
            if print_result and (completed % 20 == 0 or completed == total):
                print(f"获取概念数据: {completed}/{total}")
            result = future.result()
            if result and isinstance(result[0], tuple) and result[0][0] == 'error':
                if print_result:
                    _, name, code, err = result[0]
                    print(f"获取{name}({code})失败: {err}")
            else:
                all_data.extend(result)
    
    if not all_data:
        if print_result:
            print("未获取到数据")
        return None
    
    # 计算每天强度并取Top10
    data_df = pd.DataFrame(all_data)
    dates = sorted(data_df['date'].unique())[-days:]
    daily_top = {}  # {date: [(name, score), ...]}
    
    for date in dates:
        day = data_df[data_df['date'] == date].copy()
        if day.empty:
            continue
        day['excess'] = day['change_pct'] - benchmark[date]
        day = day.set_index('code')
        scores = _calculate_strength_scores(day['excess'], day['amount'])
        # 合并名称和分数，按分数排序取Top
        day_scores = [(day.loc[code, 'name'], round(scores[code], 1)) for code in scores.index]
        day_scores.sort(key=lambda x: x[1], reverse=True)
        daily_top[date] = day_scores[:top]
    
    if print_result and daily_top:
        print(f"\n概念板块绝对强度 Top{top} (正=跑赢大盘，负=跑输大盘)")
        
        def display_width(s):
            return sum(2 if ord(c) > 127 else 1 for c in str(s))
        
        def pad_to_width(s, width):
            s = str(s)
            pad = width - display_width(s)
            return s + ' ' * pad
        
        col_width = 16
        # 表头
        header = pad_to_width('排名', 6) + "|" + "|".join(pad_to_width(d[-5:], col_width) for d in dates) + "|"
        print(header)
        print("-" * len(header))
        # 数据行（名称行）
        for rank in range(top):
            # 名称行
            name_row = pad_to_width(str(rank + 1), 6) + "|"
            score_row = pad_to_width('', 6) + "|"
            for date in dates:
                if date in daily_top and rank < len(daily_top[date]):
                    name, score = daily_top[date][rank]
                    if display_width(name) > col_width - 2:
                        while display_width(name) > col_width - 4:
                            name = name[:-1]
                        name += '..'
                    name_row += pad_to_width(name, col_width) + "|"
                    score_row += pad_to_width(str(score), col_width) + "|"
                else:
                    name_row += pad_to_width("-", col_width) + "|"
                    score_row += pad_to_width("", col_width) + "|"
            print(name_row)
            print(score_row)
            print("-" * len(header))
    
    return daily_top


def concept_strength_chart(concept_names, days=10):
    """
    绘制概念板块的强度曲线（支持多板块对比）
    concept_names: 板块名称或列表（支持模糊匹配）
    """
    import matplotlib.pyplot as plt
    
    if isinstance(concept_names, str):
        concept_names = [concept_names]
    
    # 设置中文字体
    plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'PingFang SC']
    plt.rcParams['axes.unicode_minus'] = False
    
    start_date = get_recent_date(days + 15)
    concept_df = adata.stock.info.all_concept_code_ths()
    if (
        concept_df is None
        or concept_df.empty
        or not {"name", "index_code"}.issubset(concept_df.columns)
    ):
        print("获取概念列表失败")
        return None
    
    # 获取基准
    benchmark = _get_benchmark_returns(start_date, days, verbose=False)
    if not benchmark:
        print("获取基准数据失败")
        return None
    
    # 收集所有板块数据
    all_series = {}
    series_dates = {}
    
    for concept_name in concept_names:
        names = concept_df["name"].astype("string")
        matches = concept_df[
            names.str.contains(str(concept_name), na=False, regex=False)
        ]
        if matches.empty:
            print(f"未找到包含'{concept_name}'的板块")
            continue
        
        row = matches.iloc[0]
        code, name = row['index_code'], row['name']
        print(f"匹配到板块: {name} ({code})")
        
        try:
            df = adata.stock.market.get_market_concept_ths(index_code=code)
            if df is None or df.empty:
                print(f"获取{name}数据失败")
                continue
        except Exception as e:
            print(f"获取{name}数据失败: {e}")
            continue
        
        df = df[df['trade_date'] >= start_date].sort_values('trade_date').tail(days)
        df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
        median_amount = df['amount'].median() or 1

        dates, strengths = [], []
        for _, r in df.iterrows():
            date = str(r['trade_date'])[:10]
            if date not in benchmark:
                continue
            change_pct = _finite_float(r.get("change_pct"))
            amount = _finite_float(r.get("amount"))
            if change_pct is None or amount is None:
                continue
            excess = change_pct - benchmark[date]
            ratio = amount / median_amount
            strength = excess * (1 + math.log(ratio + 1)) if ratio > 0 else excess
            dates.append(date[-5:])
            strengths.append(round(strength, 2))

        if dates:
            all_series[name] = strengths
            series_dates[name] = dates
    
    if not all_series:
        print("无有效数据")
        return None
    
    # 绘图
    fig, ax = plt.subplots(figsize=(12, 6))
    colors = ['#E53935', '#1E88E5', '#43A047', '#FB8C00', '#8E24AA', '#00ACC1']
    
    for i, (name, strengths) in enumerate(all_series.items()):
        color = colors[i % len(colors)]
        ax.plot(
            series_dates[name],
            strengths,
            'o-',
            label=name,
            color=color,
            markersize=5,
            linewidth=2,
        )
    
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
    max_days = max(len(dates) for dates in series_dates.values())
    ax.set_title(f'概念板块绝对强度对比 (近{max_days}天)')
    ax.set_xlabel('日期')
    ax.set_ylabel('绝对强度')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.show()
    
    return all_series
