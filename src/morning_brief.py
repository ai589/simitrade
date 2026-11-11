# The 08:05 SGT brief: what to do today, in one screen.
#
# Why this exists: the US session runs 21:30-04:00 SGT, so by the time you are awake the
# picks have been sitting on the dashboard for hours - but only if you remember to open
# it. Until now a signal existed only if you went looking for it. This turns the
# dashboard into something that arrives.
#
# It is deliberately a READ-ONLY view of exactly what push_orders.py would send: it calls
# the same build_orders() / levels() / shares(), so if the brief and the dry run ever
# disagree, that is a bug worth chasing, not a rounding difference.
#
#   python src/morning_brief.py                  print the brief
#   python src/morning_brief.py --out FILE       also write it to FILE
#   python src/morning_brief.py --save           write to output/brief-YYYY-MM-DD.txt
#   python src/morning_brief.py --quiet-if-fresh exit 0 silently when nothing to do
#   python src/morning_brief.py --whatsapp       render the phone version, send nothing
#   python src/morning_brief.py --save --send    write the file AND push to WhatsApp
#
# Delivery goes to the WhatsApp self-chat through src/whatsapp_send.py, which needs its
# Chrome profile logged in once:  python src/whatsapp_send.py --login

import argparse
import datetime
import os
import sys
import textwrap

from push_orders import load_payload, load_config, build_orders, DATA_JS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "output")

STALE_HOURS = 20        # past this, the overnight refresh did not land


def data_age_hours(generated):
    try:
        gen = datetime.datetime.strptime(generated, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None
    return (datetime.datetime.now() - gen).total_seconds() / 3600


def fmt_age(hrs):
    if hrs is None:
        return "unknown age"
    if hrs < 1:
        return "%d min old" % round(hrs * 60)
    if hrs < 48:
        return "%.1f h old" % hrs
    return "%d days old" % round(hrs / 24)


def _wrap(text, indent="  ", width=92):
    return textwrap.wrap(text or "", width=width, initial_indent=indent,
                         subsequent_indent=indent) or []


def news_source_line(D):
    """One line saying who read the news - never let a keyword read pass as Claude's."""
    nm = D.get("newsMeta") or {}
    if nm.get("error"):
        return "News/macro lists FAILED this run: %s" % nm["error"]
    if nm.get("source") == "claude":
        return "News read by Claude (%s), %s of %s shortlisted names had headlines." % (
            nm.get("model"), nm.get("withNews"), nm.get("shortlisted"))
    if nm.get("source") == "keyword":
        return ("News: KEYWORD FALLBACK, not Claude (%s). Treat the news scores as rough."
                % (nm.get("note") or "no reason given"))
    return None


def macro_lines(D):
    """The market backdrop block: rule-based score, the key levels, Claude's read."""
    m = D.get("macro") or {}
    if m.get("riskScore") is None:
        return []
    tiles = {t["sym"]: t for t in m.get("tiles") or []}

    def lv(sym, fmt="%.2f"):
        t = tiles.get(sym)
        return fmt % t["last"] if t else "n/a"

    L = ["MARKET: %s (%+d on a -100..+100 scale)" % (m.get("riskLabel"), m["riskScore"]),
         "  VIX %s | 10y %s%% | dollar %s | oil %s | breadth %.0f%% above 20d"
         % (lv("^VIX", "%.1f"), lv("^TNX"), lv("DX-Y.NYB", "%.1f"), lv("CL=F", "%.1f"),
            D.get("breadth20") or 0)]
    ai = m.get("ai") or {}
    if ai.get("summary"):
        L += _wrap(ai["summary"])
        if ai.get("risks"):
            L += _wrap("Watch: " + "; ".join(ai["risks"]))
    sec = m.get("sectors") or {}
    ai_sec = ai.get("sectors") or {}

    def tilt(b):
        t = sec.get(b, {}).get("tilt", 0)
        return 0.5 * t + 0.5 * ai_sec[b]["tilt"] / 2 if b in ai_sec else t
    ranked = sorted(sec, key=tilt, reverse=True)
    up = [b for b in ranked if tilt(b) >= 0.25][:3]
    dn = [b for b in reversed(ranked) if tilt(b) <= -0.25][:3]
    if up or dn:
        L.append("  Sector tailwinds: %s | headwinds: %s"
                 % (", ".join(up) or "none", ", ".join(dn) or "none"))
    return L


def pick_lines(p, order=None, held=False):
    """One pick from Top Buys / Top Sells, with its score parts and the reasons."""
    parts = p.get("parts") or {}
    L = ["  %d. %-6s score %3.0f  %s  (%s)"
         % (p.get("rank") or 0, p["sym"], p["score"], p.get("setup") or "",
            p.get("sector") or ""),
         "       setup %.0f | news %+.1f | macro %+.1f   px %.2f  stop %.2f  target %.2f"
         % (parts.get("setup", 0), parts.get("news", 0), parts.get("macro", 0),
            p["px"], p["stop"], p["target"])]
    if p["side"] == "long":
        if order:
            L.append("       order: BUY %d @ %.2f limit, risk $%s"
                     % (order["qty"], order["limit"], format(int(order["risk"]), ",")))
        elif held:
            L.append("       already held from an earlier day - no new order")
        else:
            L.append("       no order (sector cap, sized to zero, or strategy not traded)")
    if p.get("why"):
        L += _wrap("Chart: " + "; ".join(p["why"]), "       ")
    if p.get("newsReason"):
        L += _wrap("News: " + p["newsReason"], "       ")
    return L


def lead_lines(D, orders):
    """Top Buys and Top Sells - the two lists the brief leads with."""
    if "topBuys" not in D and "topSells" not in D:
        return []
    buys, sells = D.get("topBuys") or [], D.get("topSells") or []
    by_order = {o["sym"]: o for o in orders if o["strategy"] == "topBuys"}
    held = next((st.get("held") or {} for st in D.get("strategies") or []
                 if st.get("key") == "topBuys"), {})
    sell_by = (buys or sells or [{}])[0].get("sellBy")
    L = ["TOP BUYS - hold about 5 trading days%s"
         % (", sell by %s" % sell_by if sell_by else "")]
    for p in buys:
        L += pick_lines(p, by_order.get(p["sym"]), p["sym"] in held)
    if not buys:
        L.append("  none - nothing scores high enough today.")
    L.append("")
    L.append("TOP SELLS - avoid, trim or hedge. Not sent as orders (shorting is blocked).")
    for p in sells:
        L += pick_lines(p)
    if not sells:
        L.append("  none - nothing scores high enough today.")
    vetoed = (D.get("newsMeta") or {}).get("vetoed") or []
    if vetoed:
        L.append("")
        L.append("Dropped on news:")
        for v in vetoed:
            L += _wrap("%s (%s): %s" % (v["sym"], "buy" if v["side"] == "long" else "sell",
                                        v.get("reason") or ""))
    return L


def build_brief(D, cfg):
    orders, info = build_orders(D, cfg)
    hrs = data_age_hours(D.get("generated"))
    now = datetime.datetime.now()
    L = []
    add = L.append

    add("ECL morning brief - %s SGT" % now.strftime("%a %d %b %Y, %H:%M"))

    # --- data freshness: the first thing to check, because a dead scheduler is silent ---
    if hrs is None or hrs > STALE_HOURS:
        add("")
        add("*** STALE DATA - DO NOT TRADE OFF THIS BRIEF ***")
        add("Screen is from %s (%s). The overnight refresh did not run."
            % (D.get("generated"), fmt_age(hrs)))
        add("Fix: run tools\\install_tasks.ps1, then \"1. refresh.bat\". "
            "Check logs\\refresh_log.txt.")
    else:
        add("Data: %s (%s) - ok" % (D.get("generated"), fmt_age(hrs)))

    # --- market context -----------------------------------------------------------
    regime = "ON" if D.get("regimeOn") is not False else "OFF"
    add("Regime: %s (SPY %.2f vs 50d %.2f) | breadth %.1f%% above 20d | size x%.2f"
        % (regime, D.get("spyPx") or 0, D.get("spySma50") or 0,
           D.get("breadth20") or 0, info["regimeMult"]))

    # --- the lead: macro backdrop, then the two lists ---------------------------------
    block = macro_lines(D)
    if block:
        add("")
        L.extend(block)
    src = news_source_line(D)
    if src:
        add("  " + src)
    block = lead_lines(D, orders)
    if block:
        add("")
        L.extend(block)

    # --- what to do ---------------------------------------------------------------
    add("")
    if orders:
        add("ORDERS - limit orders, they rest until the 21:30 SGT open")
        for o in orders:
            add("  %-10s %-6s %-4s %5d @ %8.2f   stop %8.2f  target %8.2f   risk $%s"
                % (o["strategy"], o["sym"], o["side"], o["qty"], o["limit"],
                   o["stop"], o["target"], format(int(o["risk"]), ",")))
        risk = sum(o["risk"] for o in orders)
        add("  %d orders | risk $%s (%.1f%% of $%s) | notional $%s"
            % (len(orders), format(int(risk), ","),
               risk / cfg["account_size"] * 100,
               format(int(cfg["account_size"]), ","),
               format(int(sum(o["notional"] for o in orders)), ",")))
    else:
        add("BUY: nothing new today (all picks already held, sized to zero, "
            "or their strategy is skipped).")

    if info["rotateOut"]:
        add("")
        add("CLOSE if held (dropped out of every traded basket):")
        add("  " + ", ".join(info["rotateOut"]))

    # --- things that quietly changed the sizing -----------------------------------
    notes = []
    if info["skippedHealth"]:
        notes.append("kill switch skipped: %s (live results below backtest)"
                     % ", ".join(info["skippedHealth"]))
    if info["heatScale"] < 1:
        notes.append("all sizes scaled x%.2f to stay under the %.0f%% portfolio heat cap"
                     % (info["heatScale"], cfg["max_heat_pct"]))
    if info["regimeMult"] < 1:
        notes.append("regime multiplier x%.2f (SPY below its 50d MA and/or breadth < 50%%)"
                     % info["regimeMult"])
    if notes:
        add("")
        add("Notes")
        for n in notes:
            add("  - " + n)

    add("")
    add("Trading: %s" % ", ".join(cfg["strategies"]))
    add("Next:  python src/push_orders.py           (dry run - check sizes)")
    add("       python src/push_orders.py --paper   (send to the Tiger paper account)")

    stale = hrs is None or hrs > STALE_HOURS
    return "\n".join(L), orders, info, stale


def whatsapp_text(D, cfg, orders, info, stale):
    """The same brief, formatted for a phone.

    The console version uses fixed-width columns that wrap into noise on WhatsApp, so the
    table becomes one block per name. WhatsApp markup is *single asterisks* for bold, not
    Markdown."""
    hrs = data_age_hours(D.get("generated"))
    now = datetime.datetime.now()
    L = ["*ECL morning brief*", now.strftime("%a %d %b %Y, %H:%M SGT")]

    if stale:
        L += ["", "*STALE DATA - DO NOT TRADE OFF THIS*",
              "Screen is from %s (%s). The overnight refresh did not run."
              % (D.get("generated"), fmt_age(hrs)),
              "Fix: tools\\install_tasks.ps1, then \"1. refresh.bat\"."]
        return "\n".join(L)

    L.append("Data %s (%s)" % (D.get("generated"), fmt_age(hrs)))
    m = D.get("macro") or {}
    if m.get("riskScore") is not None:
        L.append("*Market: %s* (%+d) - size x%.2f"
                 % (m.get("riskLabel"), m["riskScore"], info["regimeMult"]))
        if (m.get("ai") or {}).get("summary"):
            L.append(m["ai"]["summary"])
    else:
        L.append("Regime %s - breadth %.1f%% - size x%.2f"
                 % ("ON" if D.get("regimeOn") is not False else "OFF",
                    D.get("breadth20") or 0, info["regimeMult"]))
    src = news_source_line(D)
    if src and (D.get("newsMeta") or {}).get("source") != "claude":
        L.append("_%s_" % src)

    for title, key in (("TOP BUYS", "topBuys"), ("TOP SELLS (avoid / trim)", "topSells")):
        if key not in D:
            continue
        L += ["", "*%s*" % title]
        for p in D.get(key) or []:
            L.append("%d. *%s* %.0f - %s" % (p.get("rank") or 0, p["sym"], p["score"],
                                             p.get("setup") or ""))
            if p.get("newsReason") and p.get("materiality"):
                L.append("   %s" % p["newsReason"])
        if not D.get(key):
            L.append("none today")

    if orders:
        L += ["", "*ORDERS* - limits rest until the 21:30 open"]
        for o in orders:
            L.append("- *%s* %s %d @ %.2f" % (o["sym"], o["side"], o["qty"], o["limit"]))
            L.append("   stop %.2f - target %.2f - risk $%s"
                     % (o["stop"], o["target"], format(int(o["risk"]), ",")))
        risk = sum(o["risk"] for o in orders)
        L.append("%d orders - risk $%s (%.1f%%) - notional $%s"
                 % (len(orders), format(int(risk), ","),
                    risk / cfg["account_size"] * 100,
                    format(int(sum(o["notional"] for o in orders)), ",")))
    else:
        L += ["", "*ORDERS* nothing new today."]

    if info["rotateOut"]:
        L += ["", "*CLOSE if held* (%d)" % len(info["rotateOut"]),
              ", ".join(info["rotateOut"])]

    if info["skippedHealth"]:
        L += ["", "Kill switch skipped: " + ", ".join(info["skippedHealth"])]
    if info["heatScale"] < 1:
        L.append("Sizes scaled x%.2f for the %.0f%% heat cap"
                 % (info["heatScale"], cfg["max_heat_pct"]))

    L += ["", "Trading: " + ", ".join(cfg["strategies"]),
          "Then: push_orders.py (dry run) -> --paper"]
    return "\n".join(L)


def notify(text, marker=None, send=False):
    """Push the brief to the WhatsApp self-chat via src/whatsapp_send.py.

    Sending is off unless send=True. `marker` makes a re-run on the same day a no-op
    rather than a duplicate message."""
    try:
        import whatsapp_send
    except ImportError as e:
        print("WhatsApp delivery unavailable: %s" % e, file=sys.stderr)
        return False
    try:
        whatsapp_send.send(text, dry_run=not send, marker=marker)
        # send() returns False for a deliberate duplicate skip as well as for a dry run;
        # neither is a delivery failure, so only an exception counts as one.
        return True
    except whatsapp_send.WhatsAppError as e:
        print("WhatsApp delivery failed: %s" % e, file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser(description="Print today's trading brief.")
    ap.add_argument("--out", metavar="FILE", help="also write the brief to FILE")
    ap.add_argument("--save", action="store_true",
                    help="also write to output/brief-YYYY-MM-DD.txt")
    ap.add_argument("--quiet-if-fresh", action="store_true",
                    help="print nothing and exit 0 when there is nothing to act on")
    ap.add_argument("--whatsapp", action="store_true",
                    help="render the phone-formatted version instead of the console one")
    ap.add_argument("--send", action="store_true",
                    help="push the brief to the WhatsApp self-chat (implies --whatsapp)")
    args = ap.parse_args()

    if not os.path.exists(DATA_JS):
        sys.exit('No data.js at %s - run "1. refresh.bat" first.' % DATA_JS)

    D = load_payload()
    cfg = load_config()
    text, orders, info, stale = build_brief(D, cfg)

    if args.quiet_if_fresh and not stale and not orders and not info["rotateOut"]:
        return 0

    if args.whatsapp or args.send:
        print(whatsapp_text(D, cfg, orders, info, stale))
    else:
        print(text)

    delivery_failed = False
    if args.send:
        # One brief per day: the marker is the date line the message starts with, so a
        # second run finds it in the recent messages and skips.
        marker = datetime.datetime.now().strftime("%a %d %b %Y")
        delivery_failed = not notify(whatsapp_text(D, cfg, orders, info, stale),
                                     marker=marker, send=True)

    paths = []
    if args.out:
        paths.append(args.out)
    if args.save:
        os.makedirs(OUTPUT, exist_ok=True)
        paths.append(os.path.join(
            OUTPUT, "brief-%s.txt" % datetime.date.today().isoformat()))
    for p in paths:
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
        print("\nWrote %s" % p)

    # Non-zero on stale data, or when a requested delivery did not go out, so a scheduled
    # run shows up as failed rather than passing quietly with a warning nobody reads.
    if delivery_failed:
        print("\nBrief was NOT delivered to WhatsApp - see the error above.",
              file=sys.stderr)
    return 1 if (stale or delivery_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
