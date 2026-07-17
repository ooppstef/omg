class IndustryConcept:
    def __init__(self, concept_code, concept_name, change_pct, hot_value, hot_tag):
        self.concept_code = concept_code
        self.concept_name = concept_name
        self.change_pct = change_pct
        self.hot_value = hot_value
        self.hot_tag = hot_tag

    def __str__(self):
        red, green, white, cyan, reset = (
            "\033[91m",
            "\033[92m",
            "\033[97m",
            "\033[96m",
            "\033[0m",
        )
        if self.change_pct > 0:
            color = red
        elif self.change_pct < 0:
            color = green
        else:
            color = white

        return (
            f"{self.concept_name} (代码: {self.concept_code}) - "
            f"涨跌幅: {color}{self.change_pct}%{reset}, "
            f"热度值: {cyan}{self.hot_value}{reset}, "
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
