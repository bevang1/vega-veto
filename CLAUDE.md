# CLAUDE.md: project notes

## What this is
Vega Veto: a 24/7 Solana memecoin PAPER trading desk with a local dashboard
(Signal Iris). Four rule-based agents trade fake SOL on live DexScreener data,
behind a deterministic risk engine. Fake money only: no wallet, keys, or order
placement. Adding real trading would be a separate, reviewed step.

It's also a learning project: keep code beginner-readable, and explain the
"why" in comments.

Also contains a US **stock desk** (`stock_desk/`, run by `stock_run.py`) that
day-trades in an Alpaca PAPER account via the `Stock desk` GitHub workflow.
`config.TRADING_URL` must stay the paper URL.

## Environment
- macOS. Python standard library only; no dependencies to run.

## Commands
- Demo (simulated market): `python3 run.py --demo`
- Live data, fake money:   `caffeinate -i python3 run.py`
- Stock desk (needs Alpaca paper keys in env): `python3 stock_run.py --print-only`
- Tests:                   `python3 -m pip install pytest && python3 -m pytest`

## Conventions
- Every tunable number lives in `paper_trader/config.py`.
- Agents only propose buys; `paper_trader/risk.py` decides and handles exits.
- Paper fills must keep charging delay, AMM price impact, and fees
  (`paper_trader/broker.py`), or the P&L stops meaning anything.
- `data/*.db` is git-ignored local history.
