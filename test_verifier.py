# Runs planted claims through the Verifier with each model and scores them.
# Run from the project folder: python test_verifier.py

from app import agents
from app.moments import find_moments
from app.simulator import simulate_match

MODELS = [ "qwen/qwen3.8-27b"]

# (claim, should it pass?). All are about the Nunes injury moment.
CLAIMS = [
    ("Nunes pain, doctor comot am, Lopes don enter for place", False),
    ("Nunes don injure, Lopes don enter for him for minute 61", True),
    ("Nunes don injure for minute 65, Lopes don enter", False),
    ("Nunes don injure, Silva don enter for him", False),
    ("Nunes don injure and Home dey lead 1-0", False),
    ("Nunes played the full match", False),
    ("Nunes was subbed off injured and the score was still 0-0", True),
]

events = simulate_match(42)
moment = next(m for m in find_moments(events) if m.kind == "injury_sub")
facts = agents.facts_for(moment, events)

for model in MODELS:
    agents.VERIFIER_MODEL = model   # verify() reads this each time it runs
    right = 0
    print(f"\n{model}")
    for claim, should_pass in CLAIMS:
        passed = agents.check_line(claim, facts, "pidgin").verdict == "pass"
        ok = passed == should_pass
        right += ok
        print(f"  {'OK  ' if ok else 'MISS'} {claim}")
    print(f"  {right}/{len(CLAIMS)} correct")