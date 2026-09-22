# jev-binary-chat

**Live: https://jev.pl-labs.net**

A chat demo built on [TypeSafe](https://docs.typesafe.ai)'s System One model, **Jev**.

Jev does not generate text. It returns typed judgments only: a choice from a fixed
set, a yes/no probability, a score. This page makes it hold a conversation anyway.

Every word of every reply is **chosen, never generated**, by a tournament over a
5,000-word dictionary:

| round | what happens | requests |
|---|---|---|
| 1 | the dictionary is cut into 20 groups of 250. Each group is its own `Choice` question, and all 20 ride in **one** request, because System One evaluates questions in parallel and extra questions are nearly free. | 1 |
| 2 | the 20 group winners compete in a single `Choice`. | 1 |

Two requests and about 1.2 seconds per word. Every word in the dictionary is
genuinely considered for every position. A `<end of reply>` option is offered from
the third word onward, so the model decides when it has finished.

## Why a tournament and not a binary search

This started as "binary search the dictionary". That does not work, and the reason
is worth keeping.

**Binary search over an alphabetical dictionary carries no signal.** Measured, not
assumed: across a full 13-step search Jev's answers sat between 0.39 and 0.48 at
every single step. Thirteen coin flips in a row, and the output was `accepting`.
The alphabet is semantically meaningless, so "is the next word before or after
`melon`" is unanswerable: both halves contain every kind of word. Sorting by word
frequency instead roughly doubles the signal (mean |p-0.5| goes from 0.094 to
0.213) and still returns nonsense, because rank 4000 and rank 4001 are unrelated.

**Character by character fails harder.** Asked for the next letter of
`The capital of France is Par`, Jev answers `a`. For `Two plus two equals fou`,
`e`. For `Good morning, how are yo`, `e`. Three for three wrong on completions with
one obvious answer. That is not a prompting problem; continuing a character string
is exactly the generative task a judgment model does not do.

**Choosing between candidate words on meaning works extremely well.** Asked which
word best begins a reply to *thanks for your help*, Jev picks `welcome` at 0.94 and
puts `banana` at 0.00. So every question here is a semantic one, and the dictionary
is searched by elimination rather than bisection.

**The ceiling is tokens, not questions.** Measured against the live API: 20 groups
of 250 (5,000 options) costs about 34k input tokens and returns in under a second;
40 groups of 250 returns `{"detail":{"error_type":"max_tokens_exceeded"}}`. That is
why the vocabulary is the 5,000 most common English words rather than the full list.

## Security

Public page, real credential, real spend, so:

- **The API key never leaves the server.** Read from `.env` (mode 600, gitignored)
  at import. Never sent to the browser, never logged. Upstream failures are
  reported as a bare status code, because the upstream body can quote the request
  back to us.
- **The client cannot spend.** Every API call is initiated server-side. A visitor
  cannot choose the model, the vocabulary, the word cap, or the round count. The
  only thing they control is one string of at most 300 characters.
- **Rate limited twice.** A per-IP token bucket (3 replies/minute) and a global
  ceiling (300/hour). Both refuse rather than queue, so neither a single visitor
  nor the whole internet can drain the account.
- **Input is rebuilt server-side.** Posted turns are re-typed, control characters
  stripped, capped at 8 turns of 300 characters, so a client cannot smuggle a
  60k-token `state`.
- **Strict CSP** with no external origins, plus nosniff, `frame-ancestors 'none'`
  and `no-referrer`. The page loads no third-party assets at all.
- **Binds to 127.0.0.1 only.** A Cloudflare named tunnel is the single route in, so
  the origin is never directly reachable.

## Running it

```
cp .env.example .env          # then put a real TYPESAFE_API_KEY in it
chmod 600 .env
./run.sh start                # http://127.0.0.1:8975
./run.sh status
./run.sh stop
```

`run.sh` finds its process **by the port it holds**, never by a command-line
pattern: `pkill -f "python3 app.py"` also matches the shell running the script and
kills that instead.

Requires Python 3.10+ and Flask. `data/vocab.json` holds the word list and its
frequency ranks, built from the google-10000-english list.

## Layout

```
app.py                 Flask front end: streaming, rate limiting, headers
engine.py              the tournament, and why it is shaped this way
data/vocab.json        5,000-word vocabulary with frequency ranks
templates/index.html   the page
run.sh                 start/stop/status by port
```

## Known rough edges

- **Grammar wobbles.** "glad to helping anytime". Each word is chosen against the
  reply so far, but nothing enforces agreement across the whole sentence. A second
  Noul per candidate ("does this word fit grammatically here?") would help and
  costs one extra question per word.
- **It is slow by design.** Roughly 1.2s per word, so a ten-word reply takes twelve
  seconds. The words stream as they are decided, and watching the search is most of
  the point.
- **Vocabulary is 5,000 words**, so it cannot say anything needing a rarer one.
  Beyond that the token ceiling forces a third round.
