#acts like the serializer of drf it checks and validates fields 
from typing import Literal, Optional
from pydantic import BaseModel

"""
Anything that happens during the match is an event.
This includes passes, shots, tackles, fouls, cards, substitutions, injuries, goals, and the start and end of periods.
"""
class Event(BaseModel):
    id: int
    minute: int
    second: int = 0
    #
    type: Literal["pass", "shot", "tackle", "foul", "card",
                  "substitution", "injury", "goal",
                  "period_start", "period_end"]
    team: str
    player: Optional[str] = None
    target: Optional[str] = None     # who a pass was aimed at
    x: Optional[float] = None
    y: Optional[float] = None
    end_x: Optional[float] = None
    end_y: Optional[float] = None
    outcome: Optional[str] = None
    detail: Optional[str] = None
    player_on: Optional[str] = None