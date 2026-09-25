"""Drone camera adapter with a flight plan (Shresth, 2026-09-25). Plays a clip into the vision service like Aditya's
replay.py, but the drone MOVES: its position follows an ordered list of waypoints (graph entities with lat/lon) spread
over the clip's duration, and every frame is tagged with the building / road / bridge nearest the drone at that moment.
Claims the detector makes on that frame are attributed to those entities. A live drone would send the same thing from
its GPS; here the operator's planned route stands in for it. Nothing about the footage's content is assumed.

  python flight_stream.py clip.mp4 --source-id drone-1 --plan plan.json [--fps 2] [--loop] [--ts-start ISO] [--telemetry URL]

plan.json: {"waypoints": [{"id": "Building-14", "name": "Building 14", "lat": .., "lon": .., "entities": {"building": "Building-14", "road": "Main Street"}}, ...],
            "speed_mps": 12}   # cruise speed between waypoints; the drone hovers at each waypoint for the remaining share of the clip
"""
import argparse, json, os, sys, time, math
from datetime import datetime, timedelta
import cv2, httpx

p = argparse.ArgumentParser()
p.add_argument("path"); p.add_argument("--url", default=os.environ.get("RESCUEGRID_VISION", "http://127.0.0.1:8091"))
p.add_argument("--source-id", default="drone-1"); p.add_argument("--source-type", default="drone")
p.add_argument("--plan", required=True); p.add_argument("--fps", type=float, default=2.0); p.add_argument("--speed", type=float, default=1.0)
p.add_argument("--ts-start"); p.add_argument("--loop", action="store_true"); p.add_argument("--telemetry", default=os.environ.get("RESCUEGRID_GATEWAY", "http://127.0.0.1:8097"))
a = p.parse_args()
plan = json.load(open(a.plan)); wps = plan["waypoints"]; speed = float(plan.get("speed_mps") or 12.0)
t_start = datetime.fromisoformat(a.ts_start.replace("Z", "+00:00")) if a.ts_start else None


def hav(lat1, lon1, lat2, lon2):
    R = 6371000.0; p1, p2 = math.radians(lat1), math.radians(lat2); dp = p2 - p1; dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


cap = cv2.VideoCapture(a.path)
if not cap.isOpened(): sys.exit(f"cannot open {a.path}")
src_fps = cap.get(cv2.CAP_PROP_FPS) or 30; n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); duration = n_frames / src_fps
step = max(1, round(src_fps / a.fps))

# timeline: the whole plan spans the clip. Legs between waypoints take distance/speed seconds but never more than 40 % of
# the clip in total (the drone flies faster if the route is long); the rest is split as hover time at each waypoint.
dists = [hav(wps[i]["lat"], wps[i]["lon"], wps[i + 1]["lat"], wps[i + 1]["lon"]) for i in range(len(wps) - 1)]
leg_total = min(sum(dists) / speed, 0.4 * duration) if dists else 0.0
legs = [leg_total * d / sum(dists) for d in dists] if dists and sum(dists) > 0 else [0.0 for _ in dists]
hover = max(0.0, duration - leg_total) / max(1, len(wps))
sched = []  # (t0, t1, from_idx, to_idx) ; from==to means hovering
t = 0.0
for i, w in enumerate(wps):
    sched.append((t, t + hover, i, i)); t += hover
    if i < len(wps) - 1: sched.append((t, t + legs[i], i, i + 1)); t += legs[i]
total = max(t, duration)


def where(tc: float):
    """(lat, lon, entities) at clip time tc."""
    tc = tc % total if total > 0 else 0.0
    for t0, t1, i, j in sched:
        if t0 <= tc <= t1 or (t0, t1, i, j) == sched[-1]:
            if i == j or t1 <= t0: return wps[i]["lat"], wps[i]["lon"], wps[i]["entities"], wps[i]["id"]
            k = (tc - t0) / (t1 - t0)
            lat = wps[i]["lat"] + (wps[j]["lat"] - wps[i]["lat"]) * k; lon = wps[i]["lon"] + (wps[j]["lon"] - wps[i]["lon"]) * k
            near = wps[i] if k < 0.5 else wps[j]
            return lat, lon, near["entities"], near["id"]
    w = wps[-1]; return w["lat"], w["lon"], w["entities"], w["id"]


def ts_for(offset):
    return (t_start + timedelta(seconds=offset)).isoformat(timespec="milliseconds").replace("+00:00", "Z") if t_start else None


sent = 0; t_run = time.time()
while True:
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0); i = 0; t_pass = time.time()
    while True:
        ok, fr = cap.read()
        if not ok: break
        if i % step == 0:
            offset = i / src_fps
            lat, lon, ents, over = where(offset)
            ok2, buf = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 85])
            form = {"source_id": a.source_id, "source_type": a.source_type, "frame_ref": f"{os.path.basename(a.path)}#frame{i:06d}.jpg", "entities": json.dumps(ents)}
            if ts_for(offset): form["ts"] = ts_for(offset)
            t0 = time.time()
            r = httpx.post(f"{a.url}/v1/vision/frame", files={"file": (f"frame{i:06d}.jpg", buf.tobytes(), "image/jpeg")}, data=form, timeout=60)
            r.raise_for_status(); res = r.json(); sent += 1
            try: httpx.post(f"{a.telemetry}/cameras/{a.source_id}/telemetry", json={"lat": lat, "lon": lon, "over": over, "entities": ents, "t": offset, "frame": i}, timeout=3)
            except Exception: pass
            print(f"frame{i:06d}.jpg tier1={res['latency_ms']:5.1f}ms rtt={(time.time()-t0)*1000:4.0f}ms scene={res['scene']['label']}:{res['scene']['conf']:.2f} over={over} gate={res['gate']['decision']}", flush=True)
            if a.speed > 0: time.sleep(max(0.0, t_pass + offset / a.speed + 1 / (a.fps * a.speed) - time.time()))
        i += 1
    if not a.loop: break
print(f"sent {sent} frames in {time.time()-t_run:.1f}s", flush=True)
