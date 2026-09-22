"""Make a model that cannot write text, write text.

Jev returns typed judgments only: a choice from a fixed set, a yes/no probability,
a score. It does not generate. So a reply is assembled one word at a time, and every
word is chosen by a tournament over the whole dictionary.

WHY A TOURNAMENT AND NOT A BINARY SEARCH, which is what was asked for first.

Binary search over an alphabetically sorted dictionary cannot work, and this was
measured rather than assumed. Over a full 13-step search the model's answers sat
between 0.39 and 0.48 at every step - thirteen coin flips - and the output was
"accepting". The reason is structural: the alphabet carries no meaning, so "is the
next word before or after `melon`" is a question with no signal in it. Both halves
contain every kind of word. Sorting by frequency instead roughly doubles the signal
and still returns nonsense, because rank 4000 and rank 4001 have nothing to do with
each other either.

Character-by-character generation was tried next and fails harder. Asked for the
next letter of "The capital of France is Par", Jev answers 'a'. For "Two plus two
equals fou", 'e'. That is not a prompting problem: Jev is a judgment model, and
continuing a string of characters is precisely the generative task it does not do.

What it IS extremely good at is choosing between candidate words on meaning. Asked
which word best starts a reply to "thanks for your help", it picks "welcome" at 0.94
and puts "banana" at 0.00. So the search has to be over words, and every question
has to be a semantic one.

THE SHAPE THAT WORKS, in two requests per word:

  round 1   the dictionary is cut into groups of 250. Every group becomes its own
            Choice question, and all of them go in ONE request, because System One
            evaluates questions in parallel and extra questions are nearly free.
            5,000 words, 20 questions, about 34k tokens, under a second.
  round 2   the winners of each group compete in a single Choice. One request.

So each word costs 2 requests and roughly 1.2 seconds, and every word in the
dictionary was genuinely considered. 10,000 words does not fit - the API returns
max_tokens_exceeded above about 5,000 options in one request, measured - which is
why the vocabulary is the 5,000 most common English words.
"""

import json
import os
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai") + "/v1/systemone"
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")

STOP = "<end of reply>"
GROUP_SIZE = 250
VOCAB_SIZE = 5000
MAX_WORDS = 24

INSTRUCTIONS = (
    "Which word is the most natural NEXT word of the assistant's reply, continuing "
    "exactly from `reply_so_far`? The reply is being written one word at a time and "
    "must read as a fluent, friendly English sentence, so grammar matters as much as "
    "meaning: the word has to fit the point the sentence has reached. Pick the single "
    "best candidate from this group even if none is ideal."
)


class EngineError(RuntimeError):
    """A failure worth showing a user, with nothing sensitive in it."""


# THE WORD LIST IS A WEB FREQUENCY LIST, SO IT IS FULL OF THINGS THAT ARE NOT WORDS.
#
# google-10000-english is scraped, and its top 5,000 contains 194 one- and two-letter
# tokens: bare initials (c, e, s, n, t, d, m, r), units and abbreviations (pm, uk, cd,
# tv, pc), and fragments (nd, nt) left behind when "and" and "n't" were split. They are
# frequent precisely because they are debris, so they win groups and land in replies -
# "no im nt human", "helpful nd friendly". Filtering them is worth more to output
# quality than any amount of prompt wording.
#
# Two- and one-letter words that ARE words are kept explicitly rather than by a rule,
# because no rule separates "am" from "nd". Apostrophe-less contractions stay: the
# vocabulary has no punctuation, so "im" and "ive" are the only way to say I'm and I've.
KEEP_SHORT = {
    "a", "i",
    "am", "an", "as", "at", "be", "by", "do", "go", "he", "hi", "id", "if", "im", "in",
    "is", "it", "me", "my", "no", "of", "oh", "ok", "on", "or", "so", "to", "up", "us",
    "we", "ah", "ye",
}


# WORDS THIS DEMO WILL NOT SAY (2026-09-22).
#
# Max is posting this on LinkedIn under his own name. google-10000-english is a scrape
# of web text, and its top 5,000 carries 32 words I would not want appearing in a reply
# on a post a colleague is sharing professionally - including fuck, rape, porn and a
# cluster of SEO spam (viagra, casino, xxx).
#
# The risk is not hypothetical for THIS design specifically. Every word is chosen as
# the most natural continuation of the visitor's own message, so a crude prompt steers
# the search directly at the crude end of the dictionary. A generative model would at
# least have been trained to decline; this thing only knows which word fits.
#
# WHAT IS DELIBERATELY NOT HERE, because over-filtering is its own failure: gay and
# lesbian are ordinary words and removing them as though they were profanity would be
# worse than the problem. So are death, die, kill, murder, hate, drug and stupid - a
# chat demo that cannot say "die" is broken, not safe. The line drawn is sexual and
# scatological content plus scrape spam, not unpleasant subject matter.
DENY = {
    "anal", "ass", "asses", "asshole", "bastard", "bitch", "bitches", "boobs", "cock",
    "cunt", "dick", "erotic", "escort", "fag", "faggot", "fuck", "fucked", "fucking",
    "horny", "naked", "nigger", "nude", "orgasm", "piss", "porn", "porno", "rape",
    "retard", "retarded", "sex", "sexy", "shit", "shitty", "slut", "tits", "whore",
    "xxx", "casino", "gambling", "viagra",
}


def _load_vocab():
    with open(os.path.join(HERE, "data", "vocab.json")) as fh:
        data = json.load(fh)
    rank = data["rank"]
    words = sorted(data["words"], key=lambda w: rank[w])
    words = [w for w in words if len(w) > 2 or w in KEEP_SHORT]
    words = [w for w in words if w not in DENY]
    return words[:VOCAB_SIZE]


VOCAB = _load_vocab()
GROUPS = [VOCAB[i:i + GROUP_SIZE] for i in range(0, len(VOCAB), GROUP_SIZE)]


def _api_key():
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        # Never echo the value, and never guess. An empty string is a present name
        # with no credential behind it, which has been mistaken for "configured"
        # here before.
        raise EngineError("the server has no TYPESAFE_API_KEY configured")
    return key


def _post(state, questions, timeout=60):
    body = json.dumps({"state": state, "model": MODEL, "questions": questions}).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Authorization": "Bearer " + _api_key(),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        # The upstream body can quote our request. Report the status only, so a
        # user-visible error can never carry the key or the prompt.
        raise EngineError("the judgment API returned HTTP %d" % exc.code) from None
    except urllib.error.URLError as exc:
        raise EngineError("could not reach the judgment API (%s)"
                          % type(exc.reason).__name__) from None


def _state(conversation, reply_so_far):
    return {
        "conversation": conversation,
        "reply_so_far": reply_so_far or "(the reply has not started yet)",
    }


def next_word(conversation, reply_so_far, word_index):
    """One word. Returns a dict describing the whole tournament, for the UI."""
    state = _state(conversation, reply_so_far)

    questions = {}
    for i, group in enumerate(GROUPS):
        criteria = {w: None for w in group}
        if i == 0 and word_index >= 2:
            # Offered only once the reply could plausibly be finished, so the model
            # is never invited to stop before it has said anything.
            criteria[STOP] = "the reply is already a complete, natural sentence"
        questions["g%d" % i] = {"type": "choice", "instructions": INSTRUCTIONS,
                                "criteria": criteria}

    t0 = time.time()
    first = _post(state, questions)
    t_round1 = time.time() - t0

    finalists = {}
    for i in range(len(GROUPS)):
        answer = first["answers"]["g%d" % i]
        pick = answer["choice"]
        finalists[pick] = max(finalists.get(pick, 0.0), answer.get("confidence") or 0.0)

    ordered = sorted(finalists, key=lambda w: -finalists[w])

    if len(ordered) == 1:
        winner, confidence, probabilities = ordered[0], finalists[ordered[0]], {}
        grammar = {}
        t_round2 = 0.0
    else:
        # ROUND 2 ASKS TWO DIFFERENT QUESTIONS AND CODE COMBINES THEM.
        #
        # The Choice alone picks on meaning and produced "glad to helping anytime" and
        # "im am assistant": the right idea in the wrong grammatical slot. Meaning and
        # grammatical fit are independently useful dimensions, so they are asked
        # separately and multiplied here rather than crammed into one question, which
        # is the composite-scoring shape the API docs recommend - raw judgments stay
        # reusable and the policy lives in code where it can be changed without
        # re-running inference.
        #
        # It is nearly free: the Nouls ride in the SAME request as the Choice, and
        # System One evaluates every question in a request in parallel.
        questions2 = {"f": {"type": "choice", "instructions": INSTRUCTIONS,
                            "criteria": {w: None for w in ordered}}}
        for i, word in enumerate(ordered):
            if word == STOP:
                continue
            questions2["fit%d" % i] = {"type": "noul", "instructions": {
                "question": "Would `candidate` be grammatically correct as the very next "
                            "word, appended directly to `reply_so_far`? Judge only "
                            "grammar and fluency, not whether it is a good answer.",
                "candidate": word,
                "reply_so_far": reply_so_far or "(the reply has not started yet)"}}

        t0 = time.time()
        second = _post(state, questions2)
        t_round2 = time.time() - t0

        final = second["answers"]["f"]
        probabilities = final.get("probabilities") or {}
        grammar = {}
        for i, word in enumerate(ordered):
            key = "fit%d" % i
            grammar[word] = (1.0 if word == STOP
                             else second["answers"][key]["noul"])

        # Meaning times grammatical fit SQUARED. Neither factor alone may decide - a
        # word nobody can parse here is not a candidate however apt, and a grammatical
        # word nobody meant is not one either - but the exponent is not decoration.
        #
        # Measured at the branch that produced "i im am a an assistant": after "i", the
        # grammar judgment is emphatic and correct (am 0.96, im 0.71) while meaning
        # mildly prefers the wrong one (im 0.56, am 0.40). Multiplied once the wrong
        # word wins by 0.398 to 0.384. Squared, the right one wins, and the whole
        # sentence downstream stops being a repair job.
        #
        # This is a weight, so it lives in code. Changing it re-ranks existing judgments
        # without re-running any inference.
        scored = {w: (probabilities.get(w, 0.0) or 0.0) * grammar.get(w, 1.0) ** 2
                  for w in ordered}
        if max(scored.values(), default=0.0) <= 0.0:
            scored = {w: grammar.get(w, 1.0) for w in ordered}
        winner = max(scored, key=lambda w: scored[w])
        confidence = round(scored[winner], 3)

    combined = {w: (probabilities.get(w, 0.0) or 0.0) * grammar.get(w, 1.0) ** 2
                for w in ordered} if len(ordered) > 1 else {}
    top = sorted((combined or probabilities).items(), key=lambda kv: -kv[1])[:8]
    return {
        "word": winner,
        "stop": winner == STOP,
        "confidence": round(confidence, 3),
        "finalists": ordered[:20],
        "runners_up": [{"word": w, "p": round(p, 3),
                        "meaning": round(probabilities.get(w, 0.0) or 0.0, 3),
                        "grammar": round(grammar.get(w, 1.0), 3)} for w, p in top],
        "considered": len(VOCAB),
        "requests": 1 if len(ordered) == 1 else 2,
        "seconds": round(t_round1 + t_round2, 2),
        "input_tokens": first.get("usage", {}).get("input_tokens", 0),
    }


def compose(conversation, max_words=MAX_WORDS):
    """Yield one event per word until the model says the reply is finished."""
    reply = ""
    for index in range(max_words):
        step = next_word(conversation, reply, index)
        if step["stop"]:
            step["reply"] = reply
            yield step
            return
        reply = (reply + " " + step["word"]).strip()
        step["reply"] = reply
        yield step
    yield {"word": None, "stop": True, "reply": reply, "truncated": True,
           "confidence": 0.0, "finalists": [], "runners_up": [],
           "considered": len(VOCAB), "requests": 0, "seconds": 0.0, "input_tokens": 0}
