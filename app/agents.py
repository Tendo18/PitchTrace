# The AI agents. A moment goes through these steps:
#   Analyst   writes a short English insight using only the facts we give it
#   Verifier  checks it against the same facts: PASS or FAIL
#   Narrator  rewrites the verified insight in English or Nigerian Pidgin,
#             copying the voice of the lines in pidgin_examples.json
#   Checker   checks the narrated line too. For Pidgin, a translator turns the
#             line into plain English and the Verifier reads that as well,
#             because extra invented details are easier to spot in English.
# A failed step gets one retry with the reason. If the Analyst fails twice we
# use the plain summary written by code. If the narrated line fails twice we
# show the verified English insight instead.
# Minutes in the facts are the same ones the ticker shows (elapsed minute
# plus one), so the AI and the ticker can never disagree about when it was.

import json
import os
import random
import re
from pathlib import Path

from typing import Literal

from pydantic import BaseModel

from app.llm import ask
from app.models import Event
from app.moments import Moment
from app import cache
from app.ticker import shown_minute, ticker_line


Language = Literal["english", "pidgin"]

# Set to False to skip the translate-and-check step (saves model calls).
CHECK_VIA_ENGLISH = True

# Optional per-agent models from .env. Unset means None, and ask() then falls
# back to LLM_MODEL. These must stay below the llm import, which loads .env.
ANALYST_MODEL = os.getenv("ANALYST_MODEL")
VERIFIER_MODEL = os.getenv("VERIFIER_MODEL")
NARRATOR_MODEL = os.getenv("NARRATOR_MODEL")
# The translator has to understand Pidgin well, so by default it uses the
# narrator's model.
TRANSLATOR_MODEL = os.getenv("TRANSLATOR_MODEL") or NARRATOR_MODEL


class Attempt(BaseModel):
    claim: str
    verdict: Literal["pass", "fail"]
    reason: str | None = None
    english: str | None = None                 # translation, for Pidgin lines


class Commentary(BaseModel):
    minute: int
    moment: str
    text: str                                  # the final line the viewer sees
    status: Literal["verified", "retried", "fallback", "english_fallback"]
    attempts: list[Attempt]                    # Analyst tries and verdicts
    narration_checks: list[Attempt]            # checks of the narrated line
    evidence: list[int]                        # event ids behind the line


# ---------- facts (plain code, no AI) ----------

def describe(e: Event) -> str:
    parts = [f"minute {shown_minute(e)}", e.team, e.type]
    if e.player:
        parts.append(f"player {e.player}")
    if e.player_on:
        parts.append(f"replaced by {e.player_on}")
    if e.outcome:
        parts.append(f"outcome {e.outcome}")
    if e.detail:
        parts.append(f"reason {e.detail}")
    return ", ".join(parts)


def goals_up_to(events: list[Event], event_id: int) -> str:
    # Goals are counted by code, never by the model. Using event ids keeps the
    # count exact even when a goal and a shot share a minute.
    counts = []
    # dict.fromkeys keeps teams in the order they first appear in the match
    # and drops duplicates. The first team to appear is the home team.
    for team in dict.fromkeys({e.team for e in events}):
        n = sum(1 for e in events
                if e.type == "goal" and e.team == team and e.id <= event_id)
        counts.append(f"{team} {n}")
    return ", ".join(counts)


def facts_for(moment: Moment, events: list[Event]) -> list[str]:
    first_id, last_id = min(moment.event_ids), max(moment.event_ids)
    facts = [describe(e) for e in events if e.id in moment.event_ids]
    facts.append(f"score before these events: {goals_up_to(events, first_id - 1)}")
    facts.append(f"score after these events: {goals_up_to(events, last_id)}")
    return facts


def _bullets(facts: list[str]) -> str:
    return "\n".join(f"- {f}" for f in facts)


ORDER_NOTE = ("The event lines are listed in the order they happened. The "
              "last two lines give the score before and after them.")


# ---------- Analyst ----------

ANALYST_PROMPT = f"""You are a football analyst. You get FACTS about one moment
in a match. Write one or two plain English sentences saying what happened and
why it matters.

{ORDER_NOTE}

Rules:
- Use ONLY the facts given. Every name, number, minute and result you write
  must appear in the facts.
- Only say the score stayed the same if the score before and after are equal.
- Do not mention tactics, formations, form, tiredness, or any reason or
  person that is not in the facts.
- Output the sentences only."""


def analyse(moment: Moment, facts: list[str], feedback: str | None = None) -> str:
    task = f"Moment: {moment.summary}\nFacts:\n{_bullets(facts)}"
    if feedback:
        task += (f"\n\nYour previous attempt was rejected: {feedback}\n"
                 "Write a corrected version.")
    return ask(ANALYST_PROMPT, task, model=ANALYST_MODEL, max_tokens=250)


# ---------- Verifier ----------

VERIFIER_PROMPT = f"""You are a strict fact-checker for football commentary.
You get FACTS and a CLAIM. Check every statement in the claim against the
facts only. Do not use outside football knowledge. If any part of the claim
is not supported by the facts, even if it sounds plausible, it fails. That
includes extra people, roles, reasons or events that the facts never mention.

The CLAIM may be written in English or in Nigerian Pidgin. Ignore style, slang
and exclamation marks, and check only the facts it states.

{ORDER_NOTE}

Reply with PASS on the first line, or FAIL on the first line followed by one
short line naming the unsupported part."""


def verify(claim: str, facts: list[str]) -> Attempt:
    task = f"FACTS:\n{_bullets(facts)}\n\nCLAIM: {claim}"
    reply = ask(VERIFIER_PROMPT, task, temperature=0,
                model=VERIFIER_MODEL, max_tokens=120)
    # partition splits at the first newline: the verdict line and the rest.
    first, _, rest = reply.strip().partition("\n")
    ok = first.strip().upper().startswith("PASS")
    # Models sometimes put the reason on the FAIL line itself, so strip the
    # leading word FAIL (and any punctuation after it) from the reason.
    reason = re.sub(r"^\s*fail\W*", "", rest.strip() or first.strip(),
                    flags=re.IGNORECASE)
    return Attempt(claim=claim, verdict="pass" if ok else "fail",
                   reason=None if ok else reason)


# ---------- Translator and line checker ----------

TRANSLATOR_PROMPT = """Translate this Nigerian Pidgin football commentary line
into plain English, as literally as you can. Do not add, remove or fix
anything, even if it looks wrong or invented. Output the translation only."""


def translate(line: str) -> str:
    return ask(TRANSLATOR_PROMPT, line, temperature=0,
               model=TRANSLATOR_MODEL, max_tokens=150)


def check_line(line: str, facts: list[str], language: Language) -> Attempt:
    # First the Verifier reads the line directly.
    result = verify(line, facts)
    if result.verdict == "fail" or language != "pidgin" or not CHECK_VIA_ENGLISH:
        return result

    # It passed, so now check the English translation too. Extra details such
    # as an invented doctor are much easier to spot in plain English.
    english = translate(line)
    second = verify(english, facts)
    result.english = english
    if second.verdict == "fail":
        result.verdict = "fail"
        result.reason = f"the English meaning ({english}) was rejected: {second.reason}"
    return result


# ---------- Narrator ----------

RULES = """Rules:
- Use ONLY the insight and facts you are given. Never invent names, numbers,
  scores, people, or reasons.
- One or two short sentences. Lively and natural, like a fan talking.
- Output the commentary line only, with no explanation.
- Write scores home team first, then away team, in the same order the facts
  list them. So 1-2 means the first team has 1 and the second has 2."""

NARRATOR_PROMPTS = {
    "english": ("You are a football commentator. Rewrite the INSIGHT as short "
                "live commentary in clear, natural English.\n\n" + RULES),
    "pidgin": ("You are a football commentator. Rewrite the INSIGHT as short "
               "live commentary in Nigerian Pidgin, the way a Nigerian fan "
               "would say it, not a word-for-word translation.\n\n" + RULES),
}

EXAMPLES_FILE = Path(__file__).parent / "pidgin_examples.json"
STYLE_FILE = Path(__file__).parent / "pidgin_style.txt"


def pidgin_examples(kind: str, limit: int = 4) -> str:
    # Picks a few of your own lines for this kind of moment. A random pick
    # each time keeps the commentary from sounding repetitive.
    try:
        data = json.loads(EXAMPLES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    items = [x for x in data.get(kind, []) if "YOUR LINE" not in x["line"]]
    chosen = random.sample(items, min(limit, len(items)))
    return "\n".join(f'Facts: {x["facts"]}\nLine: {x["line"]}' for x in chosen)


def pidgin_style() -> str:
    # Reads your style rules, skipping comments and unfilled placeholders.
    # The file is optional.
    try:
        lines = STYLE_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ""
    return "\n".join(l for l in lines
                     if l.strip() and not l.startswith("#")
                     and not l.rstrip().endswith("..."))


def narrator_prompt(language: Language, kind: str) -> str:
    prompt = NARRATOR_PROMPTS[language]
    if language == "pidgin":
        style = pidgin_style()
        if style:
            prompt += "\n\nStyle notes from a native speaker:\n" + style
        examples = pidgin_examples(kind)
        if examples:
            prompt += ("\n\nHere are lines written by a native speaker. Copy "
                       "their voice and word choice, NOT their facts:\n"
                       + examples)
    return prompt


def narrate(insight: str, facts: list[str], language: Language,
            feedback: str | None = None, kind: str = "goal") -> str:
    task = f"INSIGHT: {insight}\nFacts:\n{_bullets(facts)}"
    if feedback:
        task += (f"\n\nYour previous line was rejected: {feedback}\n"
                 "Write a corrected line using only the insight and facts.")
    return ask(narrator_prompt(language, kind), task,
               model=NARRATOR_MODEL, max_tokens=200)


# ---------- the whole pipeline for one moment ----------

def build_commentary(moment: Moment, events: list[Event],
                     language: Language = "pidgin") -> Commentary:
    facts = facts_for(moment, events)

    # Stage 1: Analyst writes, Verifier checks, one retry.
    attempts: list[Attempt] = []
    insight = None
    feedback = None
    for _ in range(2):
        claim = analyse(moment, facts, feedback)
        result = verify(claim, facts)
        attempts.append(result)
        if result.verdict == "pass":
            insight = claim
            break
        feedback = result.reason

    if insight is None:
        insight, status = moment.summary, "fallback"   # plain code summary
    elif len(attempts) == 1:
        status = "verified"
    else:
        status = "retried"

    # Stage 2: Narrator writes, the line is checked, one retry.
    checks: list[Attempt] = []
    text = None
    feedback = None
    for _ in range(2):
        line = narrate(insight, facts, language, feedback, moment.kind)
        result = check_line(line, facts, language)
        checks.append(result)
        if result.verdict == "pass":
            text = line
            break
        feedback = result.reason

    if text is None:
        text, status = insight, "english_fallback"     # the verified English
    elif len(checks) > 1 and status == "verified":
        status = "retried"

    return Commentary(
        minute=moment.minute + 1, moment=moment.summary, text=text,
        status=status, attempts=attempts, narration_checks=checks,
        evidence=moment.event_ids,
    )


# ---------- cache and fallback ----------

# Bump this whenever you change a prompt (or the facts the prompts are given),
# so old cached results are ignored.
PROMPT_VERSION = "2"


def cached_commentary(moment: Moment, events: list[Event],
                      language: Language = "pidgin") -> Commentary:
    facts = facts_for(moment, events)
    # Everything that could change the answer goes into the key: the facts,
    # the evidence ids (so a cached result can't point at the wrong events),
    # the language, the prompt version and the models.
    key = cache.make_key(
        PROMPT_VERSION, language, moment.kind, "\n".join(facts),
        ",".join(map(str, moment.event_ids)),
        str(os.getenv("LLM_MODEL")), str(ANALYST_MODEL),
        str(VERIFIER_MODEL), str(NARRATOR_MODEL))
    hit = cache.get(key)
    if hit:
        print(f"[cache] hit: {moment.kind} minute {moment.minute}")
        return Commentary.model_validate_json(hit)

    print(f"[cache] miss: {moment.kind} minute {moment.minute}")
    result = build_commentary(moment, events, language)
    # Don't save give-ups, so they get another chance next time.
    if result.status in ("verified", "retried"):
        cache.put(key, result.model_dump_json())
    return result


def ticker_fallback(moment: Moment, events: list[Event],
                    language: Language = "pidgin") -> Commentary:
    # Used when the AI layer errors out: the exact line built by code.
    return Commentary(
        minute=moment.minute + 1, moment=moment.summary,
        text=ticker_line(moment, events, language), status="fallback",
        attempts=[], narration_checks=[], evidence=moment.event_ids)