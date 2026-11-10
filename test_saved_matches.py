# Checks that a real match is saved to disk on first load and then loaded
# from that file instead of being downloaded again. Needs internet the first
# time only. Run it with: py test_saved_matches.py

import time

from app import catalogue, real_data

MATCH_ID = 3943077   # Argentina v Colombia, any real match id works

catalogue.warm_up()   # so the home team is known and the save is allowed

path = real_data._saved_path(MATCH_ID)
path.unlink(missing_ok=True)   # start clean so the first load must download

start = time.time()
first = real_data.load_real_match(MATCH_ID)
download_time = time.time() - start
print(f"first load (download): {download_time:.2f}s, {len(first)} events")
print("file saved:", path.exists())

real_data._cache.clear()   # forget it in memory, like a server restart would

start = time.time()
second = real_data.load_real_match(MATCH_ID)
disk_time = time.time() - start
print(f"second load (from disk): {disk_time:.2f}s, {len(second)} events")

same = [e.model_dump() for e in first] == [e.model_dump() for e in second]
print("identical to the download:", same)
print("PASS" if path.exists() and same and disk_time < download_time else "CHECK THIS")