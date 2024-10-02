class IndustryConcept:
    def __init__(self, concept_code, concept_name, change_pct, hot_value, hot_tag):
        self.concept_code = concept_code  # 概念代码
        self.concept_name = concept_name  # 概念名称
        self.change_pct = change_pct  # 涨跌幅（%）
        self.hot_value = hot_value  # 热度值
        self.hot_tag = hot_tag  # 热度标签

    def __str__(self):
        # 定义颜色代码
        RED = "\033[91m"
        GREEN = "\033[92m"
        WHITE = "\033[97m"
        CYAN = "\033[96m"  # 天蓝色
        RESET = "\033[0m"

        # 根据change_pct的值选择颜色
        if self.change_pct > 0:
            color = RED
        elif self.change_pct < 0:
            color = GREEN
        else:
            color = WHITE

        return (
            f"{self.concept_name} (代码: {self.concept_code}) - "
            f"涨跌幅: {color}{self.change_pct}%{RESET}, "
            f"热度值: {CYAN}{self.hot_value}{RESET}, "
            f"热度标签: {self.hot_tag}"
        )

    @classmethod
    def from_dict(cls, data):
        return cls(
            concept_code=data.get("concept_code", ""),
            concept_name=data.get("concept_name", ""),
            change_pct=float(data.get("change_pct", 0)),
            hot_value=float(data.get("hot_value", 0)),
            hot_tag=data.get("hot_tag", ""),
        )

    def to_dict(self):
        return {
            "concept_code": self.concept_code,
            "concept_name": self.concept_name,
            "change_pct": self.change_pct,
            "hot_value": self.hot_value,
            "hot_tag": self.hot_tag,
        }
