# ultimate-trading-bot — rules for AI-assisted work

Two rules, each earned by the top mistakes found in the 2026-07-17
production audit:

1. **Deterministic tests never go behind the `integration` marker.** The
   integration suite is advisory in CI; anything deterministic that hides
   there (fail-open guarantees, parse logic, safety gates) is effectively
   untested — a regression merges green. If a test needs no live network
   service, it belongs in the gating suite, even if it lives next to
   network tests. Corollary: every safety feature named in the README
   (two-key gate, KILL switch, stop propagation) must have a gating test
   that fails when the feature is deleted.

2. **Order mutations must be idempotent or guarded.** A network timeout is
   not a failure — the venue may have accepted the order. Never blind-retry
   an order placement (use `clientOrderId` + lookup), and never let a
   periodic loop re-fire an action (close, cancel) while the previous
   attempt is still in flight (guard by order id, clear on terminal state).

House policy (see README/tests): real market data over mocks — a scripted
stand-in is allowed only when the scenario is physically untestable live
(e.g. forcing a venue to drop responses), and must say so in its docstring.
