import akshare as ak
import pandas
import datetime
from typing import List
from models import StockDaily
import os


def daily_data():
    """
    获取、清洗并处理A股所有股票的实时行情数据。
    - 剔除ST、退市、成交额为空的股票。
    - 成交额、市值单位转换为亿。
    """
    stock_zh_a_spot_em_df = ak.stock_zh_a_spot_em()

    # --- 数据清洗 ---
    # 1. 剔除成交额为空的数据
    cleaned_df = stock_zh_a_spot_em_df.dropna(subset=["成交额"])

    # 2. 剔除名称包含ST、*ST、退的股票
    cleaned_df = cleaned_df[~cleaned_df["名称"].str.contains("ST|\*ST|退", na=False)]

    # --- 数据筛选和处理 ---
    columns_to_keep = [
        "序号",
        "代码",
        "名称",
        "最新价",
        "涨跌幅",
        "成交额",
        "振幅",
        "最高",
        "最低",
        "今开",
        "昨收",
        "量比",
        "换手率",
        "总市值",
        "流通市值",
        "60日涨跌幅",
        "年初至今涨跌幅",
    ]

    existing_columns = [col for col in columns_to_keep if col in cleaned_df.columns]
    df = cleaned_df[existing_columns].copy()

    # 将金额相关的列统一转换为亿元单位的浮点数
    amount_columns = ["成交额", "总市值", "流通市值"]
    for col in amount_columns:
        if col in df.columns:
            df.loc[:, col] = (df[col] / 1e8).round(2)

    return df


def to_model_list(df: pandas.DataFrame) -> List[StockDaily]:
    """
    将DataFrame转换为StockDaily模型的列表。
    """
    return df.apply(lambda row: StockDaily.from_pandas_row(row), axis=1).tolist()


def to_csv(df: pandas.DataFrame, directory: str = "."):
    """
    将DataFrame保存为CSV文件。
    - 如果同名文件存在，则先删除。
    - 对金额、涨跌幅等列进行格式化，添加单位。
    """
    current_date = datetime.datetime.now().strftime("%Y%m%d")
    file_path = os.path.join(directory, f"daily_market_data_{current_date}.csv")

    if os.path.exists(file_path):
        os.remove(file_path)
        print(f"已删除旧文件: {file_path}")

    df_to_save = df.copy()

    # 格式化金额列，添加 '亿' 单位
    amount_columns = ["成交额", "总市值", "流通市值"]
    for col in amount_columns:
        if col in df_to_save.columns:
            df_to_save[col] = df_to_save[col].apply(lambda x: f"{x:.2f}亿")

    # 格式化涨跌幅列，添加 '%' 符号
    percent_columns = ["涨跌幅", "60日涨跌幅", "年初至今涨跌幅"]
    for col in percent_columns:
        if col in df_to_save.columns:
            df_to_save[col] = pandas.to_numeric(
                df_to_save[col], errors="coerce"
            ).fillna(0)
            df_to_save[col] = df_to_save[col].apply(lambda x: f"{x}%")

    df_to_save.to_csv(file_path, index=False, encoding="utf-8-sig")
    print(f"数据已成功保存到: {file_path}")


if __name__ == "__main__":
    # 1. 获取并清洗、处理数据
    processed_data_df = daily_data()
    print("已成功获取并清洗数据。预览:")
    print(processed_data_df.head())

    # 2. 将处理后的纯数据转换为模型列表
    stock_models = to_model_list(processed_data_df)
    print("\n已将数据转换为模型列表。预览前3个模型:")
    for model in stock_models[:3]:
        print(model)

    # 3. 将数据保存为CSV（包含金额和涨跌幅的格式化）
    current_dir = os.path.dirname(os.path.abspath(__file__))
    to_csv(processed_data_df, directory=current_dir)
