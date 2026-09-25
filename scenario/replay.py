"""Master replay runner: plays the canonical scenario into the event bus on one clock.
    .venv-free; run with system python3:
    python3 replay.py                                   # every source, real time
    python3 replay.py --sources sensor,gps --speed 10   # only some sources, 10x speed
    python3 replay.py --sources drone_vision            # drone/road-camera clips -> Aditya's vision v2 (:8091) live
    python3 replay.py --scripted-vision                 # post the scripted drone claims instead (vision not needed)
Jayvant's per-source scripts do the same for their source; running this for everything is the
one-command rehearsal. Do not run both for the same source at once (duplicate event_ids are skipped by the agent anyway).

Drone / road-camera events: when `details.media` is a video clip, the clip is played through the vision
service with Aditya's replay adapter (real time on the same clock, `--ts-start` = the event's timestamp),
and the service itself posts the contract claim it derives (YOLO-World + CLIP gate -> Qwen3-VL -> bus).
The scripted claim in scenario.json is then only the EXPECTED result (printed, compared at the end).
Nothing is posted for a clip unless the detector saw it: if v2 produces no claim the runner only WARNS
(no scripted fallback). --scripted-vision (or vision down) posts the scripted claims instead, for a backend-only
dry run. `details.entities` (JSON) and `details.fps` are passed to the adapter, so any new clip can be pointed at
any seeded entity without editing camera_entities.json. Events with a still image are sent as one forced frame (tier-1 result
attached as details.live_tier1) and the scripted claim is posted, as before."""
import argparse, json, os, subprocess, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rescuegrid_events import ROOT, ScenarioClock, load_scenario, log_line, post_event, scenario_events

VISION_URL = os.environ.get("RESCUEGRID_VISION", "http://127.0.0.1:8091")
VISION_PY = os.environ.get("RESCUEGRID_VISION_PY", os.path.join(ROOT, "vision", ".venv", "bin", "python"))
VISION_ADAPTER = os.environ.get("RESCUEGRID_VISION_ADAPTER", "/home/hp2/Aditya/vision/replay.py")
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".webm")


def vision_up() -> bool:
    try:
        with urllib.request.urlopen(f"{VISION_URL}/health", timeout=3) as r:
            return json.load(r).get("status") == "ok"
    except Exception:
        return False


def vision_reset():
    """Forget every camera's hazard memory so a rehearsal starts clean (one Qwen call per new hazard)."""
    try:
        urllib.request.urlopen(urllib.request.Request(f"{VISION_URL}/v1/vision/reset", data=b"", method="POST"), timeout=5).read()
    except Exception as e:
        print(f"      vision reset failed ({e})", file=sys.stderr)


def vision_claims() -> list:
    try:
        with urllib.request.urlopen(f"{VISION_URL}/v1/vision/claims?limit=50", timeout=5) as r:
            return json.load(r)["claims"]
    except Exception:
        return []


def live_still(ev: dict) -> tuple[dict | None, int]:
    """Push one still through the vision service (forced Qwen look, no claim from v2) so the tier-1 result is live."""
    media = os.path.join(ROOT, ev["details"]["media"])
    data = open(media, "rb").read()
    boundary = "----rgboundary"
    parts = [(f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(media)}"\r\nContent-Type: image/jpeg\r\n\r\n').encode() + data]
    for k, v in (("source_id", ev["details"].get("camera", "drone-1")), ("source_type", cam_type(ev)), ("frame_ref", ev["raw_evidence_ref"]), ("ts", ev["timestamp"]), ("force_vlm", "true")):
        parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}'.encode())
    body = b"".join(f"--{boundary}\r\n".encode() + p + b"\r\n" for p in parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{VISION_URL}/v1/vision/frame", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r), len(data)
    except Exception as e:
        print(f"      vision service unavailable ({e}); posting scripted claim only", file=sys.stderr)
        return None, len(data)


def cam_type(ev: dict) -> str:
    return "roadcam" if "roadcam" in ev["details"].get("camera", "") else "drone"


def start_clip(ev: dict, speed: float) -> subprocess.Popen:
    """Play the event's clip into the vision service with Aditya's adapter: one frame per request at 2 fps
    (scaled by the replay speed), frame timestamps on the scenario clock. Returns immediately; v2 posts the claim."""
    media = os.path.join(ROOT, ev["details"]["media"])
    cmd = [VISION_PY, VISION_ADAPTER, media, "--url", VISION_URL, "--source-id", ev["details"].get("camera", "drone-1"),
           "--source-type", cam_type(ev), "--ts-start", ev["timestamp"], "--speed", str(speed), "--quiet",
           "--fps", str(ev["details"].get("fps", 2))]
    if ev["details"].get("entities"): cmd += ["--entities", json.dumps(ev["details"]["entities"])]
    return subprocess.Popen(cmd, cwd=os.path.dirname(VISION_ADAPTER), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


ap = argparse.ArgumentParser()
ap.add_argument("--sources", default="", help="comma list; default all")
ap.add_argument("--speed", type=float, default=None)
ap.add_argument("--from-t", type=float, default=0.0, help="skip events before this scenario second")
ap.add_argument("--scripted-vision", action="store_true", help="post the scripted drone claims instead of playing clips through the vision service")
a = ap.parse_args()
sources = [s.strip() for s in a.sources.split(",") if s.strip()] or None
speed = a.speed if a.speed is not None else float(os.environ.get("RESCUEGRID_SPEED", "1"))
sc = load_scenario(); evs = [e for e in scenario_events(sources, sc) if e["_t"] >= a.from_t]
use_vision = not a.scripted_vision and any(e["source"] == "drone_vision" for e in evs) and vision_up()
print(f"RescueGrid replay: {sc['scenario_name']}  start={sc['scenario_start']}  events={len(evs)}  sources={sources or 'all'}  speed={speed}x  vision={'live ' + VISION_URL if use_vision else 'scripted claims'}")
if use_vision: vision_reset()
run_started = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
clips: list[tuple[dict, subprocess.Popen]] = []
clock = ScenarioClock(speed)
for ev in evs:
    clock.wait_until(ev["_t"])
    media = ev.get("details", {}).get("media")
    if ev["source"] == "drone_vision" and media and media.lower().endswith(VIDEO_EXT) and use_vision:
        clips.append((ev, start_clip(ev, speed)))
        print(log_line(ev) + f"  -> clip {os.path.basename(media)} playing into vision (expected claim; v2 posts its own)", flush=True)
        continue
    raw = None
    if ev["source"] == "drone_vision" and media and not media.lower().endswith(VIDEO_EXT) and use_vision:
        t1, raw = live_still(ev)
        if t1:
            ev["details"]["live_tier1"] = {"scene": t1["scene"], "detections": len(t1["detections"]), "latency_ms": t1["latency_ms"], "vision_event_id": t1["event_id"]}
    reply = post_event(ev, raw_bytes=raw)
    print(log_line(ev, reply), flush=True)

if clips:
    for ev, p in clips:
        _, err = p.communicate()
        if p.returncode != 0:
            print(f"      clip for {ev['event_id']} failed (exit {p.returncode}): {err.strip()[-300:]}", file=sys.stderr)
    # wait until the vision service has no Qwen assessment pending, then compare what it claimed with the script
    for _ in range(300):
        try:
            with urllib.request.urlopen(f"{VISION_URL}/v1/vision/stats", timeout=5) as r: s = json.load(r)
            if s["gate_triggers"] <= s["vlm_calls"] + s["vlm_merged"]: break
        except Exception: break
        time.sleep(0.2)
    time.sleep(0.5)
    got = [c for c in vision_claims() if c.get("posted_at", "") >= run_started]
    print(f"vision claims this run ({len(got)}):")
    for c in got:
        e = c["event"]; print(f"   {e['timestamp']} {e['details']['camera']:<10} {e['entity']:<14} {e['claim']:<10} conf {e['confidence']:.2f} -> {c['bus'].get('action')} ({c['bus'].get('latency_ms', '?')} ms)  ref {e['raw_evidence_ref']}")
    for ev, _ in clips:
        if not any(c["event"]["claim"] == ev["claim"] and c["event"]["details"]["camera"] == ev["details"]["camera"] for c in got):
            print(f"   WARNING: the detector did not produce the expected `{ev['entity']} {ev['claim']}` from {ev['details']['camera']} "
                  f"(evt {ev['event_id']}). Nothing was posted for it: only what vision actually saw is in the graph.", file=sys.stderr)
print("replay finished.")
