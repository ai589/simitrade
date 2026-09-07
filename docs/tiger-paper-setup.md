# Tiger paper trading — first run

`src/push_orders.py` has never sent an order. Not one, on paper or live. Every safety rail
in it (the `orders_sent.json` dedupe, the existing-position skip, the `allow_live` gate,
`max_orders_per_run`) is written but untested. So the first four weeks run on paper.

## One-off setup

1. **Install the SDK**

   ```
   pip install tigeropen
   ```

2. **Register for the Open API** at https://quant.itigerup.com — generate an RSA key pair
   there and save the private key somewhere outside this repo (it must never end up in the
   Vercel mirror). Note your `tiger_id` and your **paper** account number.

3. **Create the config**

   ```
   copy state\tiger_config.example.json state\tiger_config.json
   ```

   Fill in `tiger_id`, `private_key_path`, `paper_account`. Leave `live_account` empty and
   `allow_live` false. The rest of the file is already set to the traded strategies and the
   sizing you use. `tiger_config.json` and `orders_sent.json` are gitignored and excluded
   from `publish_silent.bat`'s robocopy — check that stays true if you edit either.

4. **Set `account_size`** to what you would actually trade, not a round number you don't
   have. Every share count on the dashboard and in the brief derives from it.

## First run

```
python src/push_orders.py                 # dry run — read the table
python src/push_orders.py --check --paper # connect, read the account back, place nothing
python src/push_orders.py --paper         # send
```

`--check` separates "my credentials are wrong" from "the order was rejected". Run it once
before the first send.

## What to verify on that first paper send

- **In the Tiger app:** each entry order arrived with *both* attached legs — take-profit at
  the target and stop-loss at the stop — at the prices the brief printed, in the right
  quantity. A bracket order that lost its legs is the dangerous failure: a position with no
  stop, held while you sleep.
- **The dedupe works:** run `--paper` a second time immediately. It must place nothing and
  print `already sent` for each name. If it re-sends, stop and fix that before going live.
- **The big one — does a DAY limit survive until the open?** Place at ~15:00 SGT and check
  at 21:30 SGT that the order is still working and fills near the open. This is the single
  assumption the whole schedule rests on: signal at the US close, order queued during your
  SGT daytime, fill at the next US open — which is exactly what the backtest models.

  If Tiger rejects or cancels a DAY order placed pre-session, the fallbacks are `GTC` time
  in force, or a market-on-open order type. Change `order.time_in_force` in `place()`
  (`src/push_orders.py`) and note which one worked.

## The one-time rotation

Switching the traded set from the momentum baskets to `valueDD / weeklyDip / value200`
means every momentum name currently held drops out of all traded baskets at once — about
20 names on the day of the switch. That is a transition cost, not a signal. Decide whether
to close them in one go or let the existing sell-by dates run off, and don't read the
resulting P&L as evidence about either strategy.

## Before going live

- Four weeks of paper with no plumbing surprises.
- `orders_sent.json` shows the dedupe holding across runs and across days.
- **Log the real fills back into `picks_log.json`.** Until executed prices sit next to the
  modelled `entry`, actual slippage against the assumed 10 bps is unmeasured — and that is
  the only way to ever answer "am I late" with real money rather than inference.
- Then set `live_account`, set `allow_live: true`, and start at a reduced `account_size`.
