import adata
import datetime
import pandas as pd

# 常量配置
TREND_SCORE_BASE = 50
STABILITY_WEIGHT = 20
COMPOSITE_STRONG_THRESHOLD = 70
COMPOSITE_STABLE_THRESHOLD = 50


def get_recent_date(n):
    """获取n天前的日期"""
    today = datetime.date.today()
    target_date = today - datetime.timedelta(days=n)
    return target_date.strftime("%Y-%m-%d")


def get_stocks_current_performance(stock_codes):
    """
    批量获取个股实时表现

    返回:
        list: 包含 code, name, change_pct 的字典列表
    """
    if not stock_codes:
        return []

    try:
        # 使用实时数据API（更快、更可靠）
        realtime_df = adata.stock.market.list_market_current(stock_codes)
        if realtime_df is None or realtime_df.empty:
            return []

        results = []
        for _, row in realtime_df.iterrows():
            results.append({
                "code": row["stock_code"],
                "name": row["short_name"],
                "change_pct": float(row["change_pct"]) if row["change_pct"] else 0.0,
                "price": float(row["price"]) if row["price"] else 0.0,
            })
        return results
    except Exception as e:
        print(f"获取实时行情失败: {e}")
        return []


def analyze_hot_sectors_rotation(
    days: int = 5, top_concepts: int = 10, top_stocks: int = 5
):
    """
    热点板块轮动分析

    功能：
    1. 追踪过去N天持续热门板块（多次上榜）
    2. 分析热点板块内的领涨个股
    3. 判断板块是否企稳（波动率、趋势方向）
    4. 输出板块轮动建议

    参数:
        days: 追踪天数（默认5天）
        top_concepts: 关注前N个热点板块（默认10）
        top_stocks: 每个板块展示前N只个股（默认5）

    返回:
        分析结果字典
    """
    print(f"=== 热点板块轮动分析（近{days}天）===\n")

    # 步骤1: 获取当前热点板块
    print("1. 获取当前热点板块...")
    try:
        current_hot = adata.sentiment.hot.hot_concept_20_ths()
    except Exception as e:
        print(f"获取热点板块失败: {e}")
        return None

    if current_hot is None or current_hot.empty:
        print("获取热点板块失败: 返回空数据")
        return None

    print(f"   当前前{top_concepts}热点板块：")
    for _, row in current_hot.head(top_concepts).iterrows():
        print(
            f"   {row['rank']:2d}. {row['concept_name']:12s} "
            f"涨跌幅:{row['change_pct']:+.2f}% 热度:{row['hot_tag']}"
        )

    # 步骤2: 获取这些板块的历史数据判断持续性
    print("\n2. 分析板块持续性及趋势...")
    start_date = get_recent_date(days + 5)

    concept_analysis = []

    for _, row in current_hot.head(top_concepts).iterrows():
        concept_code = row["concept_code"]
        concept_name = row["concept_name"]

        try:
            # 获取历史行情数据
            hist_df = adata.stock.market.get_market_concept_ths(index_code=concept_code)
            if hist_df is None or hist_df.empty:
                continue

            # 过滤近N天数据
            hist_df = hist_df[hist_df["trade_date"] >= start_date].sort_values(
                "trade_date"
            )
            if len(hist_df) < 3:  # 数据不足
                continue

            # 计算趋势指标
            recent_data = hist_df.tail(days)
            if len(recent_data) < 3:
                continue

            change_pcts = recent_data["change_pct"].astype(float)

            # 趋势方向 (近N天累计涨跌幅)
            total_change = change_pcts.sum()

            # 波动率 (标准差)
            volatility = change_pcts.std()

            # 企稳判断
            up_days = (change_pcts > 0).sum()
            down_days = (change_pcts < 0).sum()

            # 企稳得分 (0-100): 趋势向上 + 波动适中
            trend_score = min(100, max(0, TREND_SCORE_BASE + total_change * 2))
            stability_score = max(0, 100 - volatility * STABILITY_WEIGHT)

            # 综合得分
            composite_score = trend_score * 0.6 + stability_score * 0.4

            # 判断状态
            if composite_score >= COMPOSITE_STRONG_THRESHOLD:
                status = "🔥 强势"
            elif composite_score >= COMPOSITE_STABLE_THRESHOLD:
                status = "📈 企稳"
            elif total_change > 0:
                status = "⚠️  分化"
            else:
                status = "📉 调整"

            concept_analysis.append(
                {
                    "code": concept_code,
                    "name": concept_name,
                    "rank": row["rank"],
                    "change_pct": row["change_pct"],
                    "hot_tag": row["hot_tag"],
                    "total_change": total_change,
                    "volatility": volatility,
                    "up_days": up_days,
                    "down_days": down_days,
                    "trend_score": trend_score,
                    "stability_score": stability_score,
                    "composite_score": composite_score,
                    "status": status,
                    "recent_changes": change_pcts.tolist(),
                }
            )

        except Exception as e:
            print(f"   分析{concept_name}失败: {e}")
            continue

    if not concept_analysis:
        print("没有获取到有效数据")
        return None

    # 按综合得分排序
    concept_analysis.sort(key=lambda x: x["composite_score"], reverse=True)

    print("\n   板块持续性分析：")
    print(
        f"   {'排名':<4} {'板块':<12} {'累计涨跌':<8} {'波动率':<8} {'涨跌天数':<10} {'状态':<8} {'综合分':<6}"
    )
    print("   " + "-" * 65)
    for item in concept_analysis:
        print(
            f"   {item['rank']:<4} {item['name']:<12} "
            f"{item['total_change']:+.2f}%    "
            f"{item['volatility']:.2f}%     "
            f"{item['up_days']}涨/{item['down_days']}跌    "
            f"{item['status']:<8} {item['composite_score']:.1f}"
        )

    # 步骤3: 获取强势板块的成分股
    print(f"\n3. 分析强势板块领涨个股...")

    # 选择综合得分前5的板块深入分析
    strong_concepts = [
        c
        for c in concept_analysis
        if c["composite_score"] >= COMPOSITE_STABLE_THRESHOLD
    ][:5]

    for concept in strong_concepts:
        print(
            f"\n   【{concept['name']}】{concept['status']} (综合分:{concept['composite_score']:.1f})"
        )

        try:
            # 获取成分股
            stocks_df = adata.stock.info.concept_constituent_ths(
                index_code=concept["code"]
            )
            if stocks_df is None or stocks_df.empty:
                print(f"      获取成分股失败: 返回空数据")
                continue

            # 获取成分股代码列表
            stock_codes = stocks_df["stock_code"].tolist()

            # 批量获取实时行情（更快、更可靠）
            stock_performances = get_stocks_current_performance(stock_codes)

            # 按涨跌幅排序
            stock_performances.sort(key=lambda x: x["change_pct"], reverse=True)

            print(f"      领涨个股 (Top{top_stocks})：")
            for i, stock in enumerate(stock_performances[:top_stocks], 1):
                emoji = (
                    "🔥"
                    if stock["change_pct"] > 5
                    else "📈"
                    if stock["change_pct"] > 2
                    else "📊"
                )
                print(
                    f"      {i}. {emoji} {stock['name']:8s} ({stock['code']}) "
                    f"当日: {stock['change_pct']:+.2f}% 现价: {stock['price']:.2f}"
                )

            # 保存到concept中
            concept["top_stocks"] = stock_performances[:top_stocks]

        except Exception as e:
            print(f"      分析失败: {e}")
            continue

    # 步骤4: 轮动建议
    print("\n4. 板块轮动分析建议：")
    print("-" * 50)

    # 分类板块
    strong_sectors = [c for c in concept_analysis if c["status"] == "🔥 强势"]
    stable_sectors = [c for c in concept_analysis if c["status"] == "📈 企稳"]
    adjusting_sectors = [c for c in concept_analysis if c["status"] == "📉 调整"]

    if strong_sectors:
        print(f"\n   ✅ 主线热点 ({len(strong_sectors)}个) - 重点关注：")
        for s in strong_sectors:
            print(
                f"      • {s['name']}：累计{s['total_change']:+.2f}%，波动{s['volatility']:.2f}%"
            )

    if stable_sectors:
        print(f"\n   🔄 企稳板块 ({len(stable_sectors)}个) - 适合潜伏：")
        for s in stable_sectors:
            print(
                f"      • {s['name']}：累计{s['total_change']:+.2f}%，{s['up_days']}天上涨"
            )

    if adjusting_sectors:
        print(f"\n   ⏸️  调整板块 ({len(adjusting_sectors)}个) - 暂时观望：")
        for s in adjusting_sectors:
            print(f"      • {s['name']}：累计{s['total_change']:+.2f}%")

    # 轮动建议
    print("\n   💡 轮动策略建议：")
    if len(strong_sectors) >= 3:
        print("      • 主线明确，建议聚焦前排强势板块的核心个股")
        print("      • 关注板块内低位补涨机会")
    elif len(stable_sectors) >= 3:
        print("      • 板块轮动较快，建议分散配置企稳板块")
        print("      • 逢低吸纳趋势向好的板块")
    else:
        print("      • 市场分化，建议控制仓位")
        print("      • 等待主线明确后再重仓参与")

    # 识别潜在轮动方向（修复逻辑）
    if adjusting_sectors:
        # 找跌幅较大但可能企稳的板块（下跌天数 >= 上涨天数，但波动降低说明企稳）
        potential_recovery = [
            a
            for a in adjusting_sectors
            if a["down_days"] >= a["up_days"] and a["volatility"] < 3
        ]
        if potential_recovery:
            print(f"\n   🎯 潜在轮动方向（调整到位）：")
            for p in potential_recovery[:3]:
                print(f"      • {p['name']}：调整后可能反弹")

    return {
        "current_hot": current_hot.to_dict(),
        "concept_analysis": concept_analysis,
        "strong_sectors": strong_sectors,
        "stable_sectors": stable_sectors,
        "adjusting_sectors": adjusting_sectors,
    }


if __name__ == "__main__":
    # 测试运行
    result = analyze_hot_sectors_rotation(days=5, top_concepts=10, top_stocks=5)
