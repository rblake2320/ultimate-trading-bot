"""SQLite trade journal — durable record of orders, trades, and equity.

Uses only the standard library. The journal is append-only from the bot's
perspective; analytics read from it. WAL mode keeps writes cheap.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional

from ..models import Order, TradeRecord, utc_now

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    exchange_order_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    type TEXT NOT NULL,
    amount REAL NOT NULL,
    price REAL,
    status TEXT NOT NULL,
    filled REAL NOT NULL DEFAULT 0,
    average_price REAL,
    fee REAL NOT NULL DEFAULT 0,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    size REAL NOT NULL,
    pnl REAL NOT NULL,
    fees REAL NOT NULL,
    strategy TEXT,
    exit_reason TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    equity REAL NOT NULL,
    cash REAL,
    open_positions INTEGER,
    details TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades (closed_at);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_snapshots (timestamp);
"""


class TradeJournal:
    def __init__(self, db_path: str = "data/trading_bot.db"):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    def record_order(self, order: Order) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO orders
                   (id, exchange_order_id, symbol, side, type, amount, price,
                    status, filled, average_price, fee, reason, created_at,
                    updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     exchange_order_id=excluded.exchange_order_id,
                     status=excluded.status,
                     filled=excluded.filled,
                     average_price=excluded.average_price,
                     fee=excluded.fee,
                     updated_at=excluded.updated_at""",
                (
                    order.id,
                    order.exchange_order_id,
                    order.symbol,
                    order.side.value,
                    order.type.value,
                    order.amount,
                    order.price,
                    order.status.value,
                    order.filled,
                    order.average_price,
                    order.fee,
                    order.reason,
                    order.created_at.isoformat(),
                    order.updated_at.isoformat(),
                ),
            )
            self._conn.commit()

    def record_trade(self, trade: TradeRecord) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO trades
                   (symbol, entry_price, exit_price, size, pnl, fees,
                    strategy, exit_reason, opened_at, closed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade.symbol,
                    trade.entry_price,
                    trade.exit_price,
                    trade.size,
                    trade.pnl,
                    trade.fees,
                    trade.strategy,
                    trade.exit_reason,
                    trade.opened_at.isoformat(),
                    trade.closed_at.isoformat(),
                ),
            )
            self._conn.commit()

    def snapshot_equity(
        self,
        equity: float,
        cash: Optional[float] = None,
        open_positions: int = 0,
        details: Optional[dict] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO equity_snapshots
                   (timestamp, equity, cash, open_positions, details)
                   VALUES (?,?,?,?,?)""",
                (
                    utc_now().isoformat(),
                    equity,
                    cash,
                    open_positions,
                    json.dumps(details) if details else None,
                ),
            )
            self._conn.commit()

    def log_event(self, level: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (timestamp, level, message) VALUES (?,?,?)",
                (utc_now().isoformat(), level, message),
            )
            self._conn.commit()

    def recent_trades(self, limit: int = 50) -> List[Dict]:
        cur = self._conn.execute(
            "SELECT * FROM trades ORDER BY closed_at DESC LIMIT ?", (limit,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def performance_summary(self) -> Dict[str, float]:
        cur = self._conn.execute(
            """SELECT COUNT(*),
                      COALESCE(SUM(pnl), 0),
                      COALESCE(SUM(fees), 0),
                      COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0)
               FROM trades"""
        )
        count, pnl, fees, wins = cur.fetchone()
        return {
            "trades": count,
            "total_pnl": pnl,
            "total_fees": fees,
            "win_rate": wins / count if count else 0.0,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
