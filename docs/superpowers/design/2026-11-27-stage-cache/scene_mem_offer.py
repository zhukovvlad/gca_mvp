import sys, time, tracemalloc, gc
sys.path.insert(0, ".")
import sqlalchemy as sa
from crud import our_price
from database import SessionLocal
db = SessionLocal()
db.execute(sa.text("SET TRANSACTION READ ONLY"))
head = our_price._load_head(db, 7, 23)
parts = [p for p in our_price._load_participants(db, 23) if p[0] == 76]
t = time.perf_counter(); our_price._build_scene(db, head, parts, inflation_series_id=1); print(f"offer scene build (no tracemalloc) {time.perf_counter()-t:.2f}s")
gc.collect(); tracemalloc.start()
scene = our_price._build_scene(db, head, parts, inflation_series_id=1)
cur, peak = tracemalloc.get_traced_memory()
print(f"offer scene {cur/2**20:.0f} MiB peak {peak/2**20:.0f} MiB kp lines {sum(len(v) for v in scene.kp_lines.values())}")
db.rollback(); db.close()
