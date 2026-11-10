# A searchable list of every match in StatsBomb's free open data.
# It is built from their live GitHub repo and kept in memory (nothing is saved
# to disk). Searches use the in-memory copy, and the copy refreshes itself in
# the background every few hours, so matches StatsBomb adds later show up on
# their own. Each match is tagged "men" or "women" (from the competition it
# belongs to) so searches can be filtered by it. This file also holds
# fetch_json, the one function that downloads anything from StatsBomb's repo.

import json
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# StatsBomb's repo moved to the hudl organisation, and the old address still
# redirects, so we try both.
BASES = [
    "https://raw.githubusercontent.com/hudl/open-data/master/data/",
    "https://raw.githubusercontent.com/statsbomb/open-data/master/data/",
]
REFRESH_AFTER = 6 * 60 * 60   # seconds before the list is refreshed

# StatsBomb writes "male" / "female"; we show "men" / "women".
GENDER_LABELS = {"male": "men", "female": "women"}

_matches: list[dict] = []
_built_at = 0.0
_refreshing = False
_build_gate = threading.Lock()   # makes sure only one build runs at a time


def fetch_json(path: str):
    # Gets one file from StatsBomb's repo, trying each address in turn.
    # Raises FileNotFoundError if it doesn't exist, RuntimeError if the
    # repo can't be reached.
    last_error = None
    for base in BASES:
        try:
            req = urllib.request.Request(base + path, headers={"User-Agent": "pitchtrace"})
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as err:
            last_error = FileNotFoundError(path) if err.code == 404 else err
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            last_error = err
    if isinstance(last_error, FileNotFoundError):
        raise last_error
    raise RuntimeError(f"could not reach StatsBomb's open data: {last_error}")


def _plain(text: str) -> str:
    # Lower case with accents removed, so "Atlético" is found by "atletico".
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _build() -> list[dict]:
    competitions = fetch_json("competitions.json")
    pairs = sorted({(c["competition_id"], c["season_id"]) for c in competitions})
    # Which competition-season is men's and which is women's.
    genders = {
        (c["competition_id"], c["season_id"]):
            GENDER_LABELS.get(str(c.get("competition_gender", "")).lower(), "")
        for c in competitions
    }

    def season(pair):
        # One failed season shouldn't sink the whole list.
        try:
            return pair, fetch_json(f"matches/{pair[0]}/{pair[1]}.json")
        except (FileNotFoundError, RuntimeError):
            return pair, []

    with ThreadPoolExecutor(8) as pool:   # 8 downloads at a time
        seasons = list(pool.map(season, pairs))

    matches = []
    for pair, listing in seasons:
        gender = genders.get(pair, "")
        for m in listing:
            if m.get("match_status") != "available":
                continue
            home = m["home_team"]["home_team_name"]
            away = m["away_team"]["away_team_name"]
            comp = m["competition"]["competition_name"]
            year = m["season"]["season_name"]
            stage = (m.get("competition_stage") or {}).get("name", "")
            matches.append({
                "match_id": m["match_id"],
                "home": home, "away": away,
                "home_score": m.get("home_score"), "away_score": m.get("away_score"),
                "date": m.get("match_date", ""),
                "competition": comp, "season": year, "stage": stage,
                "gender": gender,   # "men", "women", or "" if StatsBomb didn't say
                # Everything a search can match on, as one lower-case string.
                "text": _plain(f"{home} {away} {comp} {year} {stage} {m.get('match_date', '')}"),
                "teams": _plain(f"{home} {away}"),
            })
    return matches


def _refresh() -> None:
    global _matches, _built_at, _refreshing
    try:
        built = _build()
        _matches, _built_at = built, time.time()
    finally:
        _refreshing = False


def _refresh_quietly() -> None:
    # Used for background refreshes: if it fails we keep the old list.
    try:
        _refresh()
    except (RuntimeError, FileNotFoundError) as err:
        print(f"[catalogue] refresh failed, keeping the old list: {err}")


def ensure_index() -> list[dict]:
    global _refreshing
    if not _matches:
        with _build_gate:   # others wait here, then find the finished list
            if not _matches:
                _refresh()
    elif time.time() - _built_at > REFRESH_AFTER and not _refreshing:
        # Serve the old list now and refresh it in the background.
        _refreshing = True
        threading.Thread(target=_refresh_quietly, daemon=True).start()
    return _matches


def warm_up() -> None:
    # Called when the server starts, so the first search is already fast.
    try:
        ensure_index()
    except (RuntimeError, FileNotFoundError) as err:
        print(f"[catalogue] could not build the match list yet: {err}")


def home_team(match_id: int) -> str | None:
    # The official home team for a match, if the list has been built already.
    # It never waits for a download: with no list yet it just returns None.
    return next((m["home"] for m in _matches if m["match_id"] == match_id), None)


def search(query: str, limit: int = 20, gender: str = "all") -> dict:
    # gender is "men", "women" or "all" (anything else counts as "all").
    index = ensure_index()
    if gender in ("men", "women"):
        index = [m for m in index if m["gender"] == gender]
    # "brazil v germany" and "brazil vs germany" both mean "brazil germany".
    words = _plain(query).replace(" vs ", " ").replace(" v ", " ").split()
    found = []
    for m in index:
        if not all(w in m["text"] for w in words):
            continue
        # A word matching a team name counts for more than one matching the
        # competition or year. Ties go to the most recent match.
        score = sum(2 if w in m["teams"] else 1 for w in words)
        found.append((score, m["date"], m))
    found.sort(key=lambda r: (r[0], r[1]), reverse=True)
    shown = [{k: v for k, v in m.items() if k not in ("text", "teams")}
             for _, _, m in found[:limit]]
    return {"indexed": len(index), "found": len(found), "results": shown}