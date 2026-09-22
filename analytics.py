"""Who is using jev.pl-labs.net, and what it costs - without collecting anyone.

WHY THIS IS SERVER-SIDE AND NOT A TAG. The page's security posture says, in as many
words, that it loads no third-party assets and its CSP names no external origins. A
Google or Plausible tag would break both and would be the only reason a visitor's
browser talks to anyone but us. Counting from inside the server costs nothing, cannot
be blocked, and keeps that claim true.

WHAT IS DELIBERATELY NOT STORED. No raw IP address, ever. A visitor is counted as a
SHA-256 of (ip + a salt that changes daily), truncated. That is enough to answer "how
many different people today" and structurally unable to answer "was this person here
yesterday", because tomorrow the same visitor hashes to something else and the old salt
is not kept. No cookies, no user agent, no referrer, and never the text anyone typed -
that last one matters because the whole point of the site is that strangers type into
it, and a log of what they said would be a liability with no use.

What IS stored, per event: the day, an event kind, the visitor hash, and the numbers
that cost money.
"""

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.environ.get("JEV_USAGE_LOG", os.path.join(HERE, "usage.jsonl"))

# jev-1.13.0: $42 per billion input tokens, output free (docs/models).
USD_PER_INPUT_TOKEN = 42 / 1e9

_lock = threading.Lock()
_salt_day = None
_salt = None


def _visitor(ip):
    """A per-day pseudonym. Not reversible, and not stable across days on purpose."""
    global _salt_day, _salt
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _salt_day != today:
        # A fresh random salt each day, kept only in memory. Restarting the process
        # mid-day starts a new salt and slightly over-counts uniques; that is the
        # right direction to be wrong in, because the alternative is persisting a
        # salt that makes the hashes linkable across days.
        _salt_day, _salt = today, os.urandom(16)
    return hashlib.sha256(_salt + (ip or "").encode()).hexdigest()[:12]


def record(kind, ip, **fields):
    """Append one event. Never raises - analytics must not be able to break the site."""
    try:
        row = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
               "kind": kind, "v": _visitor(ip)}
        row.update(fields)
        if "input_tokens" in fields:
            row["usd"] = round(fields["input_tokens"] * USD_PER_INPUT_TOKEN, 6)
        line = json.dumps(row, separators=(",", ":"))
        with _lock:
            with open(LOG, "a") as fh:
                fh.write(line + "\n")
    except Exception:                                                # noqa: BLE001
        pass


def summary(days=None):
    """Totals by day, for the report tool. Returns [] if there is nothing yet."""
    rows = []
    try:
        with open(LOG) as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        return []
    if days:
        keep = sorted({r["day"] for r in rows})[-days:]
        rows = [r for r in rows if r["day"] in keep]
    out = {}
    for r in rows:
        d = out.setdefault(r["day"], {"day": r["day"], "visitors": set(), "views": 0,
                                      "replies": 0, "words": 0, "requests": 0,
                                      "tokens": 0, "usd": 0.0, "refused": 0,
                                      "errors": 0})
        d["visitors"].add(r.get("v"))
        k = r.get("kind")
        if k == "view":
            d["views"] += 1
        elif k == "reply":
            d["replies"] += 1
            d["words"] += r.get("words", 0)
            d["requests"] += r.get("requests", 0)
            d["tokens"] += r.get("input_tokens", 0)
            d["usd"] += r.get("usd", 0.0)
        elif k == "refused":
            d["refused"] += 1
        elif k == "error":
            d["errors"] += 1
    for d in out.values():
        d["visitors"] = len(d["visitors"])
        d["usd"] = round(d["usd"], 4)
    return [out[k] for k in sorted(out)]
