import unittest

from stocks import rank
from stocks.filter import db as filter_db
from stocks.universe import A_SHARE_CODE_PREFIXES


class TestUniverseCompatibility(unittest.TestCase):
    def test_existing_prefix_constants_remain_available(self):
        self.assertIs(rank.CODE_PREFIXES, A_SHARE_CODE_PREFIXES)
        self.assertIs(filter_db.PREFIXES, A_SHARE_CODE_PREFIXES)


if __name__ == "__main__":
    unittest.main()
