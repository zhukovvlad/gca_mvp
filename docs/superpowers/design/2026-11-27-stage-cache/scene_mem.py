import sys, time, tracemalloc, gc
sys.path.insert(0, ".")
import sqlalchemy as sa
from crud import our_price
from database import SessionLocal
db = SessionLocal()
db.execute(sa.text("SET TRANSACTION READ ONLY"))
head = our_price._load_head(db, 7, 23)
parts = our_price._load_participants(db, 23)
gc.collect(); tracemalloc.start()
t = time.perf_counter()
scene = our_price._build_scene(db, head, parts, inflation_series_id=1)
built = time.perf_counter() - t
cur1, _ = tracemalloc.get_traced_memory()
p = next(p for p in scene.participants if p.offer_id == 76)
t = time.perf_counter(); our_price._evaluate(scene, p); ev1 = time.perf_counter() - t
t = time.perf_counter(); our_price._evaluate(scene, p); ev2 = time.perf_counter() - t
cur2, peak = tracemalloc.get_traced_memory()
print(f"build {built:.2f}s  scene {cur1/2**20:.0f} MiB  after 2 evals {cur2/2**20:.0f} MiB  peak {peak/2**20:.0f} MiB  eval1 {ev1:.2f}s eval2 {ev2:.2f}s")
print("participants", len(scene.participants), "kp lines", sum(len(v) for v in scene.kp_lines.values()), "contracts", len(scene.contracts))
db.rollback(); db.close()
