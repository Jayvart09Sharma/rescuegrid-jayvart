"""Replay adapter: plays a folder of images or a video file into the vision service on a timer, one frame per
request, exactly as a live camera adapter would. Works for drones and road cameras (same endpoint).

  python replay.py footage.mp4 --source-id drone-1 --fps 2
  python replay.py footage.mp4 --source-id roadcam-3 --source-type roadcam --fps 2
  python replay.py clip.mp4 --source-id drone-1 --entities '{"road": "Main Street"}' --ts-start 2026-09-25T14:00:32Z --wait

--ts-start maps video time onto the scenario clock (frame timestamp = ts-start + offset in the video), so the
claims land in the graph at scenario time. --wait blocks until Qwen has answered and prints the claims produced.
"""
import argparse, glob, os, sys, time
from datetime import datetime, timedelta
import httpx

p = argparse.ArgumentParser()
p.add_argument("path"); p.add_argument("--url", default=os.environ.get("RESCUEGRID_VISION", "http://127.0.0.1:8091"))
p.add_argument("--source-id", default="drone-1"); p.add_argument("--source-type", default="drone")
p.add_argument("--fps", type=float, default=2.0, help="frames per second sent to the service")
p.add_argument("--speed", type=float, default=1.0, help="playback speed; 0 = as fast as the service answers")
p.add_argument("--entities", help='JSON override of camera_entities.json, e.g. {"building": "Building-14"}')
p.add_argument("--ts-start", help="ISO timestamp of the first frame (scenario time); default: wall clock")
p.add_argument("--loop", action="store_true"); p.add_argument("--wait", action="store_true")
p.add_argument("--quiet", action="store_true")
a = p.parse_args()
t_start = datetime.fromisoformat(a.ts_start.replace("Z", "+00:00")) if a.ts_start else None


def frames():
    """(name, jpeg bytes, seconds into the footage)"""
    if os.path.isdir(a.path):
        for i, f in enumerate(sorted(glob.glob(os.path.join(a.path, "*.[jJpP][pPnN][gG]")))):
            yield os.path.basename(f), open(f, "rb").read(), i / a.fps
    else:
        import cv2
        cap = cv2.VideoCapture(a.path); src_fps = cap.get(cv2.CAP_PROP_FPS) or 30; step = max(1, round(src_fps / a.fps)); i = 0
        if not cap.isOpened(): sys.exit(f"cannot open {a.path}")
        while True:
            ok, fr = cap.read()
            if not ok: break
            if i % step == 0:
                ok, buf = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 85]); yield f"frame{i:06d}.jpg", buf.tobytes(), i / src_fps
            i += 1


def ts_for(offset):
    return (t_start + timedelta(seconds=offset)).isoformat(timespec="milliseconds").replace("+00:00", "Z") if t_start else None


t_run = time.time(); n = 0
while True:
    t_pass = time.time()
    for name, data, offset in frames():
        form = {"source_id": a.source_id, "source_type": a.source_type, "frame_ref": f"{os.path.basename(a.path)}#{name}"}
        if a.entities: form["entities"] = a.entities
        if ts_for(offset): form["ts"] = ts_for(offset)
        t0 = time.time()
        r = httpx.post(f"{a.url}/v1/vision/frame", files={"file": (name, data, "image/jpeg")}, data=form, timeout=60)
        r.raise_for_status(); r = r.json(); n += 1
        if not a.quiet:
            labels = ",".join(sorted({d["label"] for d in r["detections"]})) or "-"
            print(f"{name:20s} tier1={r['latency_ms']:5.1f}ms rtt={(time.time()-t0)*1000:4.0f}ms "
                  f"scene={r['scene']['label']}:{r['scene']['conf']:.2f} [{labels}] gate={r['gate']['decision']}", flush=True)
        if a.speed > 0:
            time.sleep(max(0.0, t_pass + offset / a.speed + 1 / (a.fps * a.speed) - time.time()))
    if not a.loop: break

print(f"sent {n} frames in {time.time()-t_run:.1f}s", flush=True)
if a.wait:
    while True:  # the service has one Qwen worker; wait until it has nothing queued for this camera
        s = httpx.get(f"{a.url}/v1/vision/stats").json()
        if s["gate_triggers"] <= s["vlm_calls"] + s["vlm_merged"]: break
        time.sleep(0.2)
    time.sleep(0.3)  # let the in-order bus poster finish
    for c in httpx.get(f"{a.url}/v1/vision/claims", params={"source_id": a.source_id}).json()["claims"]:
        e = c["event"]
        print(f"CLAIM {e['timestamp']} {e['entity']} {e['claim']} conf={e['confidence']} ref={e['raw_evidence_ref']} -> {c['bus'].get('action')}")
