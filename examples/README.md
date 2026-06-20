# hodlbook examples

## `demo.py` — end-to-end story, fully offline

A single runnable script that exercises the whole hodlbook stack against an
in-memory [`moto`](https://github.com/getmoto/moto) DynamoDB. **No AWS
credentials and no network access are required** — everything runs locally.

The demo tells a small crypto paper-trading story:

1. **Open a portfolio** with $100,000 of starting cash.
2. **Buy BTC and ETH over a few "days"**, advancing the mock price provider's
   `drift_step` between buys so prices move — the second BTC buy lands at a
   higher price, demonstrating a **weighted-average cost basis**.
3. **Value the portfolio** marked to market: per-asset price, market value, and
   unrealized P&L, rolled up with cash into a total.
4. **Take a profit**: sell part of the BTC position after the price rises and
   print the **realized P&L** from the trade result.
5. **Review the trade history**, most-recent-first, via repository pagination.
6. **Arm a price alert** (BTC `ABOVE` $60,000), push the price across the
   threshold, run the alert evaluator, and print the **fired alert**.

All monetary math uses `Decimal` — never `float`.

## Running it

From the repository root:

```bash
pip install -e ".[dev]"
python examples/demo.py
```

`moto` is a development dependency, so it is installed by the `[dev]` extra.
The script prints a readable trace of each step and ends with a success line.
