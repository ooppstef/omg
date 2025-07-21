from dataclasses import dataclass, fields


@dataclass
class StockDaily:
    """
    用于存储每日A股行情数据的模型。
    """

    id: int
    code: str
    name: str
    latest_price: float
    change_percent: float
    turnover: float
    amplitude: float
    high: float
    low: float
    open_price: float
    previous_close: float
    volume_ratio: float
    turnover_rate: float
    total_market_cap: float
    circulating_market_cap: float
    change_percent_60d: float
    change_percent_ytd: float

    def __repr__(self):
        return f"StockDaily(code={self.code}, name={self.name}, price={self.latest_price}, change%={self.change_percent}%)"

    @classmethod
    def from_pandas_row(cls, row):
        """
        从Pandas DataFrame的一行创建一个StockDaily实例。
        """
        # 中文列名到英文属性名的映射
        rename_map = {
            "序号": "id",
            "代码": "code",
            "名称": "name",
            "最新价": "latest_price",
            "涨跌幅": "change_percent",
            "成交额": "turnover",
            "振幅": "amplitude",
            "最高": "high",
            "最低": "low",
            "今开": "open_price",
            "昨收": "previous_close",
            "量比": "volume_ratio",
            "换手率": "turnover_rate",
            "总市值": "total_market_cap",
            "流通市值": "circulating_market_cap",
            "60日涨跌幅": "change_percent_60d",
            "年初至今涨跌幅": "change_percent_ytd",
        }

        # 创建一个字典，用于传入StockDaily的构造函数
        # 只选择模型中定义的字段，以防DataFrame中有额外的列
        model_fields = {field.name for field in fields(cls)}
        stock_data = {}
        for cn_name, en_name in rename_map.items():
            if en_name in model_fields and cn_name in row:
                stock_data[en_name] = row[cn_name]

        return cls(**stock_data)
