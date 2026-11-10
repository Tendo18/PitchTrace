# Automated checks for the ticker. For many matches and both languages, every
# score, name, minute and shot count in each line is compared with the events
# it came from. Minutes shown to viewers are the elapsed minute plus one.
# Run from the project folder: py test_ticker.py

import re
from pathlib import Path

from app.moments import find_moments
from app.real_data import load_real_match
from app.simulator import simulate_match
from app.ticker import ticker_line

SCORE = re.compile(r"\b(\d+)-(\d+)\b")
problems: list[str] = []
checked = 0


def check(label: str, events) -> None:
    global checked
    teams = list(dict.fromkeys(e.team for e in events))
    by_id = {e.id: e for e in events}
    for m in find_moments(events):
        group = [by_id[i] for i in m.event_ids]
        last_id = max(m.event_ids)
        # The true score once this moment has happened, counted here
        # separately from the ticker's own code.
        goals = [e for e in events if e.type == "goal" and e.id <= last_id]
        truth = (sum(e.team == teams[0] for e in goals),
                 sum(e.team == teams[1] for e in goals))
        first = group[0]
        # The minute a viewer should see: elapsed minute plus one.
        shown = first.minute + 1

        for language in ("english", "pidgin"):
            line = ticker_line(m, events, language)
            checked += 1
            where = f"{label}, {m.kind}, minute {m.minute}, {language}: {line}"

            for h, a in SCORE.findall(line):
                if (int(h), int(a)) != truth:
                    problems.append(f"wrong score {h}-{a}, true {truth[0]}-{truth[1]} | {where}")
            if ticker_line(m, events, language) != line:
                problems.append(f"not repeatable | {where}")

            if m.kind in ("goal", "injury_sub"):
                if first.player not in line:
                    problems.append(f"player missing | {where}")
                if str(shown) not in line:
                    problems.append(f"minute {shown} missing | {where}")
            # One of the Pidgin injury lines (yours) doesn't name the
            # replacement, so only English must.
            if m.kind == "injury_sub" and language == "english" and first.player_on not in line:
                problems.append(f"replacement missing | {where}")
            # Goals in English always read "in minute N", so the plus-one
            # can be checked exactly there.
            if m.kind == "goal" and language == "english" and f"minute {shown}." not in line:
                problems.append(f"goal minute should be {shown} | {where}")

            if m.kind == "attacking_spell":
                needle = f"{len(group)} shots" if language == "english" else f"shoot {len(group)} times"
                if needle not in line:
                    problems.append(f"shot count missing | {where}")
                for minute in (group[0].minute + 1, group[-1].minute + 1):
                    if str(minute) not in line:
                        problems.append(f"minute {minute} missing | {where}")
                scorers = [e for e in group if e.type == "goal"]
                if language == "pidgin" and scorers and scorers[0].player not in line:
                    problems.append(f"scorer missing | {where}")


for seed in range(100):
    check(f"seed {seed}", simulate_match(seed))

real = Path("data/events/3869685.json")
if real.exists():
    check("real match 3869685", load_real_match(3869685))

print(f"{checked} lines checked, {len(problems)} problems")
for p in problems[:20]:
    print(" -", p)
raise SystemExit(1 if problems else 0)