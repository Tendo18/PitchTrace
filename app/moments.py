# Scans a match's events and picks out the moments worth commenting on.
# This is plain code. Later, the AI agents will explain these moments.

from pydantic import BaseModel

from app.models import Event

# An attacking spell is a run of shots by one team: at least SPELL_SHOTS
# shots, each no more than SPELL_GAP minutes after the one before it.
# Tune these to get more or fewer moments.
SPELL_SHOTS = 3
SPELL_GAP = 3


class Moment(BaseModel):
    kind: str             # "goal", "injury_sub" or "attacking_spell"
    minute: int
    team: str
    event_ids: list[int]  # the events behind this moment: our evidence
    summary: str          # a plain label, NOT the final commentary


def find_moments(events: list[Event]) -> list[Moment]:
    moments = []

    for e in events:
        if e.type == "goal":
            moments.append(Moment(
                kind="goal", minute=e.minute, team=e.team, event_ids=[e.id],
                summary=f"{e.player} scored for {e.team}"))
        elif e.type == "substitution" and e.detail == "injury":
            moments.append(Moment(
                kind="injury_sub", minute=e.minute, team=e.team,
                event_ids=[e.id],
                summary=f"{e.player} went off injured, {e.player_on} came on"))

    shots = [e for e in events if e.type in ("shot", "goal")]
    for team in sorted({e.team for e in events}):
        team_shots = [s for s in shots if s.team == team]
        spell = []

        # The None at the end means "no more shots", so the last spell
        # still gets checked and saved.
        for s in team_shots + [None]:
            # This shot is too late to join the current spell (or the shots
            # have run out), so close the spell and keep it if it's big enough.
            if spell and (s is None or s.minute - spell[-1].minute > SPELL_GAP):
                if len(spell) >= SPELL_SHOTS:
                    moments.append(Moment(
                        kind="attacking_spell", minute=spell[0].minute,
                        team=team, event_ids=[x.id for x in spell],
                        summary=f"{team} had {len(spell)} shots between "
                                f"minute {spell[0].minute} and "
                                f"minute {spell[-1].minute}"))
                spell = []
            if s is not None:
                spell.append(s)

    return sorted(moments, key=lambda m: m.minute)