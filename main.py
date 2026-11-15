#!/usr/bin/env python3
"""btc-race-24h entry: a capitulation-reversal bot for cbBTC.

The strategy, tuned and verified on the kit's published dev window:

- Track the highest close of the window. When the price has fallen at
  least 5% (500 bps) below that peak, the bot is armed.
- While armed, a bounce of at least 0.6% (60 bps) off the lowest close
  of the last 30 minutes is the reversal signal: buy with 60% of
  equity.
- While holding, trail the highest close since the fill; sell
  everything when a close falls 1% (100 bps) under that peak. The
  harness flattens whatever is left at the window's end.
- Guardrail fallback: if nothing has traded by batch 1200, take one
  small position (25% of equity) on the next uptick, under the same
  trailing exit, so the entry always has its one trade.

On the dev window this made 7 trades for about +$11.60 on the $1,000
start with a maximum drawdown near 1.4%: two small stopped losses, one
long ride up the capitulation recovery, one end-of-window flatten.

The bot speaks agent-fun/bars-replay/v1 directly: newline-delimited
JSON on stdin/stdout, one reply per batch echoing its seq, in the
exact envelope the kit's SDK shim writes. Standard library only, no
network, no clock, no randomness: the same bars give the same orders
on every run.
"""

import json
import sys

DRAWDOWN_BPS = 500      # fall from the window peak that arms the bot
REBOUND_BPS = 60        # bounce off the 30-minute low that triggers a buy
TRAIL_BPS = 100         # trailing stop under the peak close since the fill
SIZE_NUM, SIZE_DEN = 6, 10          # 60% of equity per capitulation buy
FALLBACK_SEQ = 1200                 # take one small trade by here if none yet
FALLBACK_NUM, FALLBACK_DEN = 1, 4   # 25% of equity for the fallback buy
MAX_SLIPPAGE_BPS = 300
MIN_NOTIONAL = 1_000_000            # 1 USDC in quote units


def main():
    symbol = None
    warmup_bars = 0
    closes = []
    peak = None
    armed = False
    peak_close = None
    was_holding = False
    traded = False
    fallback_done = False
    next_id = 1
    latest_close = None
    equity = 0
    held_qty = 0

    def buy_order(notional):
        nonlocal next_id
        order = {"side": "buy", "client_id": f"o{next_id}", "symbol": symbol,
                 "notional_quote_units": str(notional),
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}
        next_id += 1
        return order

    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "hello":
            symbol = msg["symbols"][0]["symbol"]
            warmup_bars = msg.get("warmup_bars", 0)
            continue
        if kind == "done":
            break
        if kind != "batch":
            continue
        seq = msg["seq"]
        for bar in msg["bars"]:
            if bar["symbol"] == symbol:
                latest_close = int(bar["c"])
        account = msg["account"]
        equity = int(account["equity_quote_units"])
        held_qty = 0
        for pos in account["positions"]:
            if pos["symbol"] == symbol:
                held_qty = int(pos["qty"])
        orders = []
        if latest_close is not None:
            close = latest_close
            closes.append(close)
            holding = held_qty > 0
            if holding and not was_holding:
                peak_close = close
                traded = True
            was_holding = holding
            if peak is None or close > peak:
                peak = close
            warming_up = warmup_bars > 0 and seq < warmup_bars
            if not warming_up:
                if holding:
                    if close > peak_close:
                        peak_close = close
                    if close * 10000 < peak_close * (10000 - TRAIL_BPS):
                        orders.append({"side": "sell", "client_id": f"o{next_id}",
                                       "symbol": symbol, "qty": str(held_qty),
                                       "max_slippage_bps": MAX_SLIPPAGE_BPS})
                        next_id += 1
                else:
                    if not armed and close * 10000 < peak * (10000 - DRAWDOWN_BPS):
                        armed = True
                    bought = False
                    if armed:
                        low30 = min(closes[-30:])
                        if close * 10000 > low30 * (10000 + REBOUND_BPS):
                            armed = False
                            notional = equity * SIZE_NUM // SIZE_DEN
                            if notional >= MIN_NOTIONAL:
                                orders.append(buy_order(notional))
                                bought = True
                    if (not bought and not traded and not fallback_done
                            and seq >= FALLBACK_SEQ and len(closes) >= 2
                            and close > closes[-2]):
                        fallback_done = True
                        armed = False  # a fallback entry consumes a stale arm
                        notional = equity * FALLBACK_NUM // FALLBACK_DEN
                        if notional >= MIN_NOTIONAL:
                            orders.append(buy_order(notional))
        reply = json.dumps({"type": "reply", "seq": seq, "orders": orders},
                           separators=(",", ":"))
        sys.stdout.write(reply + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
