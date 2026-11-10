# Instant commentary built by code from templates, with no AI call.
# The Pidgin templates come from lines written by a native speaker. Code fills
# in the names, minutes, counts and scores straight from the events, so the
# facts are always right and it takes milliseconds, not seconds. Templates
# marked GUESS are not from his lines yet, so replace them in your own words.

import random
from typing import Literal

from app.models import Event
from app.moments import Moment

Language = Literal["english", "pidgin"]
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def shown_minute(e: Event) -> int:
    # Event minutes count elapsed minutes from 0, so an event at 22:41 is
    # stamped minute 22, but commentators call it the 23rd minute. Every line
    # a viewer reads (and every fact given to the AI) uses this instead of
    # e.minute. The replay clock keeps using the raw time.
    return e.minute + 1


def _word(n: int) -> str:
    return NUMBER_WORDS.get(n, str(n))


def _join(parts: list[str], joiner: str = " and ") -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + joiner + parts[-1]


def _teams(events: list[Event]) -> list[str]:
    # The first team to appear in the events is home, the second is away.
    return list(dict.fromkeys(e.team for e in events))


def _score(events: list[Event], upto_id: int) -> tuple[int, int]:
    # (home goals, away goals) counting every goal up to this event id.
    home, away = _teams(events)[:2]
    goals = [e for e in events if e.type == "goal" and e.id <= upto_id]
    return (sum(e.team == home for e in goals),
            sum(e.team == away for e in goals))


def _mine_theirs(events: list[Event], team: str, score: tuple[int, int]):
    # Reorders a (home, away) score so the given team's goals come first.
    return score if _teams(events)[0] == team else (score[1], score[0])


def _name(team: str, language: str) -> str:
    # Pidgin: "Home" becomes "home". English and real team names stay as is.
    return team.lower() if language == "pidgin" and team in ("Home", "Away") else team


def _the(team: str) -> str:
    return f"the {team.lower()} team" if team in ("Home", "Away") else team


def _first_event(m: Moment, events: list[Event]) -> Event:
    return next(e for e in events if e.id == m.event_ids[0])


# ---------- goals ----------

GOAL = {
    "pidgin": {
        "opener": "{t} don score first: {p} score for {t} for minute {m}, scores na {s}",
        "equaliser": "goallllll, {t} don level am oo, {p} score for minute {m}, scores na {s} now",  # GUESS
        "extend": "{t} don increase the gap: {p} don score for {t} for minute {m}, scores na {s} now",
        "plain": "goallllll, {p} don score for {t} for minute {m}, score na {s}",
    },
    "english": {
        "opener": "{p} puts {t} ahead in minute {m}. It's {s}.",
        "equaliser": "{p} equalises for {t} in minute {m}. It's {s}.",
        "extend": "{p} extends {t}'s lead in minute {m}. It's {s}.",
        "plain": "GOAL! {p} scores for {t} in minute {m}. It's {s}.",
    },
}


def _goal(m: Moment, events: list[Event], language: str) -> str:
    e = _first_event(m, events)
    before, after = _score(events, e.id - 1), _score(events, e.id)
    mine_before, theirs_before = _mine_theirs(events, e.team, before)
    if sum(before) == 0:
        kind = "opener"
    elif after[0] == after[1]:
        kind = "equaliser"
    elif mine_before > theirs_before:
        kind = "extend"
    else:
        kind = "plain"
    return GOAL[language][kind].format(
        t=_name(e.team, language), p=e.player, m=shown_minute(e),
        s=f"{after[0]}-{after[1]}")


# ---------- injury substitutions ----------

def _injury(m: Moment, events: list[Event], language: str, rng: random.Random) -> str:
    e = _first_event(m, events)
    mins = shown_minute(e)
    score = _score(events, e.id)
    mine, theirs = _mine_theirs(events, e.team, score)
    s = f"{score[0]}-{score[1]}"
    t = _name(e.team, language)
    if language == "english":
        return (f"{e.player} goes off injured for {t} in minute {mins} "
                f"and {e.player_on} comes on. It's {s}.")

    still = "still " if score[0] == score[1] else ""
    options = [
        f"{e.player} don injure for {mins} minute, {e.player_on} don come on for am for {_the(e.team)}",
        f"{e.player}{'' if e.team in ('Home', 'Away') else ' for'} {t} don carry injury, they don comot am for the {mins} minute, scores na {still}{s}",
    ]
    if mine < theirs:   # only when the team is losing
        options.append(f"{e.player} don injure for {t} for minute {mins}, "
                       f"{e.player_on} don come on for am and {t} dey lose {s}")
    return rng.choice(options)


# ---------- attacking spells ----------

def _spell(m: Moment, events: list[Event], language: str) -> str:
    shots = [e for e in events if e.id in m.event_ids]
    n, first, last = len(shots), shown_minute(shots[0]), shown_minute(shots[-1])
    saved = sum(s.outcome == "saved" for s in shots)
    blocked = sum(s.outcome == "blocked" for s in shots)
    off = sum(s.outcome == "off_target" for s in shots)
    goals = [s for s in shots if s.type == "goal"]
    other = n - saved - blocked - off - len(goals)

    score = _score(events, shots[-1].id)
    mine, theirs = _mine_theirs(events, m.team, score)
    s = f"{score[0]}-{score[1]}"
    t = _name(m.team, language)

    if language == "english":
        parts = []
        if saved: parts.append(f"{saved} saved")
        if blocked: parts.append(f"{blocked} blocked")
        if off: parts.append(f"{off} off target")
        if goals: parts.append(f"{len(goals)} goal{'s' if len(goals) > 1 else ''}")
        if other: parts.append(f"{other} other")
        return (f"{t} had {n} shots between minute {first} and minute {last}: "
                f"{_join(parts)}. It's {s}.")

    # The "everything" phrases are from your lines (the saved one is a GUESS).
    if n == saved:
        clause = "keeper been save everything"
    elif n == blocked:
        clause = "they been block everything"
    elif n == off:
        clause = "everything been go off target"
    else:
        parts = []
        if saved: parts.append(f"keeper been save {_word(saved)}")
        if blocked: parts.append(f"they been block {_word(blocked)}")
        if off: parts.append(f"{_word(off)} been go off target")
        if goals: parts.append(f"{goals[0].player} been score {_word(len(goals))}")
        if other: parts.append(f"{_word(other)} no enter")   # GUESS
        clause = _join(parts)

    if goals:
        tail = f"scores na {s} now"
    elif mine < theirs:
        tail = f"{t} still dey lose {s}"
    elif mine > theirs:
        tail = f"{t} dey lead {s} now"
    else:
        tail = f"scores na still {s}"
    opening = "" if goals else "pressure deyy, "
    return f"{opening}{t} don shoot {n} times between {first} and {last} minute, {clause}, {tail}"


# ---------- entry point ----------

def ticker_line(moment: Moment, events: list[Event],
                language: Language = "pidgin") -> str:
    # Seeding the choice with the first event id means the same match always
    # gives the same lines, which helps when you replay a demo.
    rng = random.Random(moment.event_ids[0])
    if moment.kind == "goal":
        return _goal(moment, events, language)
    if moment.kind == "injury_sub":
        return _injury(moment, events, language, rng)
    if moment.kind == "attacking_spell":
        return _spell(moment, events, language)
    return moment.summary