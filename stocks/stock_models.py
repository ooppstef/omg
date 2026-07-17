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
        except (TypeError, ValueError):
            return 0.0

    def colored_change_percent(self):
        change = float(self.change_percent)
        if change > 0:
            return f"\033[91m{self.change_percent}%\033[0m"
        if change < 0:
            return f"\033[92m{self.change_percent}%\033[0m"
        return f"{self.change_percent}%"

    def price_text(self):
        try:
            return f"{float(self.price):.2f}"
        except (TypeError, ValueError):
            return str(self.price) if self.price is not None else "--"

    def __str__(self):
        parts = [f"{self.name} {self.price_text()}", self.colored_change_percent()]
        amount = self.amount_in_billion()
        if amount:
            parts.append(f"{amount}亿")
        return " - ".join(parts)
