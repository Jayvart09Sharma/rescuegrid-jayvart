"""Benchmark the vision service: tier-1 latency per frame and tier-2 keyframe latency."""
import glob, os, sys, time, httpx
URL = os.environ.get("VISION_URL", "http://127.0.0.1:8091"); N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
imgs = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "samples", "*.jpg")))
print(f"{'image':22s} {'tier1 p50':>10s} {'rtt p50':>9s}  detections")
for f in imgs:
    data = open(f, "rb").read(); t1 = []; rtt = []; last = None
    for i in range(N):
        t0 = time.time()
        r = httpx.post(f"{URL}/v1/vision/frame", files={"file": (os.path.basename(f), data, "image/jpeg")},
                       data={"source_id": "bench-" + os.path.basename(f)[:-4], "force_vlm": "true" if i == 0 else "false"}, timeout=60).json()
        rtt.append((time.time() - t0) * 1000); t1.append(r["latency_ms"]); last = r
    labels = f"scene={last['scene']['label']}:{last['scene']['conf']:.2f}  " + ", ".join(f"{d['label']}:{d['conf']:.2f}" for d in sorted(last["detections"], key=lambda d: -d["conf"])[:3])
    print(f"{os.path.basename(f):22s} {sorted(t1)[N//2]:8.1f}ms {sorted(rtt)[N//2]:7.0f}ms  {labels}")
print("\nwaiting for tier-2 assessments...")
deadline = time.time() + 120
while time.time() < deadline:
    evs = httpx.get(f"{URL}/v1/vision/events", params={"tier": 2}).json()["events"]
    if len([e for e in evs if e["source"]["id"].startswith("bench-")]) >= len(imgs): break
    time.sleep(1)
for e in evs:
    if not e["source"]["id"].startswith("bench-"): continue
    a = e["assessment"]
    print(f"\n[{e['source']['id']}] {e['latency_ms']}ms  {'ERR ' + e['error'] if not a else ''}")
    if a: print(f"  {a['description']}\n  hazards={[(h['type'], h['severity'], h['confidence']) for h in a['hazards']]} road_passable={a['road_passable']} people={a['people_visible']}")
print("\nstats:", httpx.get(f"{URL}/v1/vision/stats").json())
