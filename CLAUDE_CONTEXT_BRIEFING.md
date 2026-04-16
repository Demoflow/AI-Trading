# Trading System — Claude Context Briefing
> Paste this entire document at the start of a new Claude chat to get it fully up to speed.

---

## Who I Am & What We're Doing
I'm building and maintaining an **automated multi-strategy trading system** in Python on Windows 11. The system trades via the **Schwab API** (schwab-py library) using OAuth2. I need ongoing help with debugging, new features, and maintenance. The system is production-running with real money on some strategies.

---

## System Location
```
C:/Users/User/Desktop/trading_system/
```

---

## Three Trading Strategies / Accounts

### 1. Aggressive Options — Account 28135437 (Elite/CASH)
- **Script:** `scripts/aggressive_live.py` (v5.3)
- **Config:** `config/elite_config.json`
- **Portfolio:** `config/paper_options.json`
- **Account equity:** ~$7,459
- **Mode:** Currently CASH account (converting from margin). Only `NAKED_LONG` options allowed — no spreads, no credit, no naked shorts until conversion confirmed. Set `account_converting: false` in `elite_config.json` when done.
- **Strategy:** Multi-strategy options scanner. Scans candidates, scores with conviction model, enters directional options (long calls/puts).
- **Key modules in `aggressive/`:**
  - `strategy_engine.py` — signal generation
  - `options_executor.py` — order placement (has circuit breaker for API outages)
  - `contract_selector.py` — picks best contract
  - `risk_manager.py` — sizing and limits
  - `exit_manager.py` — Greeks-aware exits
  - `account_manager.py` — tracks P&L and trades
  - `gex_analyzer.py`, `iv_analyzer.py`, `skew_analyzer.py` — market context
  - `vol_regime.py` — VIX regime detection

### 2. LETF Swing — Account 16167026 (PCRA/Margin)
- **Script:** `scripts/letf_live.py` (v2 Smart Timing)
- **Config:** `config/letf_config.json`
- **Portfolio:** `config/letf_portfolio.json`
- **Account equity:** ~$47,867
- **Strategy:** Swing trades leveraged ETFs (multi-day holds, up to 10 days). Sector rotation — finds strongest/weakest sectors and takes bull/bear LETF positions.
- **Key parameters:**
  - Max position: 12% of equity (~$5,744)
  - Max positions: 10
  - Min conviction: 80
  - Trailing stops: 4% (3x ETFs), 3% (2x ETFs)
  - Profit targets: 12% (3x), 8% (2x)
  - Daily halt if down 3%; weekly halt if down 10%
  - Re-scans sectors every 90 min
- **LETF Universe** (`letf/universe.py`):
  - Nasdaq: TQQQ/SQQQ (3x, underlying QQQ)
  - S&P 500: SPXL/SPXS (3x, underlying SPY)
  - Semis: SOXL/SOXS (3x, underlying SMH)
  - FANG+: FNGU/FNGD (3x, underlying META)
  - Biotech: LABU/LABD (3x, underlying XBI)
  - Financials: FAS/FAZ (3x, underlying XLF)
  - Energy: ERX/ERY (2x, underlying XLE)
  - Gold Miners: NUGT/DUST (2x, underlying GDX)
  - Small Cap: TNA/TZA (3x, underlying IWM)
  - China: YINN/YANG (3x, underlying FXI)
  - Real Estate: DRN/DRV (3x, underlying XLRE)
  - Single-stock (stricter: 90+ conviction, 7% max, 5-day hold):
    - NVDL/NVDS (2x NVDA), TSLL/TSLS (2x TSLA)
- **Key modules in `letf/`:**
  - `sector_analyzer.py` — scores sectors
  - `executor.py` — order placement (has circuit breaker, fixed unclosed file handles)
  - `exit_manager.py` — timing-aware exits
  - `smart_entry.py` — pullback/bounce gates, volume, VIX stability, time windows
  - `earnings_calendar.py` + `earnings_cluster.py` — blocks trades near earnings
  - `universe.py` — sector/ETF definitions

### 3. 0DTE Scalper — Account 16167026 (same PCRA account)
- **Script:** `scripts/scalper_live.py` (v4.0)
- **Portfolio:** `config/paper_scalp.json`
- **Equity:** $25,000 allocated
- **Strategy:** Intraday 0DTE options. Default = premium selling. Directional only on trending/volatile days.
- **Key parameters:**
  - Poll: every 5s
  - Trading hours: 9:35 AM – 3:30 PM (force-close at 3:45 PM)
  - Max 8 trades/day
  - 1 open position per underlying at a time
  - Scans 15 symbols: SPY, QQQ, AAPL, MSFT, NVDA, TSLA, AMZN, META, GOOGL, AMD, NFLX, COIN, BA, JPM, XOM
  - Updates GEX every 5 min, breadth every 2 min, expected move every 10 min
- **Day types** (classified after 30 min): TRENDING, VOLATILE, RANGE, QUIET
- **Supported structures:** LONG_OPTION, IRON_CONDOR, CREDIT_SPREAD, NAKED_PUT, NAKED_CALL, STRADDLE, STRANGLE, RATIO_SPREAD
- **Key modules in `scalper/`:**
  - `signal_engine.py` — entry signal generation
  - `contract_picker.py` — picks contracts for all structure types
  - `risk_manager.py` — sizing, limits, exit logic
  - `executor.py` — opens/closes positions, tracks P&L
  - `realtime_data.py` — polls quotes, builds 1m/5m candles
  - `day_classifier.py` — classifies day type from SPY candles + VIX
  - `gex_intraday.py` — intraday GEX regime
  - `market_internals.py` — breadth tracking
- **Last trade results (2026-04-01):** 3 trades, 3 wins, $106.90 P&L

### 4. PCRA RSI Scalper (separate simple script)
- **Script:** `scripts/pcra_rsi_scalper.py` (v2.0)
- **Account:** 16167026
- **Strategy:** Trend-aligned oversold bounce on TQQQ or SQQQ
  - Uptrend → TQQQ when RSI(14) < 20
  - Downtrend → SQQQ when RSI(14) < 20
- **Entry:** RSI crosses below 20 + volume ≥ 1.5× 10-bar avg + SPY trend gate
- **Exit:** RSI > 30, price at VWAP, -1.5% stop, or 2:50 PM CT time stop
- **Position size:** 25% of equity, 1 trade/day, 5-min bars, 30s poll

---

## Infrastructure

### Broker Auth
- **Library:** `schwab-py`
- **Token file:** `config/schwab_token.json`
- **Token expiry:** Every 7 days — must re-run `python scripts/authenticate_schwab.py`
- **Auth module:** `data/broker/schwab_auth.py` — handles `refresh_token_authentication_error` with a `logger.critical` alert
- **Token keepalive:** `scripts/token_keepalive.py` — checks/refreshes token, alerts on expiry
- **Account credentials:** In `.env` file (`SCHWAB_ACCOUNT_NUMBER`, etc.)

### Database
- **PostgreSQL + TimescaleDB** — database name: `trading_db`
- Credentials stored in `.env`
- Data ingestion: `data/ingestion/`

### Scheduling / Launch
- **Master scheduler:** `scripts/master_scheduler.py evening|morning|full`
  - Evening: token keepalive → price data backfill → aggressive scan → LETF scan
  - Morning: scan refresh + live script launch
  - Full: all of the above
- **`.bat` files** in root for quick launch: `SCALPER.bat`, `LETF.bat`, `LIVE_TRADE.bat`, `MORNING_TRADE.bat`, `FULL_STARTUP.bat`, `EVENING_SCAN.bat`, etc.

### Logging
- **loguru** throughout — setup via `utils/logging_setup.py`
- Logs go to `logs/` directory

### Key Config Files
| File | Purpose |
|---|---|
| `config/elite_config.json` | Aggressive options account config |
| `config/letf_config.json` | LETF account config |
| `config/letf_roth_config.json` | LETF Roth IRA config |
| `config/paper_options.json` | Options portfolio state |
| `config/letf_portfolio.json` | LETF portfolio state |
| `config/letf_roth_portfolio.json` | LETF Roth portfolio state |
| `config/paper_scalp.json` | Scalper portfolio state |
| `config/aggressive_trades.json` | Pending aggressive trade candidates |
| `config/universe.csv` | Full symbol universe |
| `config/schwab_token.json` | OAuth2 token |
| `config/breaker_state.json` | Circuit breaker state |
| `config/pcra_scalper_state.json` | PCRA RSI scalper daily state |

---

## Bugs Fixed (2026-03-31)
All of these are already applied — do not re-apply:

1. `diagnose.py` — unicode escape error fixed (docstring → raw string `r"""`)
2. Deleted stale empty files `0.95`, `25`, `30` from project root
3. `scripts/aggressive_live.py` — fixed `NameError: _dt not defined` (replaced `_dt.datetime.now()` → `datetime.now()`)
4. `scripts/aggressive_live.py` — fixed indentation bug where `acct_mgr.record_trade()` was inside `except` block instead of `if g_exit:` block
5. `aggressive/options_executor.py` — added **circuit breaker** (`_is_api_available`, `_record_api_failure`): 5 consecutive failures → 5-min backoff; `refresh_token_authentication_error` → 24h backoff
6. `aggressive/options_executor.py` — replaced hardcoded account `"28135437"` with `os.getenv("SCHWAB_ACCOUNT_NUMBER", "28135437")`; added to `.env`
7. `aggressive/options_executor.py` — added missing `timedelta` import
8. `data/broker/schwab_auth.py` — better error message for token expiry vs generic failures
9. `scripts/token_keepalive.py` — `logger.critical` alert on `refresh_token_authentication_error`
10. `letf/executor.py` — fixed unclosed file handles (bare `open()` → `with open(...)`)
11. `letf/executor.py` — added same circuit breaker pattern to `get_real_balance`

---

## Key Constraints & Gotchas
- **Schwab token expires every 7 days** — always check before a live session; `token_keepalive.py` handles this
- **Account 28135437 is a CASH account** currently converting from margin — only NAKED_LONG options until `account_converting` is set to `false` in `elite_config.json`
- **LETF single-stock ETFs** (NVDL/NVDS, TSLL/TSLS) have stricter rules: 90+ conviction, 7% max position, 5-day max hold
- **0DTE scalper** skips first 5 min of market (no entries before 9:35 AM) and stops entries at 3:30 PM
- **No new entries** if `daily_halt` triggered (LETF down 3%) or `weekly_halt` (LETF down 10%)
- **Circuit breakers** are active on both `options_executor.py` and `letf/executor.py` — check `config/breaker_state.json` if API calls seem blocked
- **Paper vs Live:** All scripts default to paper mode; pass `--live` flag for real orders

---

## Typical Workflow
1. **Evening:** Run `EVENING_SCAN.bat` → generates `aggressive_trades.json` + LETF candidates
2. **Morning:** Run `MORNING_TRADE.bat` → launches aggressive + LETF live scripts
3. **Intraday scalper:** Run `SCALPER.bat` separately (9:35 AM – 3:30 PM)
4. **Status check:** `SCALPER_STATUS.bat` or `DASHBOARD.bat`
5. **Token refresh:** Run `REFRESH_TOKEN.bat` if approaching 7-day expiry

---

## Current Portfolio State (as of 2026-04-01/2026-04-14)
- **Aggressive options (28135437):** No open positions. Equity ~$7,459.
- **LETF (16167026):** All positions closed. Equity ~$47,867. Cash ~$47,378.
- **Scalper:** No open positions. Cash ~$25,107. Last session: 3W/0L, +$106.90.

---

## Project Structure Summary
```
trading_system/
├── aggressive/        # Options strategy modules (30+ files)
├── scalper/           # 0DTE scalper modules (8 files)
├── letf/              # LETF swing modules (8 files)
├── strategy/          # Shared strategy engines
├── data/
│   ├── broker/        # schwab_auth.py, schwab_executor.py
│   ├── ingestion/     # market data pipeline
│   ├── storage/       # DB layer
│   └── quality/       # data QA
├── utils/             # logging, market calendar, options selector, etc.
├── analysis/          # signal generation, ML scoring
├── config/            # all JSON config and portfolio state files
├── scripts/           # all runnable entry points
├── logs/              # loguru output
└── *.bat              # Windows quick-launch scripts
```
