"""Event-driven backtester with backtest/live parity.

Parity guarantees:
  - Strategies are the same objects used live; their vectorized causal
    ``signals()`` frames drive the simulation.
  - The same :class:`RiskManager` gates and sizes every entry.
  - A signal generated on bar *i* (close) executes at bar *i+1*'s OPEN with
    slippage and fees — no lookahead by construction.
  - Stops/targets are evaluated intra-bar against high/low, stop-first when
    both are touched in one bar (the conservative assumption).

Costs modeled: taker fee both sides, base slippage in bps scaled up with
the bar's volatility (research: under-modeled slippage is the #1 cause of
backtest-to-live failure).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

import pandas as pd

from ..indicators import ta
from ..models import Signal, SignalAction, TradeRecord
from ..risk.risk_manager import RiskManager
from ..strategies.base import Strategy, validate_ohlcv

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    initial_cash: float = 10_000.0
    fee_rate: float = 0.0026  # taker, both sides
    slippage_bps: float = 5.0
    vol_slippage_mult: float = 2.0  # extra slippage per unit of bar range
    max_open_positions: int = 5
    risk: dict = field(default_factory=dict)


@dataclass
class BacktestResult:
    metrics: Dict[str, float]
    equity_curve: pd.Series
    trades: List[TradeRecord]
    config: BacktestConfig

    def summary(self) -> str:
        m = self.metrics
        lines = [
            "== Backtest result ==",
            f"  bars:            {int(m['bars'])}",
            f"  trades:          {int(m['num_trades'])}",
            f"  total return:    {m['total_return']:+.2%}",
            f"  CAGR:            {m['cagr']:+.2%}",
            f"  Sharpe:          {m['sharpe']:.2f}",
            f"  Sortino:         {m['sortino']:.2f}",
            f"  max drawdown:    {m['max_drawdown']:.2%}",
            f"  win rate:        {m['win_rate']:.1%}",
            f"  profit factor:   {m['profit_factor']:.2f}",
            f"  fees paid:       {m['fees_paid']:.2f}",
            f"  time in market:  {m['exposure']:.1%}",
        ]
        return "\n".join(lines)


@dataclass
class _OpenPosition:
    symbol: str
    size: float
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    entry_fees: float
    opened_at: pd.Timestamp


class Backtester:
    def __init__(self, strategy: Strategy, config: Optional[BacktestConfig] = None):
        self.strategy = strategy
        self.config = config or BacktestConfig()

    # ------------------------------------------------------------------ #

    def run(self, data: Dict[str, pd.DataFrame]) -> BacktestResult:
        cfg = self.config
        for symbol, df in data.items():
            validate_ohlcv(df)
            if len(df) <= self.strategy.warmup:
                raise ValueError(
                    f"{symbol}: {len(df)} bars < warmup {self.strategy.warmup}"
                )

        signals = {s: self.strategy.signals(df) for s, df in data.items()}
        atrs = {
            s: ta.atr(df["high"], df["low"], df["close"], 14) for s, df in data.items()
        }
        # Volatility proxy for slippage scaling: bar range / close.
        bar_ranges = {
            s: ((df["high"] - df["low"]) / df["close"]).fillna(0.0)
            for s, df in data.items()
        }

        risk = RiskManager(cfg.risk)
        cash = cfg.initial_cash
        risk.update_equity(cash)
        positions: Dict[str, _OpenPosition] = {}
        trades: List[TradeRecord] = []
        fees_paid = 0.0
        bars_in_market = 0

        all_ts = sorted(set().union(*[df.index for df in data.values()]))
        equity_points: List[Tuple[pd.Timestamp, float]] = []

        for t_idx, ts in enumerate(all_ts):
            # ---- 1. execute previous-bar decisions at this bar's open ----
            for symbol, df in data.items():
                if ts not in df.index:
                    continue
                loc = df.index.get_loc(ts)
                if loc == 0:
                    continue
                prev_ts = df.index[loc - 1]
                sig_row = signals[symbol].loc[prev_ts]
                bar_open = float(df["open"].loc[ts])
                slip = self._slippage(bar_ranges[symbol].loc[prev_ts])

                if sig_row["action"] == -1 and symbol in positions:
                    cash, fees_paid = self._close(
                        positions,
                        trades,
                        risk,
                        symbol,
                        bar_open * (1 - slip),
                        ts,
                        "signal_exit",
                        cash,
                        fees_paid,
                        was_stop=False,
                    )

                elif sig_row["action"] == 1 and symbol not in positions:
                    fill_price = bar_open * (1 + slip)
                    equity = self._equity(cash, positions, data, ts)
                    prev_loc = max(0, loc - 1)
                    risk.update_returns(
                        symbol, df["close"].iloc[: prev_loc + 1]
                    )
                    atr_val = atrs[symbol].iloc[prev_loc]
                    signal = Signal(
                        symbol=symbol,
                        action=SignalAction.BUY,
                        confidence=float(sig_row["confidence"]),
                        price=fill_price,
                        strategy=self.strategy.name,
                        stop_loss=(
                            None
                            if pd.isna(sig_row["stop_loss"])
                            else float(sig_row["stop_loss"])
                        ),
                        take_profit=(
                            None
                            if pd.isna(sig_row["take_profit"])
                            else float(sig_row["take_profit"])
                        ),
                    )
                    open_notionals = {
                        s: p.size
                        * float(data[s]["close"].iloc[data[s].index.get_loc(ts) - 1])
                        if ts in data[s].index and data[s].index.get_loc(ts) > 0
                        else p.size * p.entry_price
                        for s, p in positions.items()
                    }
                    decision = risk.evaluate_entry(
                        signal,
                        equity,
                        open_notionals,
                        atr=None if pd.isna(atr_val) else float(atr_val),
                        max_open_positions=cfg.max_open_positions,
                        now=ts.to_pydatetime(),
                    )
                    if decision.approved:
                        qty = min(decision.quantity, cash / (fill_price * (1 + cfg.fee_rate)))
                        cost = qty * fill_price
                        fee = cost * cfg.fee_rate
                        if qty > 0 and cost >= risk.min_notional:
                            cash -= cost + fee
                            fees_paid += fee
                            positions[symbol] = _OpenPosition(
                                symbol=symbol,
                                size=qty,
                                entry_price=fill_price,
                                stop_loss=decision.stop_loss,
                                take_profit=decision.take_profit,
                                entry_fees=fee,
                                opened_at=ts,
                            )

            # ---- 2. intra-bar stop/target checks on this bar ----
            for symbol in list(positions):
                df = data[symbol]
                if ts not in df.index:
                    continue
                pos = positions[symbol]
                if pos.opened_at == ts:
                    continue  # no same-bar exit on entry bar
                high = float(df["high"].loc[ts])
                low = float(df["low"].loc[ts])
                slip = self._slippage(bar_ranges[symbol].loc[ts])
                # Conservative: if both stop and target are inside the bar,
                # assume the stop was hit first.
                if pos.stop_loss is not None and low <= pos.stop_loss:
                    cash, fees_paid = self._close(
                        positions,
                        trades,
                        risk,
                        symbol,
                        pos.stop_loss * (1 - slip),
                        ts,
                        "stop_loss",
                        cash,
                        fees_paid,
                        was_stop=True,
                    )
                elif pos.take_profit is not None and high >= pos.take_profit:
                    cash, fees_paid = self._close(
                        positions,
                        trades,
                        risk,
                        symbol,
                        pos.take_profit * (1 - slip),
                        ts,
                        "take_profit",
                        cash,
                        fees_paid,
                        was_stop=False,
                    )

            # ---- 3. mark to market ----
            equity = self._equity(cash, positions, data, ts)
            risk.update_equity(equity, now=ts.to_pydatetime())
            equity_points.append((ts, equity))
            if positions:
                bars_in_market += 1

        # Liquidate any remaining positions at the final close.
        final_ts = all_ts[-1]
        for symbol in list(positions):
            df = data[symbol]
            last_close = float(df["close"].iloc[-1])
            cash, fees_paid = self._close(
                positions,
                trades,
                risk,
                symbol,
                last_close,
                final_ts,
                "end_of_backtest",
                cash,
                fees_paid,
                was_stop=False,
            )

        curve = pd.Series(
            [e for _, e in equity_points],
            index=pd.DatetimeIndex([t for t, _ in equity_points]),
        )
        metrics = compute_metrics(
            curve, trades, cfg.initial_cash, fees_paid, bars_in_market
        )
        return BacktestResult(
            metrics=metrics, equity_curve=curve, trades=trades, config=cfg
        )

    # ------------------------------------------------------------------ #

    def _slippage(self, bar_range: float) -> float:
        cfg = self.config
        base = cfg.slippage_bps / 10_000.0
        if pd.isna(bar_range):
            return base
        return base * (1.0 + cfg.vol_slippage_mult * float(bar_range))

    @staticmethod
    def _equity(
        cash: float,
        positions: Dict[str, _OpenPosition],
        data: Dict[str, pd.DataFrame],
        ts: pd.Timestamp,
    ) -> float:
        total = cash
        for symbol, pos in positions.items():
            df = data[symbol]
            if ts in df.index:
                price = float(df["close"].loc[ts])
            else:
                idx = df.index.searchsorted(ts, side="right") - 1
                price = float(df["close"].iloc[max(idx, 0)])
            total += pos.size * price
        return total

    def _close(
        self,
        positions: Dict[str, _OpenPosition],
        trades: List[TradeRecord],
        risk: RiskManager,
        symbol: str,
        price: float,
        ts: pd.Timestamp,
        reason: str,
        cash: float,
        fees_paid: float,
        was_stop: bool,
    ) -> Tuple[float, float]:
        pos = positions.pop(symbol)
        proceeds = pos.size * price
        fee = proceeds * self.config.fee_rate
        cash += proceeds - fee
        fees_paid += fee
        record = TradeRecord(
            symbol=symbol,
            entry_price=pos.entry_price,
            exit_price=price,
            size=pos.size,
            pnl=(price - pos.entry_price) * pos.size - fee - pos.entry_fees,
            fees=fee + pos.entry_fees,
            strategy=self.strategy.name,
            exit_reason=reason,
            opened_at=pos.opened_at.to_pydatetime(),
            closed_at=ts.to_pydatetime(),
        )
        trades.append(record)
        risk.record_trade_result(record, was_stop_loss=was_stop, now=ts.to_pydatetime())
        return cash, fees_paid


# ---------------------------------------------------------------------- #
# Metrics
# ---------------------------------------------------------------------- #


def infer_bars_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 3:
        return 365.0
    deltas = index.to_series().diff().dropna().dt.total_seconds()
    median = float(deltas.median())
    if median <= 0:
        return 365.0
    return 365.25 * 24 * 3600 / median


def compute_metrics(
    curve: pd.Series,
    trades: List[TradeRecord],
    initial_cash: float,
    fees_paid: float,
    bars_in_market: int,
) -> Dict[str, float]:
    if curve.empty:
        return {}
    final = float(curve.iloc[-1])
    total_return = final / initial_cash - 1.0
    bars_per_year = infer_bars_per_year(curve.index)
    years = max(len(curve) / bars_per_year, 1e-9)
    cagr = (
        (final / initial_cash) ** (1.0 / years) - 1.0 if final > 0 else -1.0
    )

    rets = curve.pct_change().dropna()
    if len(rets) > 1 and rets.std(ddof=0) > 0:
        sharpe = float(rets.mean() / rets.std(ddof=0) * math.sqrt(bars_per_year))
    else:
        sharpe = 0.0
    downside = rets[rets < 0]
    if len(downside) > 1 and downside.std(ddof=0) > 0:
        sortino = float(rets.mean() / downside.std(ddof=0) * math.sqrt(bars_per_year))
    else:
        sortino = sharpe if sharpe <= 0 else float("inf") if rets.mean() > 0 else 0.0

    running_max = curve.cummax()
    drawdowns = (running_max - curve) / running_max
    max_dd = float(drawdowns.max()) if len(drawdowns) else 0.0

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)

    return {
        "bars": float(len(curve)),
        "num_trades": float(len(trades)),
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": (
            gross_profit / gross_loss if gross_loss > 0 else float("inf")
        )
        if trades
        else 0.0,
        "fees_paid": fees_paid,
        "exposure": bars_in_market / len(curve) if len(curve) else 0.0,
        "final_equity": final,
    }


def walk_forward_splits(
    df: pd.DataFrame, train_bars: int, test_bars: int
) -> Iterator[Tuple[pd.DataFrame, pd.DataFrame]]:
    """Yield rolling (train, test) windows for walk-forward validation."""
    start = 0
    n = len(df)
    while start + train_bars + test_bars <= n:
        train = df.iloc[start : start + train_bars]
        test = df.iloc[start + train_bars : start + train_bars + test_bars]
        yield train, test
        start += test_bars
