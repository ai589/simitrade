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
        add("Fix: run tools\\install_tasks.ps1, then refresh.bat. "
            "Check logs\\refresh_log.txt.")
    else:
        add("Data: %s (%s) - ok" % (D.get("generated"), fmt_age(hrs)))

    # --- market context -----------------------------------------------------------
    regime = "ON" if D.get("regimeOn") is not False else "OFF"
    add("Regime: %s (SPY %.2f vs 50d %.2f) | breadth %.1f%% above 20d | size x%.2f"
        % (regime, D.get("spyPx") or 0, D.get("spySma50") or 0,
           D.get("breadth20") or 0, info["regimeMult"]))

    # --- what to do ---------------------------------------------------------------
    add("")
    if orders:
        add("BUY / SELL SHORT - limit orders, they rest until the 21:30 SGT open")
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
              "Fix: tools\\install_tasks.ps1, then refresh.bat."]
        return "\n".join(L)

    L.append("Data %s (%s)" % (D.get("generated"), fmt_age(hrs)))
    L.append("Regime %s - breadth %.1f%% - size x%.2f"
             % ("ON" if D.get("regimeOn") is not False else "OFF",
                D.get("breadth20") or 0, info["regimeMult"]))

    if orders:
        L += ["", "*BUY* - limits rest until the 21:30 open"]
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
        L += ["", "*BUY* nothing new today."]

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
        sys.exit("No data.js at %s - run refresh.bat first." % DATA_JS)

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
