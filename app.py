"""Public web front end for the word tournament.

SECURITY POSTURE, because this is open to the internet and spends real money.

  the API key       lives only in the server process, read from .env at import.
                    It is never sent to the browser, never logged, and never put
                    into an error message - _post() reports an upstream status
                    code and nothing else, because the upstream body can quote
                    the request back.
  spend             every request to the judgment API is initiated here, never by
                    the client. A visitor cannot choose the model, the vocabulary,
                    the word limit, or the number of rounds. The only thing they
                    control is one short string.
  abuse             a per-IP token bucket, plus a hard global ceiling per hour, so
                    a single visitor cannot drain the account and the whole site
                    cannot either. Both are refusals, not queues.
  input             capped at MAX_CHARS and stripped of control characters. The
                    conversation is rebuilt server-side from the posted turns and
                    truncated, so a client cannot smuggle a 60k-token state.
  headers           a strict CSP with no external origins, nosniff, no framing,
                    and no referrer. The page has no third-party assets at all.
  binding           127.0.0.1 only. Cloudflare's tunnel is the only route in, so
                    the origin is never directly reachable.
"""

import json
import os
import re
import sys
import threading
import time
from collections import defaultdict, deque

from flask import Flask, Response, jsonify, render_template, request

HERE = os.path.dirname(os.path.abspath(__file__))

# Load .env before importing the engine, which reads the key at call time.
for line in open(os.path.join(HERE, ".env")) if os.path.exists(os.path.join(HERE, ".env")) else []:
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

import engine                                                        # noqa: E402

app = Flask(__name__)

MAX_CHARS = 300
MAX_TURNS = 8
PER_IP_PER_MIN = 3
GLOBAL_PER_HOUR = 300

_ip_hits = defaultdict(deque)
_global_hits = deque()
_lock = threading.Lock()
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _client_ip():
    # Cloudflare terminates TLS and sets this. Falls back to the socket peer, which
    # on this box is the tunnel itself - so the fallback throttles everyone together
    # rather than nobody, which is the safe direction to be wrong in.
    return (request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "unknown")


def _rate_limited(ip):
    now = time.time()
    with _lock:
        while _global_hits and now - _global_hits[0] > 3600:
            _global_hits.popleft()
        if len(_global_hits) >= GLOBAL_PER_HOUR:
            return "the demo has hit its hourly ceiling; try again shortly"
        hits = _ip_hits[ip]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= PER_IP_PER_MIN:
            return "one at a time please - %d replies a minute per visitor" % PER_IP_PER_MIN
        hits.append(now)
        _global_hits.append(now)
    return None


def _clean_turns(raw):
    """Rebuild the conversation server-side. Nothing from the client is trusted."""
    turns = []
    if isinstance(raw, list):
        for item in raw[-MAX_TURNS:]:
            if not isinstance(item, dict):
                continue
            speaker = "user" if item.get("speaker") == "user" else "assistant"
            text = CONTROL.sub("", str(item.get("text", "")))[:MAX_CHARS].strip()
            if text:
                turns.append({"speaker": speaker, "text": text})
    return turns


@app.after_request
def _headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self' "
        "'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return resp


@app.route("/")
def index():
    return render_template("index.html", vocab=len(engine.VOCAB),
                           groups=len(engine.GROUPS), group_size=engine.GROUP_SIZE)


@app.route("/healthz")
def healthz():
    # Deliberately says nothing about credentials or upstream state.
    return jsonify({"ok": True, "vocabulary": len(engine.VOCAB)})


@app.route("/api/reply", methods=["POST"])
def reply():
    ip = _client_ip()
    limited = _rate_limited(ip)
    if limited:
        return jsonify({"error": limited}), 429

    payload = request.get_json(silent=True) or {}
    turns = _clean_turns(payload.get("turns"))
    if not turns or turns[-1]["speaker"] != "user":
        return jsonify({"error": "say something first"}), 400

    def stream():
        yield "retry: 10000\n\n"
        try:
            for step in engine.compose(turns):
                yield "data: %s\n\n" % json.dumps(step)
        except engine.EngineError as exc:
            yield "data: %s\n\n" % json.dumps({"error": str(exc), "stop": True})
        except Exception:                                            # noqa: BLE001
            # Never leak a traceback to a public page.
            print("unexpected engine failure", file=sys.stderr, flush=True)
            yield "data: %s\n\n" % json.dumps(
                {"error": "something went wrong on the server", "stop": True})
        yield "data: %s\n\n" % json.dumps({"done": True})

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8975"))
    # 127.0.0.1 ONLY. The tunnel is the single way in.
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
