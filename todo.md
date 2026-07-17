# Roadmap

The original pre-rebuild plan that used to live here described a system that
no longer exists; see `CHANGELOG.md` for what actually shipped (v1.0.0
ground-up rebuild, 2026-07-02) and `docs/RESEARCH.md` for the research base.

## Known gaps (flagged in the 2026-07-17 production audit, need a design decision)

- [ ] **Restart reconciliation**: positions/stops live only in memory. On
      restart, pre-existing live positions become unmanaged. Needs a decision:
      reconcile from the journal, from exchange balances, or refuse to start
      live with unknown holdings.
- [ ] **Backtester fill model**: gap-through stops fill at the stop price
      (optimistic), entry-bar stops are skipped, and sizing marks open
      positions at the fill bar's close. Fixing these changes all historical
      backtest numbers — do it once, deliberately, and re-baseline.

## Future work

- [ ] ML meta-filter training (lightgbm) — see `docs/RESEARCH.md`
- [ ] Prometheus metrics endpoint (prometheus-client)
- [ ] Restart-safe order/position persistence beyond the journal
