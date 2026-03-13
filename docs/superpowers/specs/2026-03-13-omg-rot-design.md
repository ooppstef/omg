# OMG ROT Design

Date: 2026-03-13
Status: Draft reviewed with user
Scope: `omg rot` post-close sector rotation review and next-day direction prediction

## Goal

`omg rot` is intended for rotation markets, not clear main-trend markets.

The command should help answer:

- Which concept sectors are most likely to receive capital rotation on the next trading day?
- Among those sectors, which are "pullback and return" candidates and which are "warming-up relay" candidates?
- Which sectors should be excluded from observation because their structure has degraded?

The command is not intended for:

- intraday monitoring
- minute-level timing
- detecting whether the whole market is in a trend market or rotation market

The user judges market regime first. `omg rot` is used only after the user has already decided the market is behaving like a rotation market.

## Data Source Constraints

The design must stay within `adata` capabilities available in this environment.

Relevant `adata 2.9.5` interfaces:

- `adata.stock.info.all_concept_code_ths()`
  - Returns the full THS concept universe.
  - Expected fields include `name`, `index_code`, `concept_code`.
- `adata.stock.market.get_market_concept_ths(index_code)`
  - Returns concept daily history.
  - Expected usable fields include `trade_date`, `close`, `amount`, `change_pct`.
- `adata.sentiment.hot.hot_concept_20_ths()`
  - Returns the current THS hot concept ranking only.
  - Expected fields include `rank`, `concept_name`, `concept_code`, `change_pct`, `hot_value`, `hot_tag`.
- `adata.stock.info.concept_constituent_ths(index_code=...)`
  - Returns concept constituent stocks.
- `adata.stock.market.list_market_current(code_list)`
  - Returns current daily performance for constituent stocks.

Known limitation:

- No historical hot-ranking snapshot interface has been identified.
- Therefore, `rot` must not be defined as "replaying historical hot lists".
- The correct path is to infer rotation from concept daily-strength sequences, then use the current hot list only as a weak confirmation signal.

## Design Principles

1. Use daily data only.
2. The full concept universe is the candidate pool.
3. The same-day hot list is only a weighting factor, not a hard filter.
4. Constituent-stock behavior participates in scoring, but concept-level evidence remains primary.
5. The primary output is "pullback and return" candidates.
6. "Warming-up relay" is a secondary output.

## High-Level Flow

1. Load the full concept universe from `all_concept_code_ths()`.
2. For each concept, load enough daily history to compute recent strength and adjustment patterns.
3. Compute a concept daily strength series relative to market benchmark.
4. Merge same-day hot-list information as a weak bonus factor.
5. Load constituent stocks for shortlisted concepts and use same-day stock behavior as confirmation.
6. Split the result into:
   - pullback-and-return priority
   - warming-up relay
   - drop from watch

## Core Model

### 1. Concept Daily Strength

The recommended base metric should reuse the existing idea already present in `stocks/ana.py`:

- excess return relative to HS300
- amount-based scaling factor

Recommended form:

`daily_strength = excess_return * amount_multiplier`

Where:

- `excess_return = concept_change_pct - benchmark_change_pct`
- `amount_multiplier` should be a log-scaled function of amount relative to a rolling median, to avoid large concepts dominating mechanically

This is better than the current `rot` model based only on cumulative return and volatility because it can express:

- whether the concept is actually beating the market
- whether the move had meaningful participation
- whether strength is improving or fading over time

### 2. Benchmark

Use HS300 as the benchmark, consistent with the existing concept-strength implementation in `stocks/ana.py`.

Recommended benchmark source:

- `adata.stock.market.get_market_index(index_code='000300', start_date=...)`

### 3. Observation Windows

Recommended default windows:

- long context window: 8 trading days
- recent adjustment window: last 2 trading days
- warming-up window: last 3 trading days

These are defaults only. They should be configurable at the module level.

## Output Groups

### Pullback-And-Return Priority

This is the main group and should be sorted first.

A concept belongs here when:

- it had real strength in the last 8 trading days
- it has pulled back or cooled off recently
- the pullback has not structurally broken the trend
- its constituent stocks still show enough confirmation

### Warming-Up Relay

This is a secondary group.

A concept belongs here when:

- it is not a valid pullback-return candidate
- but its recent daily strength is rising
- and the move shows broadening or improving constituent confirmation

### Drop From Watch

This group contains concepts that previously had attention but now fail both models, especially when:

- strength has faded materially
- recent pullback is too deep
- constituent confirmation is weak
- same-day heat and participation both degrade

## Pullback Score

The pullback score should be the primary ranking score.

Recommended initial weight split:

- prior-strength foundation: 40%
- pullback quality: 30%
- constituent confirmation: 20%
- heat retention: 10%

The user explicitly requested that heat retention remain low weight, because true return candidates may no longer be visibly hot on the same day.

### Pullback Score Components

#### A. Prior-Strength Foundation (40%)

Purpose:

- confirm the concept really was strong recently
- prevent false positives from weak concepts that simply bounced

Suggested signals:

- number of days in the last 8 trading days where `daily_strength` was in top market percentile
- max daily strength in the last 8 trading days
- mean strength of the strongest 2 days in the last 8 trading days

Hard gate:

- a concept must have at least 1 to 2 strong days in the last 8 trading days
- otherwise it cannot enter the pullback group and should only compete in warming-up

#### B. Pullback Quality (30%)

Purpose:

- identify healthy cooling, not breakdown

Suggested signals:

- last 2 trading days show negative or weaker relative strength
- the pullback magnitude is limited
- the latest close or latest strength remains above rolling market median
- no large collapse day that invalidates the structure

Interpretation:

- mild pullback after strong days is good
- deep damage is bad

#### C. Constituent Confirmation (20%)

Purpose:

- confirm that the concept has not internally collapsed

Suggested inputs from `concept_constituent_ths` + `list_market_current`:

- average return of top 5 strongest constituents
- positive-return ratio among the top sampled constituents
- concentration of strength in the top leaders

Suggested interpretation:

- a good pullback candidate should still have some active leaders
- not all constituent stocks need to be strong
- but a sector whose entire leading layer is weak should be downgraded

#### D. Heat Retention (10%)

Purpose:

- weak confirmation only

Suggested signals:

- present in same-day `hot_concept_20_ths`
- hot rank percentile
- hot value percentile

Important rule:

- absence from the hot list should not be a hard penalty
- presence on the hot list is a bonus, not a requirement

## Warming-Up Score

This is the secondary ranking score.

Recommended initial weight split:

- 3-day strength slope: 45%
- amount expansion: 20%
- heat improvement: 15%
- constituent diffusion confirmation: 20%

### Warming-Up Logic

The concept should enter this group when:

- it fails the pullback hard gate
- but recent daily strength has clearly improved over the last 2 to 3 days
- participation or amount is rising
- constituents are not relying on a single isolated stock move

This group is intended to catch early relay directions, not mature leaders.

## Drop-From-Watch Logic

A concept should be classified here when:

- prior strength existed but has materially deteriorated
- recent pullback exceeded acceptable damage
- constituent confirmation is poor
- and there is no meaningful same-day heat or improvement signal

This group is useful because excluding bad candidates is as important as ranking good ones.

## Recommended Result Format

The command should output groups first, then ranking within each group.

Recommended sections:

1. `回流优先`
2. `升温接力`
3. `放弃观察`

For each ranked concept, print:

- concept name
- group score
- short reason summary
- recent strength summary
- same-day heat summary if available
- key constituent confirmation summary

Example reason phrases:

- `前强后调，今日热度弱化但龙头承接仍在`
- `近3日强度抬升，成交额放大，具备接力迹象`
- `前期走强但回撤过深，板块内部确认不足`

## Explanation Layer

Each result should be explainable from the printed fields. The model should avoid opaque total scores without reasons.

Minimum explanation items:

- why it was considered prior-strong or not
- whether recent pullback is healthy or damaging
- whether same-day heat helped or did not matter
- whether constituents confirmed the concept

## Parameterization

The implementation should keep these parameters easy to tune:

- lookback days
- strong-day threshold
- pullback depth threshold
- pullback score weights
- warming-up score weights
- constituent sample size
- heat bonus weight

These values are intentionally not finalized. The user will likely adjust them after observing live results.

## Suggested Implementation Structure

Recommended refactor shape:

- `build_concept_universe()`
- `load_concept_daily_data(index_codes, start_date)`
- `compute_benchmark_series(start_date)`
- `compute_concept_daily_strength(hist_df, benchmark_df)`
- `compute_pullback_score(concept_features, hot_row, stock_features)`
- `compute_warming_score(concept_features, hot_row, stock_features)`
- `classify_concept(result)`
- `format_rot_report(groups)`

This keeps calculation, classification, and output formatting separate.

## What The Current Implementation Gets Wrong

The current `stocks/rotation_analysis.py` implementation is not a real rotation model because:

- it starts only from the current hot list
- it does not score the full concept universe
- it does not model prior strength explicitly
- it uses heat ranking as the candidate source rather than a weak confirmation signal
- it treats "5-day cumulative return + volatility" as sufficient, which is too coarse for rotation analysis

## Next Step

After user approval of this design document, the next step should be to write an implementation plan and then refactor `omg rot` to follow this model.
