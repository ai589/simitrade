# Push this week's strategy picks to Tiger Brokers as bracket orders (limit entry with
# attached take-profit + stop-loss legs), sized exactly as the dashboard sizes them.
#
# Reads data.js (the last screener run) + state/tiger_config.json. Nothing here talks to
# Tiger unless you pass --paper or --live; the default is a dry run that prints the order
# table so you can sanity-check sizes and levels first.
#
#   python src/push_orders.py               dry run (no SDK/credentials needed)
#   python src/push_orders.py --paper       place on the paper account in tiger_config.json
#   python src/push_orders.py --live        place on the live account (needs "allow_live": true)
#   python src/push_orders.py --paper --close-rotate-out   also market-sell "rotate out" names
#
# Setup (one-off): pip install tigeropen ; register at https://quant.itigerup.com (Tiger Open
# API), generate the RSA key pair, note your tiger_id + paper/live account numbers, then copy
# state/tiger_config.example.json -> state/tiger_config.json and fill it in. The config and
# the order log are gitignored and excluded from the public mirror.
#
# Safety rails: never resends the same (date, strategy, symbol) (state/orders_sent.json);
# skips names that already have an open order or position at the broker; refuses --live
# unless the config says allow_live; a hard cap on orders per run.

import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
DATA_JS = os.path.join(ROOT, "data.js")
CONFIG = os.path.join(STATE, "tiger_config.json")
SENT = os.path.join(STATE, "orders_sent.json")

DEFAULTS = {
    "mode_note": "dry | paper | live is chosen on the command line, never here",
    "tiger_id": "", "private_key_path": "", "paper_account": "", "live_account": "",
    "allow_live": False,
    "strategies": ["mom14", "sector14", "valueDD"],   # which cards to trade (dashboard keys)
    "account_size": 100000, "risk_pct": 1.0, "max_heat_pct": 6.0, "max_per_sector": 2,
    "max_pos_pct": 20, "regime_scale": True, "budget": 0,
    "entry_limit_slip_pct": 0.5,   # limit = entry x (1 + slip) for longs: marketable at the open
    "max_orders_per_run": 20,
    "skip_health": ["demoted", "research"],   # don't send strategies the kill switch flagged
}


def load_payload():
    with open(DATA_JS, encoding="utf-8") as f:
        s = f.read()
    return json.loads(s[s.index("{"): s.rindex("}") + 1])


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def levels(r, st):
    """Same as dashboard levels(): per-strategy ATR multiples, inverted for shorts."""
    sm, tm = st.get("stopMult") or 1.5, st.get("targetMult") or 2.0
    atr = r.get("atr")
    if not atr:
        return r["stop"], r["target"]
    if st.get("side") == "short":
        return round(r["entry"] + sm * atr, 2), round(r["entry"] - tm * atr, 2)
    return round(r["entry"] - sm * atr, 2), round(r["entry"] + tm * atr, 2)


def regime_mult(D, cfg):
    if not cfg["regime_scale"]:
        return 1.0
    a, b = D.get("regimeOn") is not False, (D.get("breadth20") or 0) > 50
    return 1.0 if (a and b) else 0.5 if (a or b) else 0.25


def shares(r, stop, basket, cfg, rm, scale=1.0):
    acct = cfg["account_size"]
    per_share = abs(r["entry"] - stop)
    if per_share <= 0:
        return 0
    n = int(acct * cfg["risk_pct"] / 100 * rm * scale / per_share)
    n = min(n, int(acct * cfg["max_pos_pct"] / 100 / r["entry"]))
    if cfg.get("budget"):
        n = min(n, int(cfg["budget"] / max(basket, 1) / r["entry"]))
    return max(n, 0)


def build_orders(D, cfg):
    """Mirror of the dashboard's strategy-card sizing: sector cap per basket, portfolio
    heat cap across the selected strategies (deduped by symbol), regime multiplier."""
    by_sym = {r["sym"]: r for r in D["rows"]}
    rm = regime_mult(D, cfg)
    chosen = [st for st in D["strategies"] if st["key"] in cfg["strategies"]]
    skipped_health = [st["key"] for st in chosen
                      if (st.get("health") or {}).get("status") in cfg["skip_health"]]
    chosen = [st for st in chosen if st["key"] not in skipped_health]

    def keep_set(st):
        cap, seen, keep = cfg["max_per_sector"], {}, set()
        for sym in st["picks"]:
            r = by_sym.get(sym)
            if not r:
                continue
            seen[r["sector"]] = seen.get(r["sector"], 0) + 1
            if not cap or seen[r["sector"]] <= cap:
                keep.add(sym)
        return keep

    risk_by_sym = {}
    for st in chosen:
        keep = keep_set(st)
        for sym in st["picks"]:
            r = by_sym.get(sym)
            if not r or sym not in keep:
                continue
            stop, _ = levels(r, st)
            risk_by_sym[sym] = max(risk_by_sym.get(sym, 0),
                                   shares(r, stop, len(st["picks"]), cfg, rm) * abs(r["entry"] - stop))
    heat0 = sum(risk_by_sym.values())
    cap_d = cfg["account_size"] * cfg["max_heat_pct"] / 100
    heat_scale = cap_d / heat0 if (cfg["max_heat_pct"] and heat0 > cap_d) else 1.0

    orders, seen_syms = [], set()
    for st in chosen:
        keep = keep_set(st)
        held = st.get("held") or {}
        for sym in st["picks"]:
            r = by_sym.get(sym)
            if not r or sym not in keep or sym in held or sym in seen_syms:
                continue
            stop, target = levels(r, st)
            n = shares(r, stop, len(st["picks"]), cfg, rm, heat_scale)
            if n <= 0:
                continue
            side = "SELL" if st.get("side") == "short" else "BUY"
            slip = cfg["entry_limit_slip_pct"] / 100
            limit = round(r["entry"] * (1 - slip if side == "SELL" else 1 + slip), 2)
            orders.append({"strategy": st["key"], "sym": sym, "side": side, "qty": n,
                           "limit": limit, "stop": stop, "target": target,
                           "risk": round(n * abs(r["entry"] - stop), 0),
                           "notional": round(n * r["entry"], 0), "sector": r["sector"]})
            seen_syms.add(sym)
    # rotate-out = held names no longer picked by ANY chosen strategy (a name dropped by
    # one basket but picked by another stays)
    still_picked = {sym for st in chosen for sym in st["picks"]}
    rotate_out = sorted({o["sym"] for st in chosen for o in (st.get("rotateOut") or [])}
                        - still_picked)
    info = {"regimeMult": rm, "heat0": round(heat0), "heatScale": round(heat_scale, 3),
            "skippedHealth": skipped_health, "rotateOut": rotate_out,
            "generated": D.get("generated")}
    return orders, info


def print_table(orders, info, cfg):
    print(f"data.js generated {info['generated']} | regime x{info['regimeMult']} | "
          f"heat before cap ${info['heat0']:,} -> scale x{info['heatScale']}"
          + (f" | skipped by kill switch: {', '.join(info['skippedHealth'])}" if info['skippedHealth'] else ""))
    if not orders:
        print("No new orders (all picks held, sized to zero, or strategies skipped).")
    else:
        print(f"{'strategy':12s} {'sym':6s} {'side':4s} {'qty':>5s} {'limit':>9s} {'stop':>9s} "
              f"{'target':>9s} {'risk$':>7s} {'notional$':>10s}  sector")
        for o in orders:
            print(f"{o['strategy']:12s} {o['sym']:6s} {o['side']:4s} {o['qty']:5d} {o['limit']:9.2f} "
                  f"{o['stop']:9.2f} {o['target']:9.2f} {o['risk']:7.0f} {o['notional']:10.0f}  {o['sector']}")
        print(f"{len(orders)} orders | total risk ${sum(o['risk'] for o in orders):,.0f} "
              f"({sum(o['risk'] for o in orders) / cfg['account_size'] * 100:.1f}% of ${cfg['account_size']:,}) "
              f"| notional ${sum(o['notional'] for o in orders):,.0f}")
    if info["rotateOut"]:
        print("Rotate-out (close if held): " + ", ".join(info["rotateOut"]))


def load_sent():
    try:
        with open(SENT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"sent": []}


def place(orders, info, cfg, mode, close_rotate_out):
    try:
        from tigeropen.tiger_open_config import get_client_config
        from tigeropen.trade.trade_client import TradeClient
        from tigeropen.common.util.contract_utils import stock_contract
        from tigeropen.common.util.order_utils import limit_order_with_legs, order_leg, market_order
    except ImportError:
        raise SystemExit("tigeropen not installed: pip install tigeropen")
    account = cfg["live_account"] if mode == "live" else cfg["paper_account"]
    if not (cfg["tiger_id"] and cfg["private_key_path"] and account):
        raise SystemExit(f"tiger_id / private_key_path / {mode}_account missing in {CONFIG}")
    if mode == "live" and not cfg.get("allow_live"):
        raise SystemExit('Refusing --live: set "allow_live": true in tiger_config.json first.')
    if len(orders) > cfg["max_orders_per_run"]:
        raise SystemExit(f"{len(orders)} orders exceeds max_orders_per_run={cfg['max_orders_per_run']}; aborting.")

    client = TradeClient(get_client_config(private_key_path=cfg["private_key_path"],
                                           tiger_id=cfg["tiger_id"], account=account))
    open_syms = {o.contract.symbol for o in (client.get_open_orders(account=account) or [])}
    positions = {p.contract.symbol: p for p in (client.get_positions(account=account) or [])}
    sent = load_sent()
    today = datetime.date.today().isoformat()
    already = {(e["date"], e["strategy"], e["sym"]) for e in sent["sent"]}
    placed = 0
    for o in orders:
        key = (info["generated"][:10], o["strategy"], o["sym"])
        if key in already:
            print(f"skip {o['sym']}: already sent for {key[0]}")
            continue
        if o["sym"] in open_syms or o["sym"] in positions:
            print(f"skip {o['sym']}: open order/position exists at broker")
            continue
        contract = stock_contract(symbol=o["sym"].replace(".", " "), currency="USD")
        legs = [order_leg("PROFIT", o["target"], time_in_force="GTC", outside_rth=False),
                order_leg("LOSS", o["stop"], time_in_force="GTC", outside_rth=False)]
        order = limit_order_with_legs(account=account, contract=contract, action=o["side"],
                                      quantity=o["qty"], limit_price=o["limit"], order_legs=legs)
        order.time_in_force = "DAY"
        oid = client.place_order(order)
        print(f"placed {o['side']} {o['qty']} {o['sym']} @ {o['limit']} tp {o['target']} sl {o['stop']} -> order {oid}")
        sent["sent"].append({"date": key[0], "strategy": o["strategy"], "sym": o["sym"],
                             "qty": o["qty"], "limit": o["limit"], "stop": o["stop"],
                             "target": o["target"], "mode": mode, "orderId": str(oid),
                             "sentAt": datetime.datetime.now().isoformat(timespec="seconds")})
        placed += 1
    if close_rotate_out:
        for sym in info["rotateOut"]:
            p = positions.get(sym)
            if not p or not p.quantity:
                continue
            action = "SELL" if p.quantity > 0 else "BUY"
            oid = client.place_order(market_order(account=account, contract=p.contract,
                                                  action=action, quantity=abs(int(p.quantity))))
            print(f"rotate-out {action} {abs(int(p.quantity))} {sym} at market -> order {oid}")
            sent["sent"].append({"date": today, "strategy": "rotateOut", "sym": sym,
                                 "qty": abs(int(p.quantity)), "mode": mode, "orderId": str(oid),
                                 "sentAt": datetime.datetime.now().isoformat(timespec="seconds")})
    with open(SENT, "w", encoding="utf-8") as f:
        json.dump(sent, f, indent=1)
    print(f"{placed} bracket orders placed on the {mode} account; log: {SENT}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--paper", action="store_true", help="place on the paper account")
    g.add_argument("--live", action="store_true", help="place on the live account (needs allow_live)")
    ap.add_argument("--close-rotate-out", action="store_true",
                    help="also market-close broker positions in the rotate-out list")
    a = ap.parse_args()
    cfg = load_config()
    D = load_payload()
    orders, info = build_orders(D, cfg)
    print_table(orders, info, cfg)
    mode = "live" if a.live else "paper" if a.paper else "dry"
    if mode == "dry":
        print("\nDry run only. Add --paper (or --live) to send; see header for setup.")
        return
    place(orders, info, cfg, mode, a.close_rotate_out)


if __name__ == "__main__":
    main()
