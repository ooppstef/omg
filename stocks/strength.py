"""近期强势体检：市场结构（扩散/抱团）× 主题强度 × 个股强度。

用于日复盘的「近期强势」取证：回答“最近走强的是哪些题材、哪些个股、强度多少、
可能是什么原因（市场因素 / 指标 / 量化分位）”。

数据来源与口径（v1）
- 板块与个股的多周期涨幅、量比、换手、成交额、板块内涨跌家数、领涨股，全部来自东财
  clist 单接口（`f127`=3日 / `f109`=5日 / `f160`=10日 / `f24`=60日 / `f104`+`f105`=
  板块内上涨/下跌家数）。该接口不返回交易日，因此用行内 `f124`（行情时间戳，unix 秒）
  反推报价交易日，并据此交易日去取涨停池，避免「旧日数据冒充当日」。
- 涨停池（家数 / 高度 / 首板占比 / 行业集中度 HHI）来自 akshare 东财涨停池。
- 风格与基准（沪深300 的 3/5/10/60 日收益）复用 `stocks.style` 的指数链路；
  失败只降级为「无风格 / 无基准」。
- 个股技术全套（MA/MACD/ADX/rel_vol/close_loc…）复用 `stocks.watch_advice` 的新浪日线
  链路，按候选池限额逐只精算（全市场逐只拉日线不可行）。

为什么以「概念」而不是「行业」当题材单位
- 抱团行情里同一条题材线会被拆进多个行业（军工/低空/机器人 分散在航空装备、地面兵装、
  军工电子、汽车零部、通用设备…），而行业分类又会把 eVTOL 整车与传统汽零归进同一个
  「汽车零部」。因此主题单位取东财概念板块，再用成分股 Jaccard 重叠把高重叠概念
  （低空经济 / 飞行汽车 / 通用航空…）合并成一条主题线。

强度分的含义（重要，勿过度解读）
- `score` 是**同一截面内的分位**（0～100），不是收益率预测、不是概率。它只回答
  “在这批板块/个股里，多周期涨幅 + 宽度 + 量能综合排在什么位置”。
- `regime` / `stage` / `breadth_label` 都是描述性规则标签（阈值见 `_REGIME_RULES`、
  `_STAGE_Z_*`、`_BREADTH_*`），跨日比较请看组件数字，不要只看标签。

判断层（强不等于还能买）
- `themes[].s_risk` / `fade_signals`：主题线是否已现退潮迹象（阶段回落 + 当日转跌 +
  强势成员承接转弱），即「昨天可能已经是 S 点」的风险；只报迹象，不预测后续。
- `stocks[].space`：剩余空间（异动口径近似 + 涨停池连板高度）。偏离异动阈值越近、
  连板越高，越容易被迫「绕异动」——强势归强势，但可能已经没有 b 点。
- `stocks[].pullback`：回落性质（缩量回踩 / 放量调整 / 技术面走坏），**只看技术面结构**。
  逻辑面（基本面 / 消息面 / 催化）本地没有任何数据，必须用**最新**外部信息临时核实——
  会过期的资料不如不用；核实前不得把「回调」当低吸理由（无硬逻辑的强势股回调多为 A 杀）。
- `stocks[].action`：低吸 / 追高 / 不参与，由 regime（大环境）× stage（加速/回落）×
  空间 × 所在主题线 s_risk × 回落性质 规则映射而来；`低吸` = 等回踩确认后介入
  （不代表当前正在回调）；纯描述性规则、非投资建议、不给点位，须连同依据数字一起引用。

底部启动（与「近期强势」互补）
- `bottom`：位置还低（5/10/60 日涨幅在阈值内）+ 当日/3 日转强 + 放量的票；字段形状与 `stocks` 相同
  （含 space / action / pullback / launch），用于回答「已走强的票没 b 点了，刚离底的是谁」。
- 「启动」= `launch` 标志表的加权命中：量能（量能突变 rv20≥2、短周期放量 rel_vol≥1.5、MFI≥60）、
  资金（主力净流入>0 且占比≥3%）、形态（刚站上 MA20 0～12%、贴近/站上 MA60、收在日内高位、
  MACD 柱翻红、均线粘合 ≤3%）各 2 分，价格（大阳 ≥5%）与方向（+DI > −DI）各 1 分（满分 20）；
  它是主排序键，`score`（量价分位）只作粗排与同分次序。

已知口径限制
- 主题成员表取的是「按 5 日涨幅排序的板块首页（≤100 只）」，因此成员统计是**强势成员口径**：
  `strong_member_up_ratio` 不等于板块宽度，板块真实宽度看板块自身的 `breadth`。
- 未能覆盖的候选票不参与个股排名；技术面缺失时该票仍按多周期涨幅 + 量比排名，但会标注缺口。

失败与降级
- 任一阶段失败只记 `errors` 并继续；板块层 / 主题层 / 个股层彼此独立。
- 概念接口失败时主题层为空，个股候选退化为全市场多周期涨幅榜。
"""

from __future__ import annotations

import datetime
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from stocks.universe import A_SHARE_CODE_PREFIXES
from stocks.util import finite_float

# ---------------------------------------------------------------- 标签常量

REGIME_BROAD = "普涨扩散"
REGIME_ROTATION = "板块轮动"
REGIME_HUDDLE = "主题抱团"
REGIME_DEFENSIVE = "权重/红利抱团"
REGIME_DOWN = "普跌无主线"
REGIME_UNKNOWN = "结构不明"

STAGE_LEAD = "领涨"
STAGE_WARM = "升温"
STAGE_COOL = "高位回落"
STAGE_EBB = "退潮"
STAGE_FLAT = "持平"

BREADTH_WIDE = "扩散"
BREADTH_MIXED = "中性"
BREADTH_NARROW = "龙头独拉"

ROLE_LEADER = "主题领涨"
ROLE_FOLLOWER = "主题跟随"
ROLE_SOLO = "非强势题材（独立走强）"

# 板块/个股阶段阈值（robust-z 单位；调阈值需同步更新测试与文档并升版本）
_STAGE_Z_LEAD = 0.5
_STAGE_Z_WARM = 1.0
_STAGE_Z_EBB = -0.5

# 个股阶段阈值（相对沪深300 的超额，单位 pp）
_STOCK_STAGE_LEAD = 5.0
_STOCK_STAGE_WARM = 3.0
_STOCK_STAGE_FADE = 0.0
_STOCK_STAGE_EBB = -5.0

# 市场结构判定规则（经验阈值；调阈值需同步更新测试与文档）
_REGIME_RULES = {
    "down_up_ratio": 0.25,        # 5日上涨概念占比 ≤ 此值
    "down_dispersion": 0.03,      # 且 Top十分位 − 中位数 ≤ 此值（无强势簇）
    "defensive_up_ratio": 0.30,   # 风格偏防御且上涨概念占比 ≤ 此值
    "huddle_narrow": 0.15,        # 龙头独拉板块占比 ≥ 此值
    "huddle_hhi": 0.06,           # 或涨停行业 HHI ≥ 此值
    "huddle_dispersion": 0.06,    # 或 Top十分位 − 中位数 ≥ 此值（pp）
    "huddle_up_ratio": 0.50,      # 且 5日上涨概念占比 ≤ 此值
    "broad_up_ratio": 0.60,       # 5日上涨概念占比 ≥ 此值
    "broad_wide": 0.30,           # 且宽度扩散板块占比 ≥ 此值
}

_DEFENSIVE_STYLES = frozenset({"红利防守", "大盘占优"})

# 非题材概念关键词（子串匹配，统一按大写比较）：
# 昨日回顾型 / 财报事件型 / 资金持仓型 / 指数成分型 / 交易特征型
_NON_THEME_KEYWORDS = tuple(
    token.upper()
    for token in (
        "昨日", "最近多板", "首板", "连板", "涨停", "跌停", "炸板", "触板", "打板", "撬板",
        "预增", "预减", "预盈", "预亏", "扭亏", "业绩", "高送转", "送转",
        "解禁", "增持", "减持", "回购", "股权激励", "定增", "配股", "转债",
        "次新", "新股", "破净", "破发", "低价股", "高价股", "百元股", "壳资源", "ST股",
        "证金", "汇金", "社保重仓", "基金重仓", "QFII", "北向", "陆股通",
        "融资融券", "MSCI", "富时", "标普", "上证50", "沪深300", "中证500", "中证100",
        "深证成指", "科创50", "指数", "高换手", "高振幅", "换手率", "振幅",
    )
)

# ---------------------------------------------------------------- 接口常量

_EM_HOSTS = (
    "push2delay2.eastmoney.com",
    "push2delay.eastmoney.com",
    "push2.eastmoney.com",
)
_EM_PATH = "/api/qt/clist/get"
_HTTP_TIMEOUT = 15

_BOARD_FIELDS = (
    "f12,f13,f14,f2,f3,f6,f8,f10,f24,f25,f104,f105,f109,f124,f127,f128,f136,f140,f160"
)
_STOCK_FIELDS = "f12,f13,f14,f2,f3,f6,f8,f10,f24,f62,f104,f109,f124,f127,f160,f184"

FS_CONCEPT = "m:90+t:3"
FS_INDUSTRY = "m:90+t:2"
FS_SH = "m:1+t:2,m:1+t:23"
FS_SZ = "m:0+t:6,m:0+t:80"

_PAGE_SIZE = 100
_MAX_BOARD_PAGES = 8

DEFAULT_TOP_CONCEPTS = 8
DEFAULT_TOP_INDUSTRIES = 6
DEFAULT_MAX_STOCKS = 12
DEFAULT_MAX_CANDIDATES = 24
DEFAULT_MAX_BOTTOM = 10
_BOTTOM_KLINE_LIMIT = 20  # 先按量价粗排取前 N 只精算日线，再按启动标志排序

MAX_TOP_BOARDS = 30
MAX_STOCKS_LIMIT = 30
MAX_CANDIDATES_LIMIT = 60

# 「刚转强」题材的补充名额：当日 + 3 日转强、但多周期还没走出来的板块，
# 固定小额度，多了会把噪声板块塞进主题线。
_TURN_EXTRA = 3

# 成分股 Jaccard 阈值。实测（2026-10-16）同一条题材线的板块重叠并不高：
# 数字阅读↔大众出版 0.19、AI语料↔大众出版 0.17、AI语料↔知识产权 0.18、航天航空↔通用航空 0.13；
# 而 红利股↔教育出版 只有 0.04（大板块恰好包含小板块成员，属伪相关）。
# 取 0.12：能合并真实同源线，又不吃 0.04 这类伪相关。
_CLUSTER_JACCARD = 0.12
_MIN_THEME_MEMBERS = 3
_BREADTH_WIDE_MIN = 0.60
_BREADTH_NARROW_MAX = 0.40

_LEADER_RANK = 3
_KLINE_LOOKBACK_DAYS = 180

# 底部启动初筛（与「近期强势」互补：已走强的多在异动口径内，可参与的多是刚离底的）
_BOTTOM_SCAN_FID = "f127"  # 按 3 日涨幅枚举全市场（启动是几天的事）
_BOTTOM_SCAN_PAGES = 3  # 每市场 3 页 = 300 行
_BOTTOM_MIN_CHANGE = 0.0  # 当日必须为正
_BOTTOM_MIN_VOL_RATIO = 1.5  # 量比下限：要有资金进场（与 launch 的 rel_vol 阈值一致）
_BOTTOM_MIN_AMOUNT = 5e7  # 成交额下限（流动性）
_BOTTOM_MIN_TURNOVER = 0.5
_BOTTOM_MAX_TURNOVER = 20.0  # 换手上限：更高多为高位换手派发
_BOTTOM_MAX_RET5 = 8.0  # 5/10/60 日涨幅上限 = 「位置还低」
_BOTTOM_MAX_RET10 = 15.0
_BOTTOM_MAX_RET60 = 30.0

# 启动标志阈值（量能 + 形态为主，见 _launch_of）
_LAUNCH_RV20 = 2.0  # 当日量 / 20日均量（量能突变）
_LAUNCH_REL_VOL = 1.5  # 当日量 / 5日均量（短周期放量）
_LAUNCH_MFI = 60.0  # 资金流（MFI）
_LAUNCH_MAIN_INFLOW_PCT = 3.0  # 主力净流入占成交额比（资金）
_LAUNCH_MA20_MAX = 12.0  # 「刚站上 MA20」的上限（超过说明已走了一段）
_LAUNCH_MA60_MIN = -3.0  # 贴近/站上 MA60 的下限（长期均线附近＝底部区域）
_LAUNCH_CLOSE_LOC = 0.7  # 收在日内高位
_LAUNCH_MA_CLUSTER = 3.0  # 均线粘合：MA5/10/20 离散度上限（%）
_LAUNCH_BIG_DAY = 5.0  # 大阳线/涨停确认
_LAUNCH_VOL_POINTS = 2.0  # 量能/资金每条标志的分值
_LAUNCH_SHAPE_POINTS = 2.0  # 形态每条标志的分值

# 主题线「承接转弱」信号的最少强势成员数：3～5 只的小线，1/3 上涨就会假报警
_FADE_MIN_MEMBERS = 10

# 剩余空间（异动口径，近似）：
# 「异常波动」= 连续 3 个交易日累计涨跌幅偏离值达阈值（主板 20%、创业板/科创板 30%）；
# 「严重异常波动」= 连续 10 个交易日累计偏离 ≥ 100%。监管按对应指数（上证综指/创业板指等）
# 计算并含「同向异动次数」等定性判定；这里统一用沪深300 近似，只作剩余空间的量级提示。
_ABNORMAL_DEV_3D_MAIN = 20.0
_ABNORMAL_DEV_3D_GEM = 30.0
_ABNORMAL_DEV_10D = 100.0
_NEAR_ABNORMAL_RATIO = 0.7
_HIGH_STREAK = 5  # 连板高度 ≥5：高度与停牌核查风险显著，追高性价比下降
_BREAKDOWN_CHANGE = -9.0  # 当日跌幅 ≤ 此值（近跌停）：直接判技术面走坏
_GEM_PREFIXES = ("300", "301", "302", "688")

# 回落性质（纯技术面/量价；逻辑面须用最新外部信息核实，见 _NOTES）
_PULLBACK_DROP = -3.0  # 当日跌幅门槛：视为「回落」
_PULLBACK_VOL_HIGH = 1.2  # 放量门槛（rel_vol）
_PULLBACK_CLOSE_LOW = 0.2  # 收在日内低位阈值（close_loc）

PULLBACK_SHRINK = "缩量回踩"
PULLBACK_MIXED = "放量调整"
PULLBACK_BROKEN = "技术面走坏"

SPACE_ROOMY = "空间充足"
SPACE_NARROW = "空间偏紧"
SPACE_TIGHT = "空间受限"

ACTION_CHASE = "追高"
ACTION_DIP = "低吸"
ACTION_SKIP = "不参与"

# 板块强度权重（robust-z 分量相对权重；_weighted_z 按在场键归一，不要求和恰好为 1）
_BOARD_WEIGHTS = (
    ("ret5", 0.30),
    ("ret10", 0.25),
    ("ret3", 0.15),
    ("ret60", 0.15),
    ("breadth", 0.10),
    ("vol_ratio", 0.05),
)

# 候选粗筛权重（未叠加技术面时的多周期量价口径；历史和为 1.2，只表达相对重要性）
_CANDIDATE_WEIGHTS = (
    ("ret5", 0.40),
    ("ret10", 0.30),
    ("ret3", 0.15),
    ("ret60", 0.15),
    ("vol_ratio", 0.20),
)

# 个股强度权重（相对沪深300 超额 + 量能 + 形态；量能与形态合计 28%）
_STOCK_WEIGHTS = (
    ("ex5", 0.28),
    ("ex10", 0.22),
    ("ex3", 0.14),
    ("ex60", 0.08),
    ("volume", 0.20),
    ("ma20", 0.08),
)

# 底部启动粗排权重（无日线时用；量能优先，供"先取前 N 只精算"）
_BOTTOM_WEIGHTS = (
    ("vol_ratio", 0.45),
    ("change_pct", 0.30),
    ("ret3", 0.25),
)

_BENCHMARK_HORIZONS = (3, 5, 10, 60)

_TZ = ZoneInfo("Asia/Shanghai")

_NOTES = (
    "score 为同一截面内的分位（0～100），不是收益预测或概率。",
    "板块/个股多周期涨幅来自东财 clist；报价交易日由行情时间戳 f124 推导，已随 payload 输出。",
    f"主题线由高重叠概念（成分股 Jaccard ≥ {_CLUSTER_JACCARD}）合并而来，可跨行业；行业层仅作宽度/量能参照。",
    "主题成员统计为「强势成员口径」（板块按 5 日涨幅排序的首页 ≤100 只），板块真实宽度看 boards 里的 breadth。",
    "簇内排名/领涨股按 5日×0.5 + 3日×0.3 + 当日×0.2 排序，所以「今日涨停但 5 日未走出」也算簇内领涨。",
    "板块 stage 用截面 robust-z（板块之间比强弱）；个股 stage 用相对沪深300 的绝对超额 pp（判形态），个股排名看 score 分位。",
    "个股技术面来自新浪日线（按候选池限额逐只精算），未覆盖的票不参与排名。",
    "概念层已剔除伪概念（昨日回顾/财报事件/资金持仓/指数成分等），行业层做同名层级去重。",
    "主题线＝概念强度 TopN ∪ 行业强度 TopM ∪ 启动分 TopK，再按成分股 Jaccard 合并；概念板块成员口径宽会摊薄涨幅，窄口径行业更贴近真实主线。",
    "themes[].s_risk / fade_signals 是主题线退潮迹象（阶段回落/当日转跌/承接转弱）的计数等级，只提示「可能在 S 点区域」，不预测后续。",
    "stocks[].space 用沪深300 近似异动偏离（主板 20%、创业板/科创板 30%，10 日严重异常波动 ≈100%），并含涨停池连板高度；为量级提示，非监管口径。",
    "stocks[].action（低吸/追高/不参与）是 regime × stage × 空间 × 所在线 s_risk × 回落性质 的规则映射，非投资建议，不给点位；"
    "低吸 = 「等回踩确认后介入」，不代表当前正在回调；action_notes 为空 = 默认口径（未退潮、空间未受限、环境未扩散 → 等回踩低吸）。",
    "stocks[].pullback 只判技术面性质（缩量回踩 / 放量调整 / 技术面走坏），逻辑面（基本面/消息面/催化）本地无数据，"
    "必须用最新外部信息临时核实（会过期的资料不可用）；核实前不得把回调当低吸理由，无硬逻辑的强势股回调按 A 杀风险对待。",
    "bottom[] 是底部启动榜（与「近期强势」互补）：位置还低（5/10/60 日涨幅未超阈值）+ 当日/3 日转强 + 放量，"
    "按 bottom[].launch.points 的启动标志加权分排序（满分 20）——量能 3 条（rv20≥2 / rel_vol≥1.5 / MFI≥60）、"
    "资金 1 条（主力净流入>0 且占比≥3%）、形态 5 条（刚站上 MA20 0~12% / 贴近站上 MA60 / 收在日内高位 / MACD 柱翻红 / 均线粘合≤3%）各 2 分，"
    "价格（大阳≥5%）与方向（+DI>−DI）各 1 分；bottom[].score 是量价启动分位（粗排与同分次序，与 stocks[].score 的强势分位口径不同）；launch 仅底部榜有值。",
    "stocks[].main_inflow / main_inflow_pct 是东财「当日主力净流入（元）/ 净占比（%）」，只覆盖个股；"
    "行业资金、龙虎榜、北向未纳入（体系缺口，需另用 tool / web_search）。",
)

# ---------------------------------------------------------------- 会话

_SESSION_LOCAL = threading.local()
_HOST_STATE: dict[str, str] = {"preferred": _EM_HOSTS[0]}


def _session():
    """每个工作线程独立 Session，禁用环境代理（东财 delay 主机可直连）。"""
    import requests

    session = getattr(_SESSION_LOCAL, "session", None)
    if session is None:
        s = requests.Session()
        s.trust_env = False
        s.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/",
            }
        )
        _SESSION_LOCAL.session = s
        session = s
    return session


def _host_order() -> list:
    preferred = _HOST_STATE.get("preferred")
    if preferred in _EM_HOSTS:
        return [preferred] + [h for h in _EM_HOSTS if h != preferred]
    return list(_EM_HOSTS)


def _normalize_em_diff(diff) -> list:
    """东财 clist 的 diff 可能是 list，也可能是 {"0": {...}}。"""
    if not diff:
        return []
    if isinstance(diff, dict):
        return list(diff.values())
    if isinstance(diff, list):
        return diff
    return []


def _em_page(
    fs: str,
    *,
    fields: str,
    fid: str = "f12",
    pn: int = 1,
    pz: int = _PAGE_SIZE,
    attempts: int = 2,
) -> tuple[list, Optional[int]]:
    """拉取东财 clist 单页；多主机回退，成功后记住可用主机。

    分页排序键固定用代码（fid=f12）：用涨跌幅等会变动的字段排序时，
    翻页途中重排会导致重复/漏行。
    """
    last_error = None
    for host in _host_order():
        for attempt in range(attempts):
            try:
                resp = _session().get(
                    f"https://{host}{_EM_PATH}",
                    params={
                        "pn": pn,
                        "pz": pz,
                        "po": 1,
                        "np": 1,
                        "fltt": 2,
                        "invt": 2,
                        "fid": fid,
                        "fs": fs,
                        "fields": fields,
                    },
                    timeout=_HTTP_TIMEOUT,
                )
                resp.raise_for_status()
                payload = (resp.json() or {}).get("data")
                if payload is None:
                    raise RuntimeError("clist 无 data")
                total = payload.get("total")
                try:
                    total = int(total)
                except (TypeError, ValueError):
                    total = None
                _HOST_STATE["preferred"] = host
                return _normalize_em_diff(payload.get("diff")), total
            except Exception as exc:
                last_error = f"{host}:{type(exc).__name__}"
                time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"东财 clist 拉取失败(fs={fs} pn={pn}): {last_error}")


def _paged_rows(fs: str, *, fields: str, fid: str = "f12", max_pages: int) -> list:
    """按代码分页枚举全部行（按代码去重；到 total 或页数上限即停）。"""
    rows: list = []
    seen: set = set()
    expected_total: Optional[int] = None
    for pn in range(1, max_pages + 1):
        page, total = _em_page(fs, fields=fields, fid=fid, pn=pn)
        if expected_total is None:
            expected_total = total
        if not page:
            break
        for item in page:
            if not isinstance(item, dict):
                continue
            key = str(item.get("f12") or "").strip()
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            rows.append(item)
        if expected_total is not None and len(rows) >= expected_total:
            break
        if len(page) < _PAGE_SIZE:
            break
    return rows


def fetch_board_rows(kind: str) -> list:
    """全部行业（约 500 个，含二三级）或概念（约 500 个）板块行。"""
    if kind not in ("concept", "industry"):
        raise ValueError("kind 必须是 concept 或 industry")
    fs = FS_CONCEPT if kind == "concept" else FS_INDUSTRY
    return _paged_rows(fs, fields=_BOARD_FIELDS, fid="f12", max_pages=_MAX_BOARD_PAGES)


def fetch_board_members(board_code: str) -> list:
    """板块成分股首页（按 5 日涨幅降序 = 该板块最强的一批成员）。"""
    key = str(board_code).strip().upper()
    if not key:
        return []
    rows, _total = _em_page(f"b:{key}", fields=_STOCK_FIELDS, fid="f109", pn=1)
    return [r for r in rows if isinstance(r, dict)]


def fetch_market_scan(
    errors: Optional[list] = None,
    *,
    fid: str = _BOTTOM_SCAN_FID,
    pages: int = _BOTTOM_SCAN_PAGES,
) -> list:
    """全市场按指定字段降序枚举前 pages×100 行（两市），用于「底部启动」扫描。

    按涨跌幅类字段翻页，盘中重排可能**漏行**（收盘后稳定）；按代码去重防重复。
    页失败降级（返回已取行）并记 errors。
    """
    rows: list = []
    seen: set = set()
    for fs in (FS_SH, FS_SZ):
        for pn in range(1, pages + 1):
            try:
                page, _total = _em_page(fs, fields=_STOCK_FIELDS, fid=fid, pn=pn)
            except Exception as exc:
                if errors is not None:
                    errors.append(f"bottom_scan:{fs}:{pn}:{type(exc).__name__}")
                break
            for item in page:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("f12") or "").strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                rows.append(item)
    return rows


def fetch_market_top(errors: Optional[list] = None) -> list:
    """全市场多周期涨幅榜首页（5日 + 10日 各取两市），用于兜底候选；页失败降级并记 errors。"""
    rows: list = []
    seen: set = set()
    for fs in (FS_SH, FS_SZ):
        for fid in ("f109", "f160"):
            try:
                page, _total = _em_page(fs, fields=_STOCK_FIELDS, fid=fid, pn=1)
            except Exception as exc:
                if errors is not None:
                    errors.append(f"market_top:{fs}:{fid}:{type(exc).__name__}")
                continue
            for item in page:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("f12") or "").strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                rows.append(item)
    return rows


# ---------------------------------------------------------------- 基础工具


def _median(values: list) -> Optional[float]:
    parsed = [v for v in (finite_float(x) for x in values) if v is not None]
    if not parsed:
        return None
    parsed.sort()
    mid = len(parsed) // 2
    if len(parsed) % 2:
        return parsed[mid]
    return (parsed[mid - 1] + parsed[mid]) / 2.0


def _mean(values: list) -> Optional[float]:
    parsed = [v for v in (finite_float(x) for x in values) if v is not None]
    if not parsed:
        return None
    return sum(parsed) / len(parsed)


def _share(values: list, predicate) -> Optional[float]:
    parsed = [v for v in (finite_float(x) for x in values) if v is not None]
    if not parsed:
        return None
    return sum(1 for v in parsed if predicate(v)) / len(parsed)


def _robust_z(series: pd.Series) -> pd.Series:
    """中位数/MAD 标准化，抗极值；样本不足或无离散度时返回 0。"""
    values = pd.to_numeric(series, errors="coerce").astype(float)
    if int(values.notna().sum()) < 3:
        return pd.Series(0.0, index=values.index)
    median = float(values.median())
    mad = float((values - median).abs().median())
    scale = 1.4826 * mad
    if not math.isfinite(scale) or scale <= 1e-9:
        std = float(values.std(ddof=0))
        if not math.isfinite(std) or std <= 1e-9:
            return pd.Series(0.0, index=values.index)
        scale = std
    return ((values - median) / scale).fillna(0.0).clip(-4.0, 4.0)


def _percentile_score(series: pd.Series) -> pd.Series:
    """截面分位 0～100（(rank-0.5)/n，避免首名恒等于 100）。"""
    total = int(pd.to_numeric(series, errors="coerce").notna().sum())
    if total <= 0:
        return pd.Series([None] * len(series), index=series.index, dtype=float)
    ranks = pd.to_numeric(series, errors="coerce").rank(method="average")
    return ((ranks - 0.5) / total * 100.0).round(1)


def _round(value, digits: int = 2) -> Optional[float]:
    number = finite_float(value)
    if number is None:
        return None
    return round(number, digits)


def _weighted_z(frame: pd.DataFrame, weights) -> pd.Series:
    """按相对权重合成 robust-z 分量；用**在场键的权重和**归一。

    各组权重因此无需恰好和为 1（`_CANDIDATE_WEIGHTS` 历史上和为 1.2，排序不受影响，
    但缺列时降权口径不可比）；同帧全体等比缩放，**排序保持不变**。
    """
    composite = pd.Series(0.0, index=frame.index)
    total = 0.0
    for key, weight in weights:
        if key in frame.columns:
            composite = composite + _robust_z(frame[key]) * weight
            total += weight
    if total > 0:
        composite = composite / total
    return composite


def _is_a_share(code: str) -> bool:
    c = str(code or "").strip()
    return bool(c) and c.startswith(A_SHARE_CODE_PREFIXES)


def _is_new_listing(name: str) -> bool:
    """新股/次新（N/C 前缀 + 中文名）：上市不足 5 日，多周期涨幅无意义。"""
    return bool(re.match(r"^[NC][\u4e00-\u9fff]", str(name or "").strip()))


def _is_theme_concept(name: str) -> bool:
    """剔除「伪概念」：昨日回顾、财报事件、资金属性、指数成分等不是题材线。

    这些板块常年在强度榜顶部（如「昨日连板」永远是上一轮涨停股的复盘），
    留着会把真正的题材线挤掉，并污染市场结构指标。
    """
    text = str(name or "").strip()
    if not text:
        return False
    upper = text.upper()
    if upper.startswith("ST") or upper.startswith("*ST"):
        return False
    return not any(token in upper for token in _NON_THEME_KEYWORDS)


def _industry_key(name: str) -> str:
    """行业名归一化：去掉 「Ⅱ/Ⅲ」层级后缀（地面兵装Ⅱ → 地面兵装）。"""
    return re.sub(r"[ⅠⅡⅢⅣⅤ\s]+$", "", str(name or "").strip())


def _dedupe_industry_lines(board_scores: list) -> list:
    """行业去重：归一化后名字互为子串视为同一类别的不同层级（出版 ⊂ 大众出版），每行只留分位最高的一个。"""
    kept_keys: list[str] = []
    out: list = []
    for board in board_scores:
        key = _industry_key(board.get("name"))
        if not key:
            continue
        if any(key in other or other in key for other in kept_keys):
            continue
        kept_keys.append(key)
        out.append(board)
    return out


def _is_st(name: str) -> bool:
    """风险股不进候选与榜：ST / *ST / SST 与退市整理（含「退」）。"""
    text = str(name or "").upper()
    return "ST" in text or "退" in text


# 屏蔽名单：概念名含这些关键词的板块 → 其成员不进个股榜，板块也不作主题线。
# 红利/权重防守不是题材线，也不是「启动」的目标；需要时可在此追加（如 "高股息"、"中特估"）。
_EXCLUDE_CONCEPT_KEYWORDS = ("红利",)


def _blocked_concept_name(name: str) -> bool:
    text = str(name or "")
    return bool(text) and any(key in text for key in _EXCLUDE_CONCEPT_KEYWORDS)


def _stage_of(z3, z5, z10) -> str:
    if z5 is None or z10 is None:
        return STAGE_FLAT
    if z5 > _STAGE_Z_LEAD and z10 > _STAGE_Z_LEAD:
        return STAGE_LEAD if (z3 is not None and z3 > _STAGE_Z_LEAD) else STAGE_COOL
    if z3 is not None and z3 > _STAGE_Z_WARM and z5 > -_STAGE_Z_LEAD:
        return STAGE_WARM
    if z5 < _STAGE_Z_EBB and z10 < _STAGE_Z_EBB:
        return STAGE_EBB
    return STAGE_FLAT


def _stock_stage(ex3, ex5, ex10) -> str:
    """个股阶段：用「相对沪深300 的超额 pp」绝对值判定形态，与 `score`（同类分位）互补。

    板块层用截面 z（板块之间比），个股层用绝对 pp（相对大盘），两者不可直接互比。
    """
    z3 = finite_float(ex3)
    z5 = finite_float(ex5)
    z10 = finite_float(ex10)
    if z5 is None and z10 is None:
        return STAGE_FLAT
    base5 = z5 if z5 is not None else z10
    base10 = z10 if z10 is not None else z5
    if (
        base5 is not None
        and base10 is not None
        and base5 <= _STOCK_STAGE_EBB
        and base10 <= _STOCK_STAGE_EBB
    ):
        return STAGE_EBB
    if (
        base5 is not None
        and base10 is not None
        and base5 >= _STOCK_STAGE_LEAD
        and base10 >= _STOCK_STAGE_LEAD
    ):
        if z3 is not None and z3 < _STOCK_STAGE_FADE:
            return STAGE_COOL
        return STAGE_LEAD
    if z3 is not None and z3 >= _STOCK_STAGE_WARM and (z5 is None or z5 >= 0):
        return STAGE_WARM
    return STAGE_FLAT


def _is_gem(code: str) -> bool:
    """创业板/科创板（20cm）：异动阈值 30%，与主板 20% 区分开。"""
    return str(code or "").startswith(_GEM_PREFIXES)


def _space_of(code: str, *, streak: int = 0, excess3=None, excess10=None) -> dict:
    """剩余空间：偏离异动阈值越近、连板越高，越容易「被迫绕异动」→ 没 b 点。"""
    gem = _is_gem(code)
    limit_pct = 20.0 if gem else 10.0
    dev_limit = _ABNORMAL_DEV_3D_GEM if gem else _ABNORMAL_DEV_3D_MAIN
    dev3 = finite_float(excess3)
    dev10 = finite_float(excess10)
    parts: list[str] = []
    if dev3 is not None:
        board = "创业板/科创板" if gem else "主板"
        parts.append(f"3日偏离 {dev3:+.1f}pp（{board}异动阈值 ≈{dev_limit:.0f}%）")
    if dev10 is not None:
        parts.append(f"10日 {dev10:+.1f}pp（严重异动 ≈100%，双创口径）")
    notes: list[str] = ["，".join(parts)] if parts else []
    if streak >= 1:
        notes.append(f"涨停池连板 {streak} 板")
    if (dev3 is not None and dev3 >= dev_limit) or (
        dev10 is not None and dev10 >= _ABNORMAL_DEV_10D
    ):
        label = SPACE_TIGHT
        notes.append(
            "已达异动口径：继续走强易触发核查或被迫「绕异动」，追高空间被压缩"
            "（换手率类异动标准未纳入）"
        )
    elif (
        dev3 is not None and dev3 >= dev_limit * _NEAR_ABNORMAL_RATIO
    ) or streak >= _HIGH_STREAK:
        label = SPACE_NARROW
        notes.append("接近异动口径或连板高度偏高：剩余空间有限，更适合等回踩而非追高")
    else:
        label = SPACE_ROOMY
    return {
        "label": label,
        "limit_pct": limit_pct,
        "streak": int(streak),
        "notes": notes,
    }


def _pullback_of(change_pct, ret3, tech: Optional[dict]) -> Optional[dict]:
    """回落性质（纯技术面/量价）：缩量回踩 / 放量调整 / 技术面走坏。

    只回答「结构有没有坏」，不回答「为什么跌、逻辑还在不在」——后者必须用
    **最新**消息面/基本面/公告去核实（本模块无基本面数据，过期资料不可用）。
    """
    change = finite_float(change_pct)
    r3 = finite_float(ret3)
    if change is None or (change >= 0 and (r3 is None or r3 >= 0)):
        return None
    tech = tech or {}
    rel_vol = finite_float(tech.get("rel_vol"))
    stance = tech.get("vol_stance")
    ma5 = finite_float(tech.get("vs_ma5_pct"))
    close_loc = finite_float(tech.get("close_loc"))
    notes = [f"当日 {change:+.2f}%"]
    if rel_vol is not None:
        notes.append(f"rel_vol {rel_vol:.2f}" + (f"（{stance}）" if stance else ""))
    if ma5 is not None:
        notes.append(f"距 MA5 {ma5:+.1f}%")
    if close_loc is not None and close_loc <= _PULLBACK_CLOSE_LOW:
        notes.append(f"收在日内低位（{close_loc * 100:.0f}%）")

    broken = change <= _BREAKDOWN_CHANGE
    if ma5 is not None and ma5 < 0:
        broken = True
    if (
        rel_vol is not None
        and rel_vol >= _PULLBACK_VOL_HIGH
        and change <= _PULLBACK_DROP
    ):
        broken = True
    if broken:
        state = PULLBACK_BROKEN
        notes.append("技术面走坏（放量下跌/跌破 MA5/近跌停）：先看次日承接，不按低吸处理")
    elif rel_vol is None and ma5 is None:
        state = PULLBACK_MIXED
        notes.append("量能与均线缺口：回落性质无法判定（未取到日线）")
    elif (rel_vol is None or rel_vol < _PULLBACK_VOL_HIGH) and (ma5 is None or ma5 > 0):
        state = PULLBACK_SHRINK
        notes.append("缩量回踩且仍在 MA5 上方（结构未坏）；逻辑面仍需最新信息核实后再定")
    else:
        state = PULLBACK_MIXED
        notes.append("放量调整/分歧：方向未定，等确认")
    return {"state": state, "notes": notes}


def _action_for(stage, space, regime, theme_risk=None, pullback=None) -> tuple:
    """低吸 / 追高 / 不参与：按 regime（大环境）× stage（加速/回落）× 剩余空间 × 所在线退潮 × 回落性质 的映射。

    `低吸` 是「等回踩确认后介入」的口径，不代表该票当前正在回调；只回该 action 相对输入的
    **新增**信息（默认低吸不追加说明）。纯技术面/结构规则，逻辑面须外部最新核实；非投资建议、不给点位。
    """
    label = str((space or {}).get("label") or "")
    regime = regime or REGIME_UNKNOWN
    if regime == REGIME_DOWN:
        return ACTION_SKIP, ["环境：普跌无主线（5日上涨概念占比 ≤25% 且无强势簇）"]
    if stage == STAGE_EBB:
        return ACTION_SKIP, ["个股：阶段退潮（5/10 日相对超额 ≤ −5pp）"]
    if (pullback or {}).get("state") == PULLBACK_BROKEN:
        return ACTION_SKIP, ["个股：技术面走坏（放量下跌/跌破 MA5）→ 先看承接；逻辑面另需最新信息核实"]
    if label == SPACE_TIGHT:
        return ACTION_SKIP, ["空间受限：已在异动口径内 → 无 b 点（详见 space.notes）"]
    if theme_risk == "高" and stage == STAGE_LEAD:
        return ACTION_DIP, ["所属主题线已现退潮迹象（s_risk 高、可能是昨日 S 点）→ 不追高"]
    if regime == REGIME_BROAD and stage == STAGE_LEAD and label == SPACE_ROOMY:
        return ACTION_CHASE, ["环境扩散 + 阶段领涨 + 空间充足，扩张期可执行追高"]
    if stage == STAGE_COOL:
        return ACTION_DIP, ["个股：3 日相对超额转负（高位回落，昨日可能已是 S 点）"]
    return ACTION_DIP, []


def _breadth_label(breadth: Optional[float]) -> Optional[str]:
    value = finite_float(breadth)
    if value is None:
        return None
    if value >= _BREADTH_WIDE_MIN:
        return BREADTH_WIDE
    if value <= _BREADTH_NARROW_MAX:
        return BREADTH_NARROW
    return BREADTH_MIXED


def _momentum_key(record: dict) -> float:
    """簇内排序键：5日×0.5 + 3日×0.3 + 当日×0.2（当日涨幅权重小，避免只看一天）。"""
    parts = [
        (finite_float(record.get("ret5")), 0.5),
        (finite_float(record.get("ret3")), 0.3),
        (finite_float(record.get("change_pct")), 0.2),
    ]
    if all(value is None for value, _weight in parts):
        return -999.0
    return sum((value or 0.0) * weight for value, weight in parts)


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


# ---------------------------------------------------------------- 记录归一化


def board_record(row: dict) -> Optional[dict]:
    """东财板块行（原始 f 字段 dict）→ 归一化记录。"""
    if not isinstance(row, dict):
        return None
    code = str(row.get("f12") or "").strip()
    name = str(row.get("f14") or "").strip()
    if not code or not name:
        return None
    up_count = finite_float(row.get("f104"))
    down_count = finite_float(row.get("f105"))
    breadth = None
    if up_count is not None and down_count is not None and (up_count + down_count) > 0:
        breadth = up_count / (up_count + down_count)
    return {
        "code": code,
        "name": name,
        "change_pct": finite_float(row.get("f3")),
        "ret3": finite_float(row.get("f127")),
        "ret5": finite_float(row.get("f109")),
        "ret10": finite_float(row.get("f160")),
        "ret60": finite_float(row.get("f24")),
        "ytd": finite_float(row.get("f25")),
        "amount": finite_float(row.get("f6")),
        "turnover": finite_float(row.get("f8")),
        "vol_ratio": finite_float(row.get("f10")),
        "up_count": up_count,
        "down_count": down_count,
        "breadth": breadth,
        "leader": str(row.get("f128") or "").strip() or None,
        "leader_code": str(row.get("f140") or "").strip() or None,
        "leader_pct": finite_float(row.get("f136")),
        "quote_ts": finite_float(row.get("f124")),
    }


def stock_record(row: dict) -> Optional[dict]:
    """东财个股行（原始 f 字段 dict）→ 归一化记录。"""
    if not isinstance(row, dict):
        return None
    code = str(row.get("f12") or "").strip()
    name = str(row.get("f14") or "").strip()
    if not code or not name:
        return None
    return {
        "code": code,
        "name": name,
        "price": finite_float(row.get("f2")),
        "change_pct": finite_float(row.get("f3")),
        "ret3": finite_float(row.get("f127")),
        "ret5": finite_float(row.get("f109")),
        "ret10": finite_float(row.get("f160")),
        "ret60": finite_float(row.get("f24")),
        "amount": finite_float(row.get("f6")),
        "turnover": finite_float(row.get("f8")),
        "vol_ratio": finite_float(row.get("f10")),
        "main_inflow": finite_float(row.get("f62")),
        "main_inflow_pct": finite_float(row.get("f184")),
        "quote_ts": finite_float(row.get("f124")),
    }


def _records(rows, mapper) -> list:
    out = []
    for row in rows or []:
        item = mapper(row)
        if item is not None:
            out.append(item)
    return out


def derive_quote_date(records: list) -> tuple[Optional[str], Optional[str]]:
    """从 f124 行情时间戳反推报价交易日（众数）。返回 (date, iso timestamp)。"""
    stamps = [int(r["quote_ts"]) for r in records if finite_float(r.get("quote_ts"))]
    stamps = [value for value in stamps if value >= 1_000_000_000]
    if not stamps:
        return None, None
    counts: dict[str, int] = {}
    for value in stamps:
        day = datetime.datetime.fromtimestamp(value, tz=_TZ).date().isoformat()
        counts[day] = counts.get(day, 0) + 1
    day = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    same_day = [
        value
        for value in stamps
        if datetime.datetime.fromtimestamp(value, tz=_TZ).date().isoformat() == day
    ]
    moment = max(same_day)
    return day, datetime.datetime.fromtimestamp(moment, tz=_TZ).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 板块强度


def score_boards(records: list) -> list:
    """给板块记录加 robust-z 分量、强度分位（0～100）、阶段与宽度标签。"""
    if not records:
        return []
    frame = pd.DataFrame(records)
    composite = pd.Series(0.0, index=frame.index)
    for key, weight in _BOARD_WEIGHTS:
        if key not in frame.columns:
            continue
        z = _robust_z(frame[key])
        frame[f"z_{key}"] = z
        composite = composite + z * weight
    frame["composite"] = composite
    frame["score"] = _percentile_score(frame["composite"])
    # 启动分：当日 + 3 日 各半，用于捞「刚转强」的题材（与多周期强度分并列，不混算）
    frame["turn_score"] = _percentile_score(
        _robust_z(frame["change_pct"]) * 0.5 + _robust_z(frame["ret3"]) * 0.5
    )

    rounded = (
        "change_pct",
        "ret3",
        "ret5",
        "ret10",
        "ret60",
        "ytd",
        "amount",
        "turnover",
        "vol_ratio",
        "up_count",
        "down_count",
        "breadth",
        "leader_pct",
    )
    out: list = []
    for _, row in frame.iterrows():
        item = dict(row)
        item.pop("composite", None)
        for key in rounded:
            item[key] = _round(item.get(key))
        for key, _weight in _BOARD_WEIGHTS:
            item[f"z_{key}"] = _round(item.get(f"z_{key}"), 2)
        item["stage"] = _stage_of(
            item.get("z_ret3"), item.get("z_ret5"), item.get("z_ret10")
        )
        item["breadth_label"] = _breadth_label(item.get("breadth"))
        item["score"] = _round(item.get("score"), 1)
        item["turn_score"] = _round(item.get("turn_score"), 1)
        item["picked_by"] = None
        item["quote_ts"] = None
        out.append(item)
    out.sort(key=lambda r: (-(finite_float(r.get("score")) or 0.0), str(r.get("code"))))
    return out


def board_attribution(board: dict) -> list:
    """板块/主题的确定性归因串（每条都挂在可回溯的数字上）。"""
    lines = []
    ret5 = finite_float(board.get("ret5"))
    ret10 = finite_float(board.get("ret10"))
    if ret5 is not None:
        line = f"5日 {ret5:+.2f}%（强度分位 {board.get('score')}）"
        if ret10 is not None:
            line += f"，10日 {ret10:+.2f}%"
        lines.append(line)
    breadth = finite_float(board.get("breadth"))
    if breadth is not None:
        up = finite_float(board.get("up_count")) or 0.0
        down = finite_float(board.get("down_count")) or 0.0
        lines.append(
            f"板块内上涨 {int(up)}/{int(up + down)}（宽度 {breadth * 100:.0f}%，"
            f"{board.get('breadth_label')}）"
        )
    vol_ratio = finite_float(board.get("vol_ratio"))
    if vol_ratio is not None:
        lines.append(f"板块量比 {vol_ratio:.2f}")
    leader = board.get("leader")
    if leader:
        leader_pct = finite_float(board.get("leader_pct"))
        line = f"领涨股 {leader}"
        if leader_pct is not None:
            line += f" {leader_pct:+.2f}%"
        lines.append(line)
    if board.get("stage"):
        lines.append(f"阶段 {board['stage']}")
    return lines


# ---------------------------------------------------------------- 市场结构


def _sentiment_from_zt(zt_rows: list) -> dict:
    """涨停池 → 家数 / 高度 / 首板占比 / 行业集中度（HHI）。"""
    rows = [r for r in (zt_rows or []) if isinstance(r, dict)]
    limit_up = len(rows)
    if not limit_up:
        return {"ok": False, "limit_up": 0}

    industry_counts: dict[str, int] = {}
    boards: list[int] = []
    first_boards = 0
    for row in rows:
        industry = str(row.get("所属行业") or "").strip()
        if industry:
            industry_counts[industry] = industry_counts.get(industry, 0) + 1
        value = finite_float(row.get("连板数"))
        if value is not None and value >= 1 and float(value).is_integer():
            boards.append(int(value))
            if int(value) == 1:
                first_boards += 1

    hhi = max_industry = max_share = top3_share = None
    if industry_counts:
        total = sum(industry_counts.values())
        hhi = sum((count / total) ** 2 for count in industry_counts.values())
        max_industry = max(industry_counts, key=industry_counts.get)
        max_share = industry_counts[max_industry] / total
        top3 = sorted(industry_counts.values(), reverse=True)[:3]
        top3_share = sum(top3) / total

    return {
        "ok": True,
        "limit_up": limit_up,
        "height": max(boards) if boards else None,
        "first_board_ratio": _round(first_boards / len(boards), 3) if boards else None,
        "industry_hhi": _round(hhi, 4),
        "max_industry": max_industry,
        "max_industry_share": _round(max_share, 3),
        "top3_industry_share": _round(top3_share, 3),
        "industries": len(industry_counts),
        "industry_counts": dict(sorted(industry_counts.items(), key=lambda kv: -kv[1])[:8]),
    }


def compute_regime(
    concept_scores: list,
    *,
    sentiment: Optional[dict] = None,
    style_state: Optional[str] = None,
) -> dict:
    """概念板块截面 + 涨停池 + 风格 → 市场结构标签（描述性规则，非预测）。"""
    ret5 = [r.get("ret5") for r in (concept_scores or []) if r.get("ret5") is not None]
    metrics: dict[str, Any] = {"n_concepts": len(ret5)}
    if len(ret5) < 20:
        return {
            "regime": REGIME_UNKNOWN,
            "confidence": "低",
            "reasons": ["概念板块样本不足（<20），不做结构判定"],
            "metrics": metrics,
        }

    concepts = concept_scores or []
    ordered = sorted(ret5, reverse=True)
    top_n = max(3, len(ordered) // 10)
    up_ratio = _share(ret5, lambda v: v > 0)
    median5 = _median(ret5)
    top_decile = _mean(ordered[:top_n])
    dispersion = (
        top_decile - median5 if top_decile is not None and median5 is not None else None
    )
    wide_share = (
        sum(
            1
            for r in concepts
            if (finite_float(r.get("breadth")) or 0.0) >= _BREADTH_WIDE_MIN
            and (finite_float(r.get("ret5")) or 0.0) > 0
        )
        / len(concepts)
    )
    # 「龙头独拉」= 当日上涨、但自身宽度仍窄的板块，占当日上涨板块的比例
    up_today = [
        r for r in concepts if (finite_float(r.get("change_pct")) or 0.0) > 0
    ]
    narrow_share = (
        sum(
            1
            for r in up_today
            if (finite_float(r.get("breadth")) or 1.0) <= _BREADTH_NARROW_MAX
        )
        / max(1, len(up_today))
    )

    sent = sentiment or {}
    hhi = finite_float(sent.get("industry_hhi")) if sent.get("ok") else None
    metrics.update(
        {
            "up_ratio_5d": _round(up_ratio, 3),
            "median_ret5": _round(median5),
            "top_decile_ret5": _round(top_decile),
            "dispersion": _round(dispersion),
            "wide_breadth_share": _round(wide_share, 3),
            "narrow_leader_share": _round(narrow_share, 3),
            "limit_up": sent.get("limit_up"),
            "height": sent.get("height"),
            "first_board_ratio": sent.get("first_board_ratio"),
            "industry_hhi": _round(hhi, 4),
            "style_state": style_state,
        }
    )

    reasons: list[str] = []
    if up_ratio is not None and median5 is not None:
        reasons.append(
            f"概念 5 日上涨占比 {up_ratio * 100:.0f}%（{len(ret5)} 个概念，中位数 {median5:+.2f}%）"
        )
    if dispersion is not None:
        reasons.append(
            f"Top十分位 − 中位数 = {dispersion:+.1f}pp（越大越集中在少数题材）"
        )
    reasons.append(f"上涨概念中龙头独拉占比 {narrow_share * 100:.0f}%（宽度<40% 但当日上涨）")
    if hhi is not None:
        line = f"涨停行业 HHI {hhi:.4f}"
        if sent.get("max_industry"):
            share = float(sent.get("max_industry_share") or 0.0)
            line += f"，最大行业 {sent['max_industry']} 占 {share * 100:.0f}%"
        reasons.append(line)
    if style_state:
        reasons.append(f"风格标签 {style_state}")

    r = _REGIME_RULES
    has_cluster = (
        (dispersion is not None and dispersion >= r["down_dispersion"])
        or narrow_share >= r["huddle_narrow"]
        or (hhi is not None and hhi >= r["huddle_hhi"])
    )
    regime = REGIME_ROTATION
    # 先判「有没有簇」，再判簇落在题材还是权重/红利；顺序决定语义边界
    if up_ratio is not None and up_ratio <= r["down_up_ratio"] and not has_cluster:
        regime = REGIME_DOWN
    elif (
        up_ratio is not None
        and up_ratio <= r["huddle_up_ratio"]
        and (
            narrow_share >= r["huddle_narrow"]
            or (hhi is not None and hhi >= r["huddle_hhi"])
            or (dispersion is not None and dispersion >= r["huddle_dispersion"])
        )
    ):
        regime = REGIME_HUDDLE
    elif (
        style_state in _DEFENSIVE_STYLES
        and up_ratio is not None
        and up_ratio <= r["defensive_up_ratio"]
    ):
        regime = REGIME_DEFENSIVE
    elif (
        up_ratio is not None
        and up_ratio >= r["broad_up_ratio"]
        and wide_share >= r["broad_wide"]
    ):
        regime = REGIME_BROAD

    if regime == REGIME_HUDDLE:
        strong = sum(
            1
            for hit in (
                narrow_share >= r["huddle_narrow"],
                hhi is not None and hhi >= r["huddle_hhi"],
                dispersion is not None and dispersion >= r["huddle_dispersion"],
            )
            if hit
        )
        confidence = "高" if strong >= 2 else "中"
    elif regime == REGIME_ROTATION:
        confidence = "低"
    else:
        confidence = "中"

    return {
        "regime": regime,
        "confidence": confidence,
        "reasons": reasons,
        "metrics": metrics,
    }


# ---------------------------------------------------------------- 主题线


def cluster_themes(
    ranked_boards: list,
    member_sets: dict,
    *,
    threshold: float = _CLUSTER_JACCARD,
) -> list:
    """把成分股高度重叠的强势板块合并成一条主题线（并查集，保留高分板块为主名）。"""
    codes = [str(b.get("code")) for b in ranked_boards if b.get("code")]
    if not codes:
        return []
    parent = {code: code for code in codes}

    def find(code: str) -> str:
        while parent[code] != code:
            parent[code] = parent[parent[code]]
            code = parent[code]
        return code

    for i, left in enumerate(codes):
        for right in codes[i + 1 :]:
            if (
                _jaccard(member_sets.get(left) or set(), member_sets.get(right) or set())
                >= threshold
            ):
                root_right, root_left = find(right), find(left)
                if root_right != root_left:
                    parent[root_right] = root_left

    groups: dict[str, list] = {}
    for code in codes:
        groups.setdefault(find(code), []).append(code)

    by_code = {str(b.get("code")): b for b in ranked_boards if b.get("code")}
    clusters = []
    for members in groups.values():
        ordered = sorted(
            members, key=lambda c: -(finite_float(by_code[c].get("score")) or 0.0)
        )
        union: set = set()
        for code in ordered:
            union |= member_sets.get(code) or set()
        clusters.append(
            {
                "primary": by_code[ordered[0]],
                "boards": [by_code[c] for c in ordered],
                "board_codes": ordered,
                "aliases": [by_code[c].get("name") for c in ordered[1:]],
                "member_codes": union,
            }
        )
    clusters.sort(key=lambda c: -(finite_float(c["primary"].get("score")) or 0.0))
    return clusters


def _theme_fade(primary: dict, theme: dict) -> tuple:
    """主题线的退潮迹象（= 昨日可能已是 S 点的风险）：回落形态 + 当日走弱 + 承接转弱。

    纯描述性事实句 + 计数等级（高 = 退潮阶段或 ≥2 条迹象），不预测后续。
    """
    signals: list[str] = []
    stage = primary.get("stage")
    change = finite_float(primary.get("change_pct"))
    vol_ratio = finite_float(primary.get("vol_ratio"))
    if stage in (STAGE_COOL, STAGE_EBB):
        line = f"板块阶段 {stage}"
        if change is not None:
            line += f"（当日 {change:+.2f}%）"
        if vol_ratio is not None:
            line += f"，量比 {vol_ratio:.2f}"
        signals.append(line)
    elif change is not None and change < 0:
        line = f"当日 {change:+.2f}%（强势线当日转跌）"
        if vol_ratio is not None:
            line += f"，量比 {vol_ratio:.2f}"
        signals.append(line)
    up_ratio = finite_float(theme.get("strong_member_up_ratio"))
    members = theme.get("n_members") or 0
    if up_ratio is not None and up_ratio < 0.40 and members >= _FADE_MIN_MEMBERS:
        signals.append(f"强势成员当日上涨占比 {up_ratio * 100:.0f}%（承接转弱）")
    if stage == STAGE_EBB or len(signals) >= 2:
        level = "高"
    elif signals:
        level = "中"
    else:
        level = "低"
    return signals, level


def theme_payload(
    cluster: dict,
    member_rows: dict,
    *,
    zt_codes: Optional[set] = None,
    benchmark: Optional[dict] = None,
) -> dict:
    """主题线明细：强势成员统计 / 涨停数 / 领涨股 / 归因串。"""
    primary = cluster["primary"]
    rows: dict[str, dict] = {}
    for code in cluster["board_codes"]:
        for raw in member_rows.get(code) or []:
            item = stock_record(raw) if isinstance(raw, dict) else None
            if item is None or not _is_a_share(item["code"]) or _is_st(item["name"]):
                continue
            rows.setdefault(item["code"], item)
    members = list(rows.values())
    zt_codes = zt_codes or set()
    benchmark = benchmark or {}

    leaders = sorted(
        [m for m in members if m.get("ret5") is not None],
        key=lambda m: (-_momentum_key(m), m["code"]),
    )[:_LEADER_RANK]
    ret5 = finite_float(primary.get("ret5"))
    bench5 = finite_float(benchmark.get("ret5_hs300"))
    excess5 = _round(ret5 - bench5, 2) if ret5 is not None and bench5 is not None else None

    theme = {
        "name": primary.get("name"),
        "aliases": [a for a in (cluster.get("aliases") or []) if a],
        "board_codes": list(cluster["board_codes"]),
        "score": primary.get("score"),
        "turn_score": primary.get("turn_score"),
        "picked_by": primary.get("picked_by"),
        "stage": primary.get("stage"),
        "breadth_label": primary.get("breadth_label"),
        "breadth": primary.get("breadth"),
        "ret3": primary.get("ret3"),
        "ret5": primary.get("ret5"),
        "ret10": primary.get("ret10"),
        "ret60": primary.get("ret60"),
        "vol_ratio": primary.get("vol_ratio"),
        "excess5": excess5,
        "n_members": len(members),
        "member_ret5_median": _round(_median([m.get("ret5") for m in members])),
        "member_ret10_median": _round(_median([m.get("ret10") for m in members])),
        "member_ret60_median": _round(_median([m.get("ret60") for m in members])),
        "strong_member_up_ratio": _round(
            _share([m.get("change_pct") for m in members], lambda v: v > 0), 3
        ),
        "limit_up_count": sum(1 for m in members if m["code"] in zt_codes),
        "leaders": [
            {
                "code": m["code"],
                "name": m["name"],
                "change_pct": _round(m.get("change_pct")),
                "ret3": _round(m.get("ret3")),
                "ret5": _round(m.get("ret5")),
                "ret10": _round(m.get("ret10")),
                "ret60": _round(m.get("ret60")),
                "vol_ratio": _round(m.get("vol_ratio")),
            }
            for m in leaders
        ],
        "attribution": [],
    }

    lines = list(board_attribution(primary))
    if primary.get("picked_by"):
        lines.append(f"入选口径：{primary['picked_by']}")
    if theme["excess5"] is not None:
        lines.append(f"相对沪深300 5日超额 {theme['excess5']:+.2f}pp")
    if theme["n_members"]:
        lines.append(
            f"强势成员 {theme['n_members']} 只：5日中位数 {theme['member_ret5_median']}%、"
            f"10日中位数 {theme['member_ret10_median']}%、"
            f"当日上涨占比 {(theme['strong_member_up_ratio'] or 0) * 100:.0f}%"
        )
    if theme["limit_up_count"]:
        lines.append(f"强势成员当日涨停 {theme['limit_up_count']} 家")
    if theme["aliases"]:
        lines.append(f"与高重叠概念合并：{'、'.join(theme['aliases'][:4])}")
    theme["attribution"] = lines
    theme["fade_signals"], theme["s_risk"] = _theme_fade(primary, theme)
    return theme


# ---------------------------------------------------------------- 个股强度


def _tech_notes(tech: Optional[dict]) -> tuple[list, Optional[str]]:
    """技术面确定性描述串 + 量能标签（无 tech 时返回缺口说明）。"""
    if not isinstance(tech, dict):
        return ["未取到 K 线，技术面缺口"], None
    notes: list = []
    vs = {
        key: finite_float(tech.get(f"vs_{key}_pct"))
        for key in ("ma5", "ma10", "ma20", "ma60")
    }
    above = [key.replace("ma", "MA") for key, value in vs.items() if value is not None and value >= 0]
    below = [key.replace("ma", "MA") for key, value in vs.items() if value is not None and value < 0]
    if above and not below:
        notes.append("站上 " + "/".join(above))
    elif above and below:
        notes.append("站上 " + "/".join(above) + f"，仍在 {'/'.join(below)} 下方")
    elif below:
        notes.append("跌破 " + "/".join(below))
    macd_hist = finite_float(tech.get("macd_hist"))
    dif = finite_float(tech.get("dif"))
    dea = finite_float(tech.get("dea"))
    if macd_hist is not None:
        line = f"MACD 柱{'为正' if macd_hist > 0 else '为负'}（{macd_hist:+.3f}）"
        if dif is not None and dea is not None and dif > dea:
            line += "，DIF>DEA"
        notes.append(line)
    adx = finite_float(tech.get("adx"))
    if adx is not None:
        notes.append(f"ADX {adx:.1f}" + ("（趋势较强）" if adx >= 25 else ""))
    close_loc = finite_float(tech.get("close_loc"))
    if close_loc is not None:
        if close_loc >= 0.7:
            notes.append(f"收在日内高位（{close_loc * 100:.0f}%）")
        elif close_loc <= 0.3:
            notes.append(f"收在日内低位（{close_loc * 100:.0f}%）")
    rel_vol = finite_float(tech.get("rel_vol"))
    stance = tech.get("vol_stance")
    if rel_vol is not None:
        notes.append(f"rel_vol {rel_vol:.2f}" + (f"（{stance}）" if stance else ""))
    return notes, (str(stance) if stance else None)


def _role_for(theme_name: Optional[str], rank_in_cluster: Optional[tuple]) -> str:
    if not theme_name:
        return ROLE_SOLO
    if not rank_in_cluster:
        return ROLE_FOLLOWER
    position, total = rank_in_cluster
    if position <= _LEADER_RANK:
        return ROLE_LEADER
    return f"{ROLE_FOLLOWER}（强势成员第 {position}/{total}）"


def build_candidates(
    member_rows: dict,
    market_rows: list,
    *,
    membership: dict,
    limit: int,
) -> list:
    """第一级漏斗：强主题成分股 ∪ 全市场多周期涨幅榜，去重后按多周期量价取 Top N。"""
    merged: dict[str, dict] = {}

    def add(raw, source: str):
        item = stock_record(raw) if isinstance(raw, dict) else None
        if item is None:
            return
        code = item["code"]
        if not _is_a_share(code) or _is_st(item["name"]) or _is_new_listing(item["name"]):
            return
        if (finite_float(item.get("price")) or 0.0) <= 0:
            return
        if item.get("ret5") is None and item.get("ret10") is None:
            return
        if code not in merged:
            item["sources"] = [source]
            merged[code] = item
        elif source not in merged[code]["sources"]:
            merged[code]["sources"].append(source)

    for code, rows in (member_rows or {}).items():
        for raw in rows or []:
            add(raw, f"theme:{code}")
    for raw in market_rows or []:
        add(raw, "market")

    if not merged:
        return []
    frame = pd.DataFrame(list(merged.values()))
    frame = frame.assign(composite=_weighted_z(frame, _CANDIDATE_WEIGHTS))
    frame = frame.sort_values(
        by=["composite", "code"], ascending=[False, True], kind="mergesort"
    )
    out = []
    for _, row in frame.head(limit).iterrows():
        item = dict(row)
        item.pop("composite", None)
        item["themes"] = list((membership or {}).get(item["code"]) or [])
        out.append(item)
    return out


def _add_excess(frame: pd.DataFrame, benchmark: dict, tech_by_code: dict) -> pd.DataFrame:
    """加相对沪深300 超额（ex3/5/10/60）与 rel_vol 列；基准缺口按 0 处理。"""
    for horizon in _BENCHMARK_HORIZONS:
        key = f"ret{horizon}"
        bench = finite_float(benchmark.get(f"ret{horizon}_hs300"))
        if bench is None or key not in frame.columns:
            frame[f"ex{horizon}"] = 0.0
        else:
            frame[f"ex{horizon}"] = (
                pd.to_numeric(frame[key], errors="coerce").astype(float) - bench
            )
    frame["rel_vol"] = pd.Series(
        [
            finite_float((tech_by_code.get(code) or {}).get("rel_vol"))
            if isinstance(tech_by_code.get(code), dict)
            else None
            for code in frame["code"]
        ],
        index=frame.index,
    )
    # 量能单列（日线 rel_vol 优先，缺则用 clist 量比兜底）与形态列（距 MA20）：
    # 避免「量比 + rel_vol」双口径重复计权，并让强势榜也有形态项。
    vol_ratio = (
        pd.to_numeric(frame["vol_ratio"], errors="coerce")
        if "vol_ratio" in frame.columns
        else pd.Series(None, index=frame.index, dtype=float)
    )
    frame["volume"] = frame["rel_vol"].where(frame["rel_vol"].notna(), vol_ratio)
    frame["ma20"] = pd.Series(
        [
            finite_float((tech_by_code.get(code) or {}).get("vs_ma20_pct"))
            if isinstance(tech_by_code.get(code), dict)
            else None
            for code in frame["code"]
        ],
        index=frame.index,
    )
    return frame


def _stock_payload(
    item: dict,
    *,
    code: str,
    tech: Optional[dict],
    themes: list,
    role: str,
    score,
    stage: str,
    space: dict,
    pullback: Optional[dict],
    action: str,
    action_notes: list,
    launch: Optional[dict] = None,
) -> dict:
    """个股 payload：强势榜与底部启动榜共用同一形状（skill 可统一引用）。

    `launch` 只有底部启动榜有值（启动标志命中清单与加权分），强势榜为 None。
    """
    notes, stance = _tech_notes(tech)
    payload = {
        "code": code,
        "name": item.get("name"),
        "price": _round(item.get("price")),
        "change_pct": _round(item.get("change_pct")),
        "ret3": _round(item.get("ret3")),
        "ret5": _round(item.get("ret5")),
        "ret10": _round(item.get("ret10")),
        "ret60": _round(item.get("ret60")),
        "excess3": _round(item.get("ex3")),
        "excess5": _round(item.get("ex5")),
        "excess10": _round(item.get("ex10")),
        "excess60": _round(item.get("ex60")),
        "vol_ratio": _round(item.get("vol_ratio")),
        "turnover": _round(item.get("turnover")),
        "main_inflow": _round(item.get("main_inflow"), 0),
        "main_inflow_pct": _round(item.get("main_inflow_pct")),
        "amount": _round(item.get("amount"), 0),
        "score": _round(score, 1),
        "launch": launch,
        "stage": stage,
        "space": space,
        "pullback": pullback,
        "action": action,
        "action_notes": action_notes,
        "themes": themes,
        "role": role,
        "vol_stance": stance,
        "tech_notes": notes,
        "tech": (
            {
                "as_of": tech.get("as_of"),
                "source": tech.get("source"),
                "close": _round(tech.get("close")),
                "rel_vol": _round(tech.get("rel_vol")),
                "rv20": _round(tech.get("rv20")),
                "vs_ma5_pct": _round(tech.get("vs_ma5_pct")),
                "vs_ma20_pct": _round(tech.get("vs_ma20_pct")),
                "vs_ma60_pct": _round(tech.get("vs_ma60_pct")),
                "adx": _round(tech.get("adx")),
                "macd_hist": _round(tech.get("macd_hist"), 3),
                "close_loc": _round(tech.get("close_loc")),
                "natr": _round(tech.get("natr")),
            }
            if tech
            else None
        ),
        "sources": list(item.get("sources") or []),
    }
    payload["attribution"] = _stock_attribution(payload)
    return payload


def _ranked_rows(frame: pd.DataFrame) -> list:
    """按 composite 降序（同分按代码）取行，并去掉 composite 列。"""
    frame = frame.sort_values(
        by=["composite", "code"], ascending=[False, True], kind="mergesort"
    )
    out = []
    for _, row in frame.iterrows():
        item = dict(row)
        item.pop("composite", None)
        out.append(item)
    return out


def _stock_assembly(
    item: dict,
    *,
    tech_by_code: dict,
    cluster_membership: dict,
    cluster_order: dict,
    zt_streak: dict,
    regime: Optional[str],
    theme_risk: dict,
    launch: Optional[dict] = None,
) -> dict:
    """把一条记录补全成个股 payload（角色 / 阶段 / 空间 / 回落性质 / 操作口径）。"""
    code = str(item.get("code"))
    themes = list(cluster_membership.get(code) or [])
    theme_name = themes[0] if themes else None
    positions = cluster_order.get(theme_name) or []
    rank_in_cluster = (
        (positions.index(code) + 1, len(positions)) if code in positions else None
    )
    tech = tech_by_code.get(code) if isinstance(tech_by_code.get(code), dict) else None
    stage = _stock_stage(item.get("ex3"), item.get("ex5"), item.get("ex10"))
    space = _space_of(
        code,
        streak=int(zt_streak.get(code) or 0),
        excess3=item.get("ex3"),
        excess10=item.get("ex10"),
    )
    pullback = _pullback_of(item.get("change_pct"), item.get("ret3"), tech)
    action, action_notes = _action_for(
        stage, space, regime, theme_risk.get(theme_name), pullback
    )
    return _stock_payload(
        item,
        code=code,
        tech=tech,
        themes=themes,
        role=_role_for(theme_name, rank_in_cluster),
        score=item.get("score"),
        stage=stage,
        space=space,
        pullback=pullback,
        action=action,
        action_notes=action_notes,
        launch=launch,
    )


def rank_stocks(
    candidates: list,
    *,
    tech_by_code: Optional[dict] = None,
    benchmark: Optional[dict] = None,
    cluster_membership: Optional[dict] = None,
    cluster_order: Optional[dict] = None,
    zt_streak: Optional[dict] = None,
    regime: Optional[str] = None,
    theme_risk: Optional[dict] = None,
) -> list:
    """第二级：叠加技术全套、相对沪深300 超额、剩余空间与操作口径，输出个股强度榜。"""
    if not candidates:
        return []
    tech_by_code = tech_by_code or {}
    benchmark = benchmark or {}
    cluster_membership = cluster_membership or {}
    cluster_order = cluster_order or {}
    zt_streak = zt_streak or {}
    theme_risk = theme_risk or {}

    frame = _add_excess(pd.DataFrame(candidates), benchmark, tech_by_code)
    frame["composite"] = _weighted_z(frame, _STOCK_WEIGHTS)
    frame["score"] = _percentile_score(frame["composite"])
    return [
        _stock_assembly(
            item,
            tech_by_code=tech_by_code,
            cluster_membership=cluster_membership,
            cluster_order=cluster_order,
            zt_streak=zt_streak,
            regime=regime,
            theme_risk=theme_risk,
        )
        for item in _ranked_rows(frame)
    ]


def _is_bottom_start(record: dict) -> bool:
    """底部启动初筛：当日/3 日转强 + 放量，而 5/10/60 日还没走出来（= 位置还低）。"""
    change = finite_float(record.get("change_pct"))
    ret3 = finite_float(record.get("ret3"))
    vol_ratio = finite_float(record.get("vol_ratio"))
    amount = finite_float(record.get("amount"))
    turnover = finite_float(record.get("turnover"))
    if change is None or change <= _BOTTOM_MIN_CHANGE or ret3 is None or ret3 <= 0:
        return False
    if vol_ratio is None or vol_ratio < _BOTTOM_MIN_VOL_RATIO:
        return False
    if amount is None or amount < _BOTTOM_MIN_AMOUNT:
        return False
    if turnover is not None and not (
        _BOTTOM_MIN_TURNOVER <= turnover <= _BOTTOM_MAX_TURNOVER
    ):
        return False
    for key, cap in (
        ("ret5", _BOTTOM_MAX_RET5),
        ("ret10", _BOTTOM_MAX_RET10),
        ("ret60", _BOTTOM_MAX_RET60),
    ):
        value = finite_float(record.get(key))
        if value is not None and value > cap:
            return False
    return True


def _launch_of(item: dict, tech: Optional[dict]) -> dict:
    """启动标志（量能 / 资金 / 形态为主）：命中 / 缺口清单 + 加权分。

    量能：量能突变 rv20≥2、短周期放量 rel_vol≥1.5、资金流 MFI≥60（各 2 分）；
    资金：主力净流入 > 0 且净占比 ≥3%（2 分）；
    形态：刚站上 MA20（0～12%）、贴近/站上 MA60（≥−3%）、收在日内高位（≥0.7）、
          MACD 柱翻红、均线粘合（MA5/10/20 离散度 ≤3%）（各 2 分）；
    价格/方向：大阳 ≥5%、+DI > −DI（各 1 分）。满分 20。
    """
    tech = tech or {}
    rv20 = finite_float(tech.get("rv20"))
    rel_vol = finite_float(tech.get("rel_vol"))
    mfi = finite_float(tech.get("mfi"))
    vs20 = finite_float(tech.get("vs_ma20_pct"))
    vs60 = finite_float(tech.get("vs_ma60_pct"))
    close_loc = finite_float(tech.get("close_loc"))
    macd_hist = finite_float(tech.get("macd_hist"))
    plus_di = finite_float(tech.get("plus_di"))
    minus_di = finite_float(tech.get("minus_di"))
    change = finite_float(item.get("change_pct"))
    main_inflow = finite_float(item.get("main_inflow"))
    main_pct = finite_float(item.get("main_inflow_pct"))
    mas = [finite_float(tech.get(k)) for k in ("ma5", "ma10", "ma20")]
    cluster = None
    if all(v is not None and v > 0 for v in mas):
        cluster = (max(mas) / min(mas) - 1.0) * 100

    hits: list[str] = []
    miss: list[str] = []
    score = 0.0

    def flag(kind: str, ok: bool, text: str, points: float) -> None:
        nonlocal score
        if ok:
            score += points
            hits.append(f"{kind}：{text}")
        else:
            miss.append(f"{kind}：{text}（未达）")

    flag(
        "量能",
        rv20 is not None and rv20 >= _LAUNCH_RV20,
        f"量能突变 rv20 {rv20:.2f}" if rv20 is not None else "量能突变 rv20 缺数据",
        _LAUNCH_VOL_POINTS,
    )
    flag(
        "量能",
        rel_vol is not None and rel_vol >= _LAUNCH_REL_VOL,
        f"短周期放量 rel_vol {rel_vol:.2f}" if rel_vol is not None else "短周期放量缺数据",
        _LAUNCH_VOL_POINTS,
    )
    flag(
        "量能",
        mfi is not None and mfi >= _LAUNCH_MFI,
        f"资金流 MFI {mfi:.0f}" if mfi is not None else "资金流 MFI 缺数据",
        _LAUNCH_VOL_POINTS,
    )
    flag(
        "资金",
        main_inflow is not None
        and main_inflow > 0
        and main_pct is not None
        and main_pct >= _LAUNCH_MAIN_INFLOW_PCT,
        (
            f"主力净流入 {main_inflow / 1e8:+.2f}亿（占比 {main_pct:+.1f}%）"
            if main_inflow is not None and main_pct is not None
            else "主力资金缺数据"
        ),
        _LAUNCH_VOL_POINTS,
    )
    flag(
        "形态",
        vs20 is not None and 0 <= vs20 <= _LAUNCH_MA20_MAX,
        f"刚站上 MA20（{vs20:+.1f}%）" if vs20 is not None else "MA20 位置缺数据",
        _LAUNCH_SHAPE_POINTS,
    )
    flag(
        "形态",
        vs60 is not None and vs60 >= _LAUNCH_MA60_MIN,
        f"贴近/站上 MA60（{vs60:+.1f}%）" if vs60 is not None else "MA60 位置缺数据",
        _LAUNCH_SHAPE_POINTS,
    )
    flag(
        "形态",
        close_loc is not None and close_loc >= _LAUNCH_CLOSE_LOC,
        f"收在日内高位 {close_loc * 100:.0f}%" if close_loc is not None else "日内位置缺数据",
        _LAUNCH_SHAPE_POINTS,
    )
    flag(
        "形态",
        macd_hist is not None and macd_hist > 0,
        f"MACD 柱翻红 {macd_hist:+.3f}" if macd_hist is not None else "MACD 柱缺数据",
        _LAUNCH_SHAPE_POINTS,
    )
    flag(
        "形态",
        cluster is not None and cluster <= _LAUNCH_MA_CLUSTER,
        f"均线粘合（MA5/10/20 离散 {cluster:.1f}%）" if cluster is not None else "均线缺数据",
        _LAUNCH_SHAPE_POINTS,
    )
    flag(
        "价格",
        change is not None and change >= _LAUNCH_BIG_DAY,
        f"大阳 {change:+.2f}%" if change is not None else "当日涨幅缺数据",
        1.0,
    )
    flag(
        "方向",
        plus_di is not None and minus_di is not None and plus_di > minus_di,
        (
            f"+DI {plus_di:.1f} > −DI {minus_di:.1f}"
            if plus_di is not None and minus_di is not None
            else "DI 缺数据"
        ),
        1.0,
    )
    return {"points": round(score, 1), "hits": hits, "miss": miss}


def rank_bottom(
    records: list,
    *,
    limit: int = DEFAULT_MAX_BOTTOM,
    tech_by_code: Optional[dict] = None,
    benchmark: Optional[dict] = None,
    cluster_membership: Optional[dict] = None,
    cluster_order: Optional[dict] = None,
    zt_streak: Optional[dict] = None,
    regime: Optional[str] = None,
    theme_risk: Optional[dict] = None,
) -> list:
    """底部启动榜：位置还低 + 当日/3 日转强 + 放量，按**启动标志加权分**排序（量能、形态为主）。

    `score` 是启动分位（低位池内量价的分位，用于粗排与同分次序）；`launch` 是启动标志的
    命中清单与加权分（量能 3 条、形态 4 条各 2 分，价格/方向各 1 分），是主排序键。
    """
    pool = [r for r in records if _is_bottom_start(r)]
    if not pool:
        return []
    tech_by_code = tech_by_code or {}
    cluster_membership = cluster_membership or {}
    cluster_order = cluster_order or {}
    zt_streak = zt_streak or {}
    theme_risk = theme_risk or {}

    frame = _add_excess(pd.DataFrame(pool), benchmark or {}, tech_by_code)
    frame["composite"] = _weighted_z(frame, _BOTTOM_WEIGHTS)
    frame["score"] = _percentile_score(frame["composite"])
    rows = []
    for item in _ranked_rows(frame):
        code = str(item.get("code"))
        tech = tech_by_code.get(code) if isinstance(tech_by_code.get(code), dict) else None
        rows.append(
            _stock_assembly(
                item,
                tech_by_code=tech_by_code,
                cluster_membership=cluster_membership,
                cluster_order=cluster_order,
                zt_streak=zt_streak,
                regime=regime,
                theme_risk=theme_risk,
                launch=_launch_of(item, tech),
            )
        )
    rows.sort(
        key=lambda r: (
            -(finite_float(r["launch"]["points"]) or 0.0),
            -(finite_float(r["score"]) or 0.0),
            r["code"],
        )
    )
    return rows[:limit]


def _stock_attribution(stock: dict) -> dict:
    """个股归因两段（theme / stock）；市场因素由顶层 market.context 统一给一次。"""
    theme: list = []
    ret5 = finite_float(stock.get("ret5"))
    excess5 = finite_float(stock.get("excess5"))
    if ret5 is not None:
        line = f"5日 {ret5:+.2f}%"
        if excess5 is not None:
            line += f"（相对沪深300 {excess5:+.1f}pp）"
        ret10 = finite_float(stock.get("ret10"))
        if ret10 is not None:
            line += f"，10日 {ret10:+.2f}%"
        theme.append(line)
    vol_ratio = finite_float(stock.get("vol_ratio"))
    if vol_ratio is not None:
        theme.append(f"当日量比 {vol_ratio:.2f}")
    if stock.get("themes"):
        theme.append("所属强势主题：" + "、".join(stock["themes"][:3]))
    if stock.get("role"):
        theme.append(f"簇内角色：{stock['role']}")
    stock_side = list(stock.get("tech_notes") or [])
    if stock.get("stage"):
        stock_side.append(f"阶段 {stock['stage']}")
    return {"theme": theme, "stock": stock_side}


# ---------------------------------------------------------------- 风格与基准


def _series_ret(series: pd.Series, n: int) -> Optional[float]:
    """n 日收益（小数）。与 `stocks.style._ret` 同定义，但 style 只给 5/20/60 日，
    本模块要 3/10 日且不跨模块依赖私有函数，故本地保留。"""
    values = pd.to_numeric(series, errors="coerce").dropna()
    values = values[values > 0]
    if len(values) < n + 1:
        return None
    start = float(values.iloc[-(n + 1)])
    if start == 0:
        return None
    return float(values.iloc[-1]) / start - 1.0


def fetch_style_info() -> dict:
    """复用 stocks.style 的指数链路，产出风格标签与沪深300 多周期基准。"""
    from stocks import style as style_mod

    closes = style_mod.fetch_style_closes()
    feat = style_mod.compute_style_features(closes)
    state, reasons = style_mod.classify_style(feat)
    frame = feat.get("frame")
    benchmark: dict[str, Any] = {}
    as_of = None
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        as_of = frame.index[-1].strftime("%Y-%m-%d")
        for horizon in _BENCHMARK_HORIZONS:
            # 指数收益是小数，转成和 clist 一致的百分数口径（否则超额会差 100 倍量级）
            value = _series_ret(frame["hs300"], horizon)
            benchmark[f"ret{horizon}_hs300"] = (
                _round(value * 100.0, 2) if value is not None else None
            )
    return {
        "ok": bool(feat.get("ok")),
        "as_of": as_of,
        "state": state,
        "reasons": list(reasons)[:4],
        "benchmark": benchmark,
    }


def _fetch_zt_rows(quote_date: Optional[str]) -> list:
    """涨停池（只拉涨停池：够算家数 / 高度 / 首板占比 / 行业集中度）。"""
    try:
        import akshare as ak
    except ImportError as exc:  # pragma: no cover - 依赖缺失属环境问题
        raise RuntimeError("需要 akshare 才能取涨停池") from exc

    from stocks.util import force_direct_http

    candidates: list[str] = []
    if quote_date:
        candidates.append(str(quote_date).replace("-", ""))
    today = datetime.datetime.now(_TZ).date()
    for offset in range(0, 7):
        candidates.append((today - datetime.timedelta(days=offset)).strftime("%Y%m%d"))
    seen: set = set()
    for day in candidates:
        if not day or day in seen:
            continue
        seen.add(day)
        try:
            with force_direct_http():
                frame = ak.stock_zt_pool_em(date=day)
        except Exception:
            continue
        if frame is None or frame.empty:
            continue
        return frame.to_dict(orient="records")
    return []


# ---------------------------------------------------------------- 组装


def _validate_int(name: str, value: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数")
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} 必须在 1..{maximum} 之间")
    return value


def _market_context_lines(regime: dict, style_info: dict, sentiment: dict) -> list:
    lines = []
    if regime.get("regime"):
        metrics = regime.get("metrics") or {}
        up_ratio = finite_float(metrics.get("up_ratio_5d"))
        lines.append(
            f"结构={regime['regime']}（置信度 {regime.get('confidence')}；"
            f"概念5日上涨占比 {(up_ratio or 0) * 100:.0f}%，"
            f"Top十分位−中位数 {metrics.get('dispersion')}pp）"
        )
    if style_info.get("state"):
        benchmark = style_info.get("benchmark") or {}
        line = f"风格={style_info['state']}"
        if benchmark.get("ret5_hs300") is not None:
            line += f"，沪深300 5日 {benchmark['ret5_hs300']:+.2f}%"
        lines.append(line)
    if sentiment.get("ok"):
        parts = [f"涨停 {sentiment.get('limit_up')} 家"]
        if sentiment.get("height") is not None:
            parts.append(f"高度 {sentiment['height']} 板")
        if sentiment.get("first_board_ratio") is not None:
            parts.append(f"首板占比 {sentiment['first_board_ratio'] * 100:.0f}%")
        if sentiment.get("industry_hhi") is not None:
            parts.append(f"行业HHI {sentiment['industry_hhi']:.4f}")
        lines.append("情绪=" + "，".join(parts))
    return lines


def _fetch_members(board_codes: list, errors: list) -> dict:
    """并发拉多个板块的成分股首页；失败记 errors 并返回空列表（不中断整体取证）。"""
    out: dict[str, list] = {}
    if not board_codes:
        return out
    with ThreadPoolExecutor(max_workers=min(6, len(board_codes))) as pool:
        futures = {pool.submit(fetch_board_members, code): code for code in board_codes}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                out[code] = fut.result()
            except Exception as exc:
                out[code] = []
                errors.append(f"members:{code}:{type(exc).__name__}")
    return out


def _fetch_tech_map(codes: list, tech_map: dict, errors: list) -> None:
    """按候选池限额逐只精算日线（复用 watch_advice 的新浪链路），结果就地写入 tech_map。"""
    missing = [code for code in codes if code not in tech_map]
    if not missing:
        return
    from stocks import watch_advice as watch_advice_mod
    from stocks.util import recent_date

    start = recent_date(_KLINE_LOOKBACK_DAYS)
    with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
        futures = {
            pool.submit(watch_advice_mod._fetch_tech_for_code, code, start=start): code
            for code in missing
        }
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                snap, err = fut.result()
            except Exception as exc:
                snap, err = None, f"tech:{type(exc).__name__}"
            if snap is not None:
                tech_map[code] = snap
            if err:
                errors.append(f"tech:{code}:{str(err)[:60]}")


def _data_completeness(
    regime: dict, themes: list, stocks: list, sentiment: dict, style_info: dict
) -> str:
    """本次取证的数据完整度（高/中/低），与 regime 的判定置信度是两件事。"""
    score = 0
    if regime.get("regime") not in (None, REGIME_UNKNOWN):
        score += 1
    if len(themes) >= 2:
        score += 1
    if stocks:
        score += 1
    if sentiment.get("ok"):
        score += 1
    if style_info.get("ok"):
        score += 1
    if score >= 4:
        return "高"
    if score >= 3:
        return "中"
    return "低"


def _prepare_theme_maps(
    themes: list, clusters: list, member_rows: dict
) -> tuple[dict, dict, dict]:
    """主题成员 → (代码→主题名, 主题→簇内动量排名, 代码→动量键)。

    簇内排名用 `_momentum_key`（5日×0.5 + 3日×0.3 + 当日×0.2），
    这样「今天涨停、5 日还没走出来」的票也能被认成簇内领涨。
    """
    key_map: dict[str, float] = {}
    theme_of: dict[str, list] = {}
    for cluster in clusters:
        for board_code in cluster["board_codes"]:
            for raw in member_rows.get(board_code) or []:
                if not isinstance(raw, dict):
                    continue
                code = str(raw.get("f12") or "").strip()
                if not code or code in key_map:
                    continue
                key_map[code] = _momentum_key(stock_record(raw) or {})
    for theme, cluster in zip(themes, clusters):
        name = theme.get("name")
        for code in cluster["member_codes"]:
            if not _is_a_share(code):
                continue
            theme_of.setdefault(code, [])
            if name and name not in theme_of[code]:
                theme_of[code].append(name)
    cluster_order: dict[str, list] = {}
    for cluster, theme in zip(clusters, themes):
        name = theme.get("name")
        codes = [c for c in cluster["member_codes"] if _is_a_share(c)]
        cluster_order[name] = sorted(
            codes, key=lambda c: (-(key_map.get(c, -999.0)), c)
        )
    return theme_of, cluster_order, key_map


def analyze(
    *,
    top_concepts: int = DEFAULT_TOP_CONCEPTS,
    top_industries: int = DEFAULT_TOP_INDUSTRIES,
    max_stocks: int = DEFAULT_MAX_STOCKS,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    board_rows: Optional[dict] = None,
    members_by_board: Optional[dict] = None,
    market_top_rows: Optional[list] = None,
    zt_rows: Optional[list] = None,
    style_payload: Optional[dict] = None,
    tech_by_code: Optional[dict] = None,
    bottom_rows: Optional[list] = None,
) -> dict[str, Any]:
    """近期强势体检（含底部启动）。各层独立降级：失败只记 errors；整层无数据才抛 ValueError。

    `board_rows` / `members_by_board` / `market_top_rows` / `zt_rows` / `style_payload` /
    `tech_by_code` / `bottom_rows` 为原始行注入点，便于单测与复用，避免打网络。
    """
    top_concepts = _validate_int("top_concepts", top_concepts, MAX_TOP_BOARDS)
    top_industries = _validate_int("top_industries", top_industries, MAX_TOP_BOARDS)
    max_stocks = _validate_int("max_stocks", max_stocks, MAX_STOCKS_LIMIT)
    max_candidates = _validate_int("max_candidates", max_candidates, MAX_CANDIDATES_LIMIT)

    errors: list[str] = []
    notes = list(_NOTES)

    # —— 1) 板块截面 ——
    raw_boards = dict(board_rows or {})
    if board_rows is None:
        for kind in ("concept", "industry"):
            try:
                raw_boards[kind] = fetch_board_rows(kind)
            except Exception as exc:
                raw_boards[kind] = []
                errors.append(f"board:{kind}:{type(exc).__name__}")
    concept_records_all = _records(raw_boards.get("concept"), board_record)
    concept_records = [r for r in concept_records_all if _is_theme_concept(r["name"])]
    n_concepts_excluded = len(concept_records_all) - len(concept_records)
    industry_records = _records(raw_boards.get("industry"), board_record)
    if board_rows is None:
        if not concept_records:
            errors.append("board:concept:empty")
        if not industry_records:
            errors.append("board:industry:empty")

    quote_date, quote_ts = derive_quote_date(concept_records or industry_records)

    # —— 1b) 屏蔽名单（红利类概念：不进主题线与个股榜；boards.* 仍保留原始截面）——
    blocked_boards = [r for r in concept_records_all if _blocked_concept_name(r["name"])]
    blocked_codes: set[str] = set()
    if blocked_boards:
        if members_by_board is not None:
            blocked_rows = {
                b["code"]: list(members_by_board.get(b["code"]) or [])
                for b in blocked_boards
            }
        else:
            blocked_rows = _fetch_members([b["code"] for b in blocked_boards], errors)
        for rows in blocked_rows.values():
            for raw in rows:
                code = str(raw.get("f12") or "").strip() if isinstance(raw, dict) else ""
                if code:
                    blocked_codes.add(code)
        notes.append(
            "已屏蔽红利类概念（不进主题线与个股榜，boards 保留原始截面）：{}（剔除 {} 只成员）".format(
                "、".join(b["name"] for b in blocked_boards[:5]), len(blocked_codes)
            )
        )

    # —— 2) 涨停池 ——
    zt = zt_rows
    if zt is None:
        try:
            zt = _fetch_zt_rows(quote_date)
        except Exception as exc:
            zt = []
            errors.append(f"zt:{type(exc).__name__}")
    zt = [r for r in (zt or []) if isinstance(r, dict)]
    sentiment = _sentiment_from_zt(zt)
    if not sentiment.get("ok"):
        errors.append("zt:empty")
    zt_streak: dict[str, int] = {}
    for row in zt:
        code = str(row.get("代码") or "").strip()
        if code:
            zt_streak[code] = int(finite_float(row.get("连板数")) or 0)

    # —— 3) 风格与基准 ——
    style_info: dict[str, Any] = {"ok": False, "state": None, "benchmark": {}}
    if isinstance(style_payload, dict) and style_payload:
        style_info = dict(style_payload)
        style_info.setdefault(
            "ok", bool(style_info.get("benchmark") or style_info.get("state"))
        )
    else:
        try:
            style_info = fetch_style_info()
        except Exception as exc:
            errors.append(f"style:{type(exc).__name__}")
    if not style_info.get("ok"):
        notes.append("风格/基准缺口：相对超额按 0 处理，个股强度退化为绝对涨幅+量能口径。")

    # —— 4) 板块强度与市场结构 ——
    concept_scores = score_boards(concept_records)
    industry_scores = score_boards(industry_records)
    regime = compute_regime(
        concept_scores, sentiment=sentiment, style_state=style_info.get("state")
    )

    # —— 5) 主题线 ——
    # 选取口径：概念强度 TopN ∪ 行业强度 TopM ∪「刚转强」启动分 TopK（要求当日与 3 日都为正）。
    # 概念板块的成员口径很宽（同一个概念横跨多个行业），会把题材涨幅摊薄；
    # 窄口径的行业板块（如 大众出版）反而更能反映真实主线，所以两层一起选再按成分重叠合并。
    # 每只板块会标 picked_by，便于回溯它是怎么进榜的。
    industry_selected = _dedupe_industry_lines(industry_scores)[:top_industries]
    selected: list[dict] = []
    picked: set[str] = set()
    for board in concept_scores[:top_concepts]:
        if board["code"] in picked:
            continue
        picked.add(board["code"])
        board["picked_by"] = f"概念强度分 Top{top_concepts}"
        selected.append(board)
    for board in industry_selected:
        if board["code"] in picked:
            continue
        picked.add(board["code"])
        board["picked_by"] = f"行业强度分 Top{top_industries}"
        selected.append(board)
    # 「刚转强」名额：当日与 3 日都为正的板块按启动分补充（不挤占强度分名额）
    turns = sorted(
        (
            b
            for b in concept_scores
            if (finite_float(b.get("change_pct")) or 0.0) > 0
            and (finite_float(b.get("ret3")) or 0.0) > 0
        ),
        key=lambda b: (-(finite_float(b.get("turn_score")) or 0.0), str(b.get("code"))),
    )
    added_turn = 0
    for board in turns:
        if added_turn >= _TURN_EXTRA:
            break
        if board["code"] in picked:
            continue
        picked.add(board["code"])
        board["picked_by"] = "启动分（当日+3日转强）"
        selected.append(board)
        added_turn += 1
    top_concept_boards = selected
    theme_rows: dict[str, list] = {}
    if top_concept_boards:
        if members_by_board is not None:
            theme_rows = {
                board["code"]: list(members_by_board.get(board["code"]) or [])
                for board in top_concept_boards
            }
        else:
            theme_rows = _fetch_members([b["code"] for b in top_concept_boards], errors)
        missing = [b["code"] for b in top_concept_boards if not theme_rows.get(b["code"])]
        if missing:
            errors.append(f"members:empty:{len(missing)}")
    # 屏蔽名单与 ST/北交所 同属「选股 universe」过滤：从源头（成员）剔除，下游无需再判
    if blocked_codes:
        theme_rows = {
            code: [
                r for r in rows if str(r.get("f12") or "").strip() not in blocked_codes
            ]
            for code, rows in theme_rows.items()
        }
    member_sets = {
        code: {
            str(r.get("f12")).strip()
            for r in rows
            if isinstance(r, dict) and str(r.get("f12") or "").strip()
        }
        for code, rows in theme_rows.items()
    }
    clusters = cluster_themes(top_concept_boards, member_sets)
    themes = [
        theme_payload(
            cluster,
            theme_rows,
            zt_codes=set(zt_streak),
            benchmark=style_info.get("benchmark"),
        )
        for cluster in clusters
    ]
    # 主题线过滤：成员过少（如单票行业）或命中屏蔽名单 → 不成主题线（boards 仍保留原始截面）
    dropped_small = [t["name"] for t in themes if (t.get("n_members") or 0) < _MIN_THEME_MEMBERS]
    drop_names = set(dropped_small) | {
        t["name"] for t in themes if _blocked_concept_name(t["name"])
    }
    if drop_names:
        themes = [t for t in themes if t["name"] not in drop_names]
        clusters = [c for c in clusters if c["primary"].get("name") not in drop_names]
        if dropped_small:
            notes.append(
                "成员<{}的板块未计入主题线（样本过少，如单票行业）：{}".format(
                    _MIN_THEME_MEMBERS, "、".join(dropped_small[:5])
                )
            )
    theme_of, cluster_order, _key_map = _prepare_theme_maps(themes, clusters, theme_rows)

    # —— 6) 个股候选与精算 ——
    market_rows = market_top_rows
    if market_rows is None:
        # 页内失败由函数内部降级并记 errors（部分成功时取到的行仍可用）。
        market_rows = fetch_market_top(errors)
    if blocked_codes:
        market_rows = [
            r for r in market_rows if str(r.get("f12") or "").strip() not in blocked_codes
        ]

    candidates = build_candidates(
        theme_rows, market_rows or [], membership=theme_of, limit=max_candidates
    )

    # —— 6b) 底部启动（与强势榜互补：已走强的多在异动口径内，可参与的多是刚离底）——
    raw_bottom = bottom_rows
    if raw_bottom is None:
        raw_bottom = fetch_market_scan(errors)
    bottom_pool = [
        r
        for r in _records(raw_bottom, stock_record)
        if _is_a_share(r["code"])
        and not _is_st(r["name"])
        and not _is_new_listing(r["name"])
        and r["code"] not in blocked_codes
        and (finite_float(r.get("price")) or 0.0) > 0
    ]
    # 先按量价粗排取前 N 只（启动标志需要日线），补完日线后再按标志分重建 payload
    bottom_pre = rank_bottom(
        bottom_pool,
        limit=_BOTTOM_KLINE_LIMIT,
        benchmark=style_info.get("benchmark"),
        zt_streak=zt_streak,
    )
    bottom_codes = [r["code"] for r in bottom_pre]

    tech_map: dict[str, Any] = dict(tech_by_code or {})
    _fetch_tech_map([c["code"] for c in candidates] + bottom_codes, tech_map, errors)

    theme_risk = {t.get("name"): t.get("s_risk") for t in themes}
    market_lines = _market_context_lines(regime, style_info, sentiment)
    ranked = rank_stocks(
        candidates,
        tech_by_code=tech_map,
        benchmark=style_info.get("benchmark"),
        cluster_membership=theme_of,
        cluster_order=cluster_order,
        zt_streak=zt_streak,
        regime=regime.get("regime"),
        theme_risk=theme_risk,
    )
    stocks = ranked[:max_stocks]
    if candidates and not any(s.get("tech") for s in stocks):
        notes.append("个股技术面全部缺失：本次只有多周期涨幅与量能口径。")

    bottom = rank_bottom(
        bottom_pool,
        tech_by_code=tech_map,
        benchmark=style_info.get("benchmark"),
        cluster_membership=theme_of,
        cluster_order=cluster_order,
        zt_streak=zt_streak,
        regime=regime.get("regime"),
        theme_risk=theme_risk,
    )
    if bottom_pool and not bottom:
        notes.append("底部启动池为空：本次没有满足「位置还低 + 放量转强」的票。")

    if not concept_records and not industry_records and not stocks:
        raise ValueError("近期强势取证失败：板块与个股均无数据")

    as_of_dates = sorted(
        {
            value
            for value in (
                quote_date,
                style_info.get("as_of"),
                *(
                    (s.get("tech") or {}).get("as_of")
                    for s in (*stocks, *bottom)
                    if isinstance(s.get("tech"), dict)
                ),
            )
            if value
        }
    )

    return {
        "quote_trade_date": quote_date,
        "quote_timestamp": quote_ts,
        "as_of": as_of_dates[0] if len(as_of_dates) == 1 else None,
        "as_of_dates": as_of_dates,
        "observed_at": datetime.datetime.now(_TZ).isoformat(timespec="seconds"),
        "data_completeness": _data_completeness(
            regime, themes, stocks, sentiment, style_info
        ),
        "regime": regime,
        "themes": themes,
        "boards": {
            "concept": concept_scores[:top_concepts],
            "industry": industry_selected,
            "weak_concept": list(reversed(concept_scores[-3:])) if concept_scores else [],
        },
        "stocks": stocks,
        "bottom": bottom,
        "market": {
            "context": market_lines,
            "style": {
                "state": style_info.get("state"),
                "as_of": style_info.get("as_of"),
                "reasons": list(style_info.get("reasons") or [])[:4],
            },
            "benchmark": dict(style_info.get("benchmark") or {}),
            "sentiment": sentiment,
        },
        "meta": {
            "n_concepts": len(concept_scores),
            "n_concepts_excluded": n_concepts_excluded,
            "n_industries": len(industry_scores),
            "n_themes": len(themes),
            "n_members": sum(t.get("n_members") or 0 for t in themes),
            "n_candidates": len(candidates),
            "n_stocks": len(stocks),
        },
        "notes": notes,
        "errors": errors,
    }
