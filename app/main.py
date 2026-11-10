# Creates the web app and defines its URLs (endpoints).
#   /        the web page (static/index.html)
#   /ticker  fast path: lines built by code, answers instantly
#   /stream  slow path: AI commentary, cached, streamed as each moment finishes
#   /search  find a real match by team, competition, year or stage
#            (optionally only men's or only women's matches)
# The other endpoints are for testing the pieces on their own.

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from app import catalogue
from app.agents import (Commentary, Language, cached_commentary, facts_for,
                        ticker_fallback, verify)
from app.models import Event
from app.moments import Moment, find_moments
from app.real_data import load_real_match
from app.simulator import club_names, simulate_match
from app.ticker import ticker_line


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once when the server starts. We build the match list in the
    # background, so the server is ready at once and the first search is fast.
    task = asyncio.create_task(asyncio.to_thread(catalogue.warm_up))
    yield
    task.cancel()


app = FastAPI(title="PitchTrace", lifespan=lifespan)

MAX_PARALLEL = 2

# An optional team name typed in the URL. Only letters, digits, spaces and a
# few marks are allowed, up to 24 characters, because the name ends up inside
# the AI prompts and we don't want strangers writing instructions there.
TeamName = Annotated[str | None, Query(pattern=r"^[A-Za-z0-9 .'-]{1,24}$")]

# Which matches a search should include.
Gender = Literal["all", "men", "women"]


def _real_events(match_id: int) -> list[Event]:
    try:
        return load_real_match(match_id)   # downloads the match the first time
    except FileNotFoundError:
        raise HTTPException(status_code=404,
                            detail=f"No match {match_id} in StatsBomb's open data")
    except RuntimeError as err:
        raise HTTPException(status_code=502, detail=str(err))


def _events_for(seed: int, match_id: int | None, home: str | None = None,
                away: str | None = None) -> list[Event]:
    if match_id:
        return _real_events(match_id)
    home, away = club_names(seed, home, away)
    if home.lower() == away.lower():
        raise HTTPException(status_code=400, detail="The two teams need different names")
    return simulate_match(seed, home, away)


# Searches StatsBomb's open data by team, competition, year or stage, for
# example "argentina france" or "world cup 2022 final". It can't search by
# player, because the match listings don't include players. "gender" limits
# the results to men's or women's matches.
@app.get("/search")
def search(q: Annotated[str, Query(min_length=2, max_length=60)],
           limit: Annotated[int, Query(ge=1, le=50)] = 20,
           gender: Gender = "all"):
    try:
        return catalogue.search(q, limit, gender)
    except (RuntimeError, FileNotFoundError) as err:
        raise HTTPException(status_code=502, detail=str(err))


@app.get("/")
def page():
    return FileResponse("static/index.html")


@app.get("/match", response_model=list[Event])
def match(seed: int = 42, home: TeamName = None, away: TeamName = None):
    return _events_for(seed, None, home, away)


@app.get("/real_match/{match_id}", response_model=list[Event])
def real_match(match_id: int):
    return _real_events(match_id)


@app.get("/moments", response_model=list[Moment])
def moments(seed: int = 42, match_id: int | None = None,
            home: TeamName = None, away: TeamName = None):
    return find_moments(_events_for(seed, match_id, home, away))


@app.get("/verify_test")
def verify_test(claim: str, seed: int = 42):
    events = simulate_match(seed)
    moment = next(m for m in find_moments(events) if m.kind == "injury_sub")
    return verify(claim, facts_for(moment, events))


@app.get("/commentary", response_model=list[Commentary])
def commentary(seed: int = 42, match_id: int | None = None, limit: int = 3,
               kind: str | None = None, language: Language = "pidgin",
               home: TeamName = None, away: TeamName = None):
    events = _events_for(seed, match_id, home, away)
    found = [m for m in find_moments(events)
             if kind is None or m.kind == kind][:limit]
    return [cached_commentary(m, events, language) for m in found]


# Fast path. No model calls. "t" is the match time in seconds of the line's
# LAST event, which is when it becomes visible, so a line can never give a
# goal away before it happens on screen.
@app.get("/ticker")
def ticker(seed: int = 42, match_id: int | None = None,
           language: Language = "pidgin", home: TeamName = None,
           away: TeamName = None):
    events = _events_for(seed, match_id, home, away)
    by_id = {e.id: e for e in events}
    lines = []
    for m in find_moments(events):
        last = by_id[max(m.event_ids)]
        lines.append({
            "id": f"{m.kind}-{m.event_ids[0]}",
            "kind": m.kind,
            "minute": last.minute,
            "t": last.minute * 60 + last.second,
            "text": ticker_line(m, events, language),
            "evidence": m.event_ids,
        })
    return sorted(lines, key=lambda x: (x["t"], x["id"]))


@app.get("/stream")
async def stream(seed: int = 42, match_id: int | None = None,
                 language: Language = "pidgin", limit: int = 0,
                 home: TeamName = None, away: TeamName = None):
    # Run in a worker thread, since loading a real match can download a file.
    events = await asyncio.to_thread(_events_for, seed, match_id, home, away)
    found = find_moments(events)
    if limit:
        found = found[:limit]
    gate = asyncio.Semaphore(MAX_PARALLEL)

    async def work(m: Moment) -> dict:
        async with gate:
            try:
                result = await asyncio.to_thread(cached_commentary, m, events, language)
            except Exception as exc:
                print(f"[stream] {m.kind} minute {m.minute} failed: {exc!r}")
                result = ticker_fallback(m, events, language)
        return {"id": f"{m.kind}-{m.event_ids[0]}", **result.model_dump()}

    async def messages():
        tasks = [asyncio.create_task(work(m)) for m in found]
        for finished in asyncio.as_completed(tasks):
            yield f"data: {json.dumps(await finished)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(messages(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})