#!/usr/bin/env python3
"""What jev.pl-labs.net has been doing, and what it cost.

    ./usage_report.py            every day so far
    ./usage_report.py --days 7   the last seven

Reads usage.jsonl, which holds no IP addresses, no cookies, no user agents and
nothing anyone typed - see analytics.py for why. Visitor counts are per-day
pseudonyms, so "visitors" means distinct people THAT DAY and the same person on two
days counts twice. That is the honest reading of the number and the report says so
rather than letting anyone infer a stickiness it cannot measure.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analytics                                                     # noqa: E402


def _money(usd):
    """Never print $0.00 for money that was actually spent.

    The first run showed $0.00 against 105,666 tokens, because %.2f rounds four tenths
    of a cent to nothing. A cost column that reads zero on every quiet day is a column
    everyone learns to skip, and then nobody notices the day it is not zero.
    """
    if usd == 0:
        return "$0"
    if usd < 0.01:
        return "<1c"
    if usd < 1:
        return "%.0fc" % (usd * 100)
    return "$%.2f" % usd


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=None)
    args = ap.parse_args()

    rows = analytics.summary(args.days)
    if not rows:
        # An empty log and a missing log are different things, and saying "no usage"
        # for a path that was never wired would be the write-only-check failure again.
        where = analytics.LOG
        if not os.path.exists(where):
            print("no usage log at %s - nothing has been recorded yet, which is "
                  "different from nobody having visited." % where)
        else:
            print("usage log exists at %s but holds no events yet." % where)
        return 0

    print("%-12s %8s %7s %8s %7s %9s %11s %9s %7s" % (
        "day", "visitors", "views", "replies", "words", "requests", "tokens",
        "cost", "refused"))
    tv = tr = tw = tq = tt = trf = 0
    tc = 0.0
    for d in rows:
        print("%-12s %8d %7d %8d %7d %9d %11s %9s %7d" % (
            d["day"], d["visitors"], d["views"], d["replies"], d["words"],
            d["requests"], "{:,}".format(d["tokens"]), _money(d["usd"]),
            d["refused"]))
        tv += d["visitors"]; tr += d["views"]; tw += d["replies"]
        tq += d["words"]; tt += d["requests"]; trf += d["refused"]
        tc += d["usd"]
    print("-" * 92)
    print("%-12s %8d %7d %8d %7d %9d %11s %9s %7d" % (
        "total", tv, tr, tw, tq, tt, "{:,}".format(sum(d["tokens"] for d in rows)),
        _money(tc), trf))
    errs = sum(d["errors"] for d in rows)
    if errs:
        print("\n%d request(s) ended in an error." % errs)
    if trf:
        print("%d request(s) were REFUSED by the rate limit - if that number is large, "
              "real visitors are being turned away and the cap wants raising." % trf)
    print("\nvisitors is distinct people PER DAY; the same person on two days counts "
          "twice.\nThe log deliberately cannot tell otherwise - see analytics.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
