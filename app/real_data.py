# Converts a real StatsBomb match into our own Event format.
# After this, the rest of the app can't tell real matches from simulated ones.
# A match is looked up in three places, fastest first: memory (the last few),
# then a saved file in cache/matches/ on your own disk, then a download from
# GitHub. Every match you download is saved, so watching it again later is
# instant, even after restarting the server.

import json
import threading
from collections import OrderedDict
from pathlib import Path

from app.catalogue import fetch_json, home_team
from app.models import Event

MAX_CACHED = 6   # how many converted matches to keep in memory
SHOT_OUTCOMES = {"Off T": "off_target", "Wayward": "off_target"}

# Bump this whenever the conversion below changes, so old saved files are
# ignored instead of loading matches converted the old way.
CONVERTER_VERSION = 1
SAVE_DIR = Path(__file__).resolve().parent.parent / "cache" / "matches"


def _short_names(match_id: int) -> dict:
    # Events carry full legal names ("Lionel Andrés Messi Cuccittini"). The
    # lineup file has short names for many players ("Lionel Messi"). If it
    # can't be fetched we simply keep the full names.
    try:
        teams = fetch_json(f"lineups/{match_id}.json")
    except (FileNotFoundError, RuntimeError):
        return {}
    return {p["player_id"]: p.get("player_nickname") or p["player_name"]
            for team in teams for p in team["lineup"]}


def _outcome(block: dict):
    # StatsBomb nests outcomes like {"outcome": {"name": "Saved"}}.
    return block.get("outcome", {}).get("name")


def _convert(r: dict, short) -> list[dict]:
    # Turns one StatsBomb event into zero or more of our events, returned as
    # dicts of fields. StatsBomb has dozens of event types, and we keep only
    # the ones we use. A foul with a card becomes two events.
    kind = r["type"]["name"]
    loc = r.get("location") or [None, None]
    base = {"x": loc[0], "y": loc[1]}

    if kind == "Pass":
        p = r["pass"]
        end = p.get("end_location") or [None, None]
        # Completed passes have no outcome at all, only failed ones do.
        outcome = "incomplete" if _outcome(p) else "complete"
        return [{**base, "type": "pass", "end_x": end[0], "end_y": end[1],
                 "target": short(p.get("recipient", {})), "outcome": outcome}]

    if kind == "Shot":
        s = r["shot"]
        end = s.get("end_location") or [None, None]
        raw = _outcome(s) or ""
        outcome = SHOT_OUTCOMES.get(raw, raw.lower().replace(" ", "_"))
        return [{**base, "type": "goal" if raw == "Goal" else "shot",
                 "end_x": end[0], "end_y": end[1], "outcome": outcome}]

    if kind == "Duel" and r.get("duel", {}).get("type", {}).get("name") == "Tackle":
        raw = _outcome(r["duel"]) or ""
        return [{**base, "type": "tackle",
                 "outcome": "lost" if raw.startswith("Lost") else "won"}]

    if kind == "Foul Committed":
        out = [{**base, "type": "foul"}]
        card = r.get("foul_committed", {}).get("card", {}).get("name")
        if card:
            out.append({**base, "type": "card", "detail": card.lower()})
        return out

    if kind == "Substitution":
        s = r["substitution"]
        return [{"type": "substitution",
                 "player_on": short(s.get("replacement", {})),
                 "detail": (_outcome(s) or "").lower() or None}]

    if kind == "Half Start":
        return [{"type": "period_start"}]
    if kind == "Half End":
        return [{"type": "period_end"}]
    if kind == "Injury Stoppage":
        return [{**base, "type": "injury"}]

    return []


def _convert_match(match_id: int) -> list[Event]:
    raw = fetch_json(f"events/{match_id}.json")   # FileNotFoundError if no such match
    names = _short_names(match_id)

    def short(block: dict):
        # The short name if we have one, otherwise the full name (or None).
        return names.get(block.get("id"), block.get("name"))

    events: list[Event] = []
    # The first team is treated as home. StatsBomb's match list says who the
    # official home team is, so we use that when we have it. Otherwise it is
    # whichever team the events file mentions first.
    first_team = home_team(match_id)

    for r in raw:
        if r.get("period", 1) >= 5:   # skip the penalty shootout
            continue
        team = r.get("team", {}).get("name")
        if not team:
            continue

        for fields in _convert(r, short):
            first_team = first_team or team

            # Both halves of a period start and end are logged once per team,
            # so keep only the first team's copy.
            if fields["type"] in ("period_start", "period_end") and team != first_team:
                continue

            # StatsBomb has both teams attacking toward x = 120. Our simulator
            # mirrors the second team so the teams face opposite goals, so we
            # do the same here.
            if team != first_team:
                for key in ("x", "end_x"):
                    if fields.get(key) is not None:
                        fields[key] = 120 - fields[key]

            events.append(Event(
                id=len(events) + 1,
                minute=r["minute"],
                second=r.get("second", 0),
                team=team,
                player=short(r.get("player", {})),
                **fields,   # unpacks the dict into keyword arguments
            ))

    return events


def _saved_path(match_id: int) -> Path:
    return SAVE_DIR / f"v{CONVERTER_VERSION}_{match_id}.json"


def _load_saved(match_id: int) -> list[Event] | None:
    # Returns the saved match, or None if there is no usable file. A file that
    # is damaged or from an older shape of Event is treated as missing, and
    # the match is simply downloaded again.
    path = _saved_path(match_id)
    if not path.exists():
        return None
    try:
        return [Event(**e) for e in json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError) as err:
        print(f"[real_data] ignoring saved match {match_id}: {err}")
        return None


def _save(match_id: int, events: list[Event]) -> None:
    # Written to a temporary file first and then renamed, so a crash half way
    # through can never leave a broken file behind. A failed save is not an
    # error: the match still plays, it just won't be instant next time.
    path = _saved_path(match_id)
    try:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps([e.model_dump() for e in events]), encoding="utf-8")
        temp.replace(path)
    except OSError as err:
        print(f"[real_data] could not save match {match_id}: {err}")


_cache: OrderedDict[int, list[Event]] = OrderedDict()
_lock = threading.Lock()


def load_real_match(match_id: int) -> list[Event]:
    # One lock around the whole load, so if the page asks for the same match
    # twice at once (it does), the second request waits and then reuses the
    # first one's result instead of downloading the match again.
    with _lock:
        if match_id in _cache:
            _cache.move_to_end(match_id)   # most recently used goes last
            return _cache[match_id]

        events = _load_saved(match_id)
        if events is None:
            events = _convert_match(match_id)
            # Only save when the match list is ready. Without it we can't be
            # sure which team is the official home team, and we don't want to
            # save a match with home and away the wrong way round.
            if home_team(match_id) is not None:
                _save(match_id, events)

        _cache[match_id] = events
        while len(_cache) > MAX_CACHED:
            _cache.popitem(last=False)      # drop the least recently used
        return events