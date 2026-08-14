import math

from stocks.util import supports_color


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
    def __init__(self, code, name, price, change_percent, amount, as_of=None):
        self.code = code
        self.name = name
        self.price = price
        self.change_percent = change_percent
        self.amount = amount
        self.as_of = as_of

    def amount_in_billion(self):
        try:
            amount = float(self.amount)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(amount) or amount <= 0:
            return 0.0
        return round(amount / 100000000, 2)

    def colored_change_percent(self):
        try:
            change = float(self.change_percent)
        except (TypeError, ValueError):
            return "--"
        if not math.isfinite(change):
            return "--"
        text = f"{self.change_percent}%"
        if not supports_color():
            return text
        if change > 0:
            return f"\033[91m{text}\033[0m"
        if change < 0:
            return f"\033[92m{text}\033[0m"
        return text

    def price_text(self):
        try:
            price = float(self.price)
        except (TypeError, ValueError):
            return str(self.price) if self.price is not None else "--"
        return f"{price:.2f}" if math.isfinite(price) else "--"

    def __str__(self):
        parts = [f"{self.name} {self.price_text()}", self.colored_change_percent()]
        amount = self.amount_in_billion()
        if amount:
            parts.append(f"{amount}亿")
        return " - ".join(parts)
