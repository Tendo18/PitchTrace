# Generates a fake 90-minute match as a list of Events. The match is played as
# a chain of possessions: someone has the ball and does something with it
# (pass, shot, tackle, foul), then the ball moves on, so each event starts
# where the last one ended. A seed makes the match repeatable.

import random

from app.models import Event

PITCH_LENGTH = 120
PITCH_WIDTH = 80
MATCH_SECONDS = 90 * 60

# Starting eleven for each team: (name, role).
LINEUPS = {
    "Home": [("Pires", "GK"), ("Santos", "DEF"), ("Moreira", "DEF"),
             ("Teixeira", "DEF"), ("Rocha", "DEF"), ("Silva", "MID"),
             ("Costa", "MID"), ("Nunes", "MID"), ("Araujo", "MID"),
             ("Dias", "FWD"), ("Mendes", "FWD")],
    "Away": [("Hughes", "GK"), ("Ward", "DEF"), ("Foster", "DEF"),
             ("Bell", "DEF"), ("Perry", "DEF"), ("Walker", "MID"),
             ("Reid", "MID"), ("Cole", "MID"), ("Lowe", "MID"),
             ("Barnes", "FWD"), ("Nash", "FWD")],
}
BENCH = {"Lopes": "MID"}

# Moments that always happen: (minute, team, player off, player on, reason).
SCRIPTED_SUBS = [(61, "Home", "Nunes", "Lopes", "injury")]

OTHER = {"Home": "Away", "Away": "Home"}

# Made-up club names, so a simulated match can have two named teams.
CLUBS = ["Harbour City", "Redstone Athletic", "Ironbridge Town",
         "Kingsfield Rovers", "Blue Valley", "Eastwood Albion",
         "Northgate United", "Stonebridge City", "Lakeside Wanderers",
         "Highfield Rangers", "Oakmont FC", "Westbrook Town"]


def club_names(seed: int, home: str | None = None, away: str | None = None):
    # Any name you give is used. The missing ones are picked from CLUBS using
    # the seed, so the same seed always gives the same teams.
    pool = [c for c in CLUBS if c not in (home, away)]
    picks = random.Random(f"clubs-{seed}").sample(pool, 2)
    return home or picks[0], away or picks[1]

# How likely each role is to be picked for a job. Weights, not percentages.
RECEIVER = {"GK": 0.3, "DEF": 2, "MID": 4, "FWD": 3}   # who a pass is aimed at
WINS_BALL = {"GK": 0.2, "DEF": 3, "MID": 3, "FWD": 1}  # who picks up a loose ball
DEFENDS = {"GK": 0, "DEF": 3, "MID": 4, "FWD": 1}      # who tackles or fouls
STARTS = {"GK": 0, "DEF": 1, "MID": 4, "FWD": 2}       # who takes the kick-off


def simulate_match(seed: int = 42, home: str = "Home", away: str = "Away") -> list[Event]:
    rng = random.Random(seed)
    role = {name: r for team in LINEUPS.values() for name, r in team} | BENCH
    on_pitch = {team: [n for n, _ in players] for team, players in LINEUPS.items()}
    events: list[Event] = []
    t = 0.0   # match time in seconds

    # Coordinates are worked out from the point of view of the team with the
    # ball, who always attacks toward x = 120. frame says whose point of view
    # the numbers use, and emit() mirrors them for Away so the teams attack
    # opposite goals on the real pitch.
    def emit(kind, team, frame, x=None, y=None, end_x=None, end_y=None, **fields):
        if frame == "Away":
            x = None if x is None else PITCH_LENGTH - x
            end_x = None if end_x is None else PITCH_LENGTH - end_x
        r = lambda v: None if v is None else round(v, 1)
        events.append(Event(
            id=len(events) + 1, minute=int(t // 60), second=int(t % 60),
            type=kind, team=team, x=r(x), y=r(y), end_x=r(end_x), end_y=r(end_y),
            **fields))

    def pick(team, weights, exclude=None):
        names = [n for n in on_pitch[team] if n != exclude]
        return rng.choices(names, weights=[weights[role[n]] for n in names])[0]

    def keeper(team):
        return next(n for n in on_pitch[team] if role[n] == "GK")

    emit("period_start", "Home", "Home")
    poss, holder, bx, by = "Home", pick("Home", STARTS), 60.0, 40.0
    subs = sorted(SCRIPTED_SUBS)

    while True:
        t += rng.uniform(2, 7)   # seconds until the next thing happens
        if t >= MATCH_SECONDS:
            break

        while subs and t >= subs[0][0] * 60:
            _, team, off, on, reason = subs.pop(0)
            emit("substitution", team, team, player=off, player_on=on, detail=reason)
            on_pitch[team][on_pitch[team].index(off)] = on
            if holder == off:
                holder = on

        # Shots only happen near the opponent's goal.
        weights = {"pass": 80, "shot": 5.5 if bx >= 78 else 0,
                   "tackle": 4.5, "foul": 1.8}
        action = rng.choices(list(weights), weights=list(weights.values()))[0]
        defending = OTHER[poss]

        if action == "pass":
            target = pick(poss, RECEIVER, exclude=holder)
            dx = rng.gauss(6, 11)
            ex = min(max(bx + dx, 3), 117)
            ey = min(max(by + rng.uniform(-16, 16), 3), 77)
            ok = rng.random() < (0.9 if dx < 15 else 0.68)   # long balls fail more
            emit("pass", poss, poss, bx, by, ex, ey, player=holder,
                 target=target, outcome="complete" if ok else "incomplete")
            if ok:
                holder, bx, by = target, ex, ey
            else:   # the other team picks it up, so flip to their point of view
                poss, holder = defending, pick(defending, WINS_BALL)
                bx, by = PITCH_LENGTH - ex, ey

        elif action == "shot":
            outcome = rng.choices(["goal", "saved", "off_target", "blocked"],
                                  weights=[11, 30, 35, 24])[0]
            if outcome == "blocked":
                ex, ey = min(bx + 4, 118), by + rng.uniform(-3, 3)
            elif outcome == "off_target":
                ex, ey = 120, rng.choice([rng.uniform(24, 33), rng.uniform(47, 56)])
            else:
                ex, ey = 120, rng.uniform(35, 45)
            emit("goal" if outcome == "goal" else "shot", poss, poss, bx, by,
                 ex, ey, player=holder, outcome=outcome)
            if outcome == "goal":   # the other team kicks off from the centre
                poss, holder, bx, by = defending, pick(defending, STARTS), 60.0, 40.0
            elif outcome == "blocked":
                poss, holder = defending, pick(defending, WINS_BALL)
                bx, by = PITCH_LENGTH - ex, ey
            else:   # saved or off target: the goalkeeper restarts play
                poss, holder, bx, by = defending, keeper(defending), 8.0, 40.0

        elif action == "tackle":
            tackler = pick(defending, DEFENDS)
            won = rng.random() < 0.55
            emit("tackle", defending, poss, bx, by, player=tackler,
                 outcome="won" if won else "lost")
            if won:
                poss, holder, bx = defending, tackler, PITCH_LENGTH - bx

        else:   # foul by the defending team, the other side keeps the ball
            fouler = pick(defending, DEFENDS)
            emit("foul", defending, poss, bx, by, player=fouler)
            if rng.random() < 0.1:
                emit("card", defending, poss, bx, by, player=fouler,
                     detail="yellow card")

    t = MATCH_SECONDS
    emit("period_end", "Home", "Home")

    # Everything above used "Home" and "Away" as internal labels. Now that the
    # match is finished, swap in the real team names.
    names = {"Home": home, "Away": away}
    for e in events:
        e.team = names[e.team]
    return events