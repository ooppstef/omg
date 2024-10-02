class StockBase:
    def __init__(self, id, code, name, short, exchange):
        self.id = id
        self.code = code
        self.name = name
        self.short = short
        self.exchange = exchange

    @classmethod
    def init_with_code(cls, code):
        return cls(None, code, "", "", "")

    def __str__(self):
        return f"{self.code} {self.name} {self.short} {self.exchange}"


class StockCurrentPrice:
    def __init__(self, code, name, price, change_percent, amount):
        self.code = code
        self.name = name
        self.price = price
        self.change_percent = change_percent
        self.amount = amount

    def amount_in_billion(self):
        try:
            amount = float(self.amount)
            return round(amount / 100000000, 2)
        except ValueError:
            return 0.0

    def colored_change_percent(self):
        if float(self.change_percent) > 0:
            return f"\033[91m{self.change_percent}%\033[0m"  # 红色
        elif float(self.change_percent) < 0:
            return f"\033[92m{self.change_percent}%\033[0m"  # 绿色
        else:
            return f"{self.change_percent}%"  # 默认白色

    def __str__(self):
        if self.amount_in_billion() == 0.0:
            return f"{self.name} - {self.colored_change_percent()}"
        else:
            return f"{self.name} - {self.colored_change_percent()} - {self.amount_in_billion()}亿"
