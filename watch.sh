#!/usr/bin/env bash
# Watch jev.pl-labs.net while it is getting public traffic.
#
# WRITTEN AS A FILE, NOT AN INLINE HEREDOC (2026-09-22). Two attempts at this inline
# failed in ways that were invisible until somebody asked for the numbers:
#
#   attempt 1  `ref=$(grep -c ... || echo 0)` produced "0\n0", because grep -c prints 0
#              AND exits 1 when there are no matches, so `|| echo 0` fired as well. Every
#              comparison then died with "integer expression expected". The site-down
#              check worked; the refusal and error checks never ran ONCE in an hour, while
#              I had told Max a safety net was running.
#   attempt 2  `setsid nohup bash /dev/stdin <<'SH'` exited immediately and silently,
#              because setsid reopened the stdin the heredoc was being read from.
#
# A file can be read, linted and re-run by hand. An inline heredoc cannot.
#
# IT POLLS /healthz, NOT /. The first watcher fetched the page itself every 60 seconds and
# the analytics counted each one as a visit: 64 of the first 64 "page views" were mine.
# /healthz is deliberately not recorded, so monitoring cannot flatter the numbers.
set -uo pipefail

LOG="${JEV_USAGE_LOG:-/home/greg/jev-binary-chat/usage.jsonl}"
URL="${JEV_URL:-https://jev.pl-labs.net}"
TICKS="${TICKS:-120}"
REFUSE_AT="${REFUSE_AT:-20}"
ERROR_AT="${ERROR_AT:-10}"

count() { local n; n=$(grep -c "\"kind\":\"$1\"" "$LOG" 2>/dev/null || true); echo "${n:-0}"; }

for ((i = 1; i <= TICKS; i++)); do
  sleep 60
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 25 "$URL/healthz" 2>/dev/null || echo 000)
  ref=$(count refused); err=$(count error); rep=$(count reply)
  if [ "$code" != "200" ]; then
    echo "SITE DOWN: healthz returned $code at ${i}min (replies=$rep refused=$ref errors=$err)"
    exit 1
  fi
  if [ "$ref" -ge "$REFUSE_AT" ]; then
    echo "REFUSALS: $ref refused against $rep replies at ${i}min - the 300/hour cap is"
    echo "turning real visitors away, not blocking abuse. Ask Max before raising it."
    exit 2
  fi
  if [ "$err" -ge "$ERROR_AT" ]; then
    echo "ERRORS: $err errors against $rep replies at ${i}min"
    exit 3
  fi
done
echo "$TICKS minutes clean: site up, refusals under $REFUSE_AT, errors under $ERROR_AT"
