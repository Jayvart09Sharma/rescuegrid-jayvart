"""RescueGrid vision service v2 (Aditya): hazard-gated two-tier pipeline.

Supersedes Shresth/rescuegrid/vision/server.py (v1, kept there as a backup, do not run both on one port).

  frame ──► Tier 1, every frame (~50 ms): YOLO-World objects + CLIP scene class
              │
              ▼ gate (per camera): only HAZARD signals count (a hazard scene, or a hazard object such as
              │ rubble/fire/flood water). Cars, people and trucks are context, never a trigger.
              │   - a hazard must persist GATE_PERSIST frames in a row      (kills one-frame flicker)
              │   - Qwen is called once per hazard per camera               (no re-describing the same collapse)
              │   - re-assessed only every REASSESS_SEC while it persists; dropped after GATE_CLEAR frames without it
              ▼
          Tier 2 (Qwen3-VL-8B via ZRT, ~2 s): VLM_FRAMES frames from the last BUFFER_SEC of this camera in ONE
              request + the detector's confirmed hazard as a hint -> description, hazards, road_passable, people_visible
              ▼
          Contract event (Shresth's six fields: source, timestamp, confidence, entity, claim, raw_evidence_ref)
              -> Kenil's fusion agent via the event bus (RESCUEGRID_BUS). The claim comes from the detector's hazard
              class + Qwen's road_passable; the entity from camera_entities.json (or the frame's `entities` field).

Input is one frame per request, same as v1, so live and replay adapters do not change. Video is split into
frames by the adapter (replay.py), never here.
"""
import base64, hashlib, io, json, os, threading, time, uuid, logging
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

import cv2, numpy as np
import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from PIL import Image

log = logging.getLogger("vision")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
HERE = os.path.dirname(os.path.abspath(__file__))

# ---- config -----------------------------------------------------------------
YOLO_WEIGHTS    = os.environ.get("YOLO_WEIGHTS", os.path.join(HERE, "weights", "yolov8m-worldv2.pt"))
YOLO_CONF       = float(os.environ.get("YOLO_CONF", "0.20"))
YOLO_IMGSZ      = int(os.environ.get("YOLO_IMGSZ", "640"))
SCENE_MODEL     = os.environ.get("SCENE_MODEL", "ViT-L/14")       # CLIP variant; ViT-B/16 is 3x faster, weaker on flood
SCENE_CONF      = float(os.environ.get("SCENE_CONF", "0.5"))      # min prob to report a non-normal scene as THE label
SCENE_MULTI     = float(os.environ.get("SCENE_MULTI", "0.3"))     # every non-normal scene above this is listed in scene.labels
ZRT_URL         = os.environ.get("ZRT_URL", "http://127.0.0.1:8080/v1")
VLM_MODEL       = os.environ.get("VLM_MODEL", "vision")
VLM_MAX_SIDE    = int(os.environ.get("VLM_MAX_SIDE", "640"))     # resize longest side before sending to the VLM
VLM_MAX_TOKENS  = int(os.environ.get("VLM_MAX_TOKENS", "120"))
VLM_FRAMES      = int(os.environ.get("VLM_FRAMES", "4"))         # frames per Qwen request (benchmark: 4 frames ~2.0 s vs 1 frame ~1.8 s)
GATE_SCENE_CONF = float(os.environ.get("GATE_SCENE_CONF", "0.5"))  # a hazard scene counts for the gate at/above this
GATE_OBJ_CONF   = float(os.environ.get("GATE_OBJ_CONF", "0.35"))   # a hazard object counts for the gate at/above this
GATE_PERSIST    = int(os.environ.get("GATE_PERSIST", "3"))       # consecutive frames before a hazard is confirmed
GATE_CLEAR      = int(os.environ.get("GATE_CLEAR", "5"))         # consecutive frames without it before it is dropped
REASSESS_SEC    = float(os.environ.get("REASSESS_SEC", "60"))    # re-ask Qwen about a hazard that is still there
BUFFER_SEC      = float(os.environ.get("BUFFER_SEC", "5"))       # per-camera frame memory Qwen's frames are picked from
BUFFER_MAX      = int(os.environ.get("BUFFER_MAX", "32"))
OPEN_CONF       = float(os.environ.get("OPEN_CONF", "0.6"))      # 'normal' scene confidence for an `open` road report
BUS_URL         = os.environ.get("RESCUEGRID_BUS")               # e.g. http://127.0.0.1:8096 ; unset = dry run (claims kept locally)
CAMERA_MAP_FILE = os.environ.get("CAMERA_MAP", os.path.join(HERE, "camera_entities.json"))
FRAME_DIR       = os.environ.get("FRAME_DIR", os.path.join(HERE, "frames"))   # evidence frames saved here for provenance
EVIDENCE_PREFIX = os.environ.get("EVIDENCE_PREFIX", "vision/frames/")        # raw_evidence_ref = prefix + file; served at /v1/vision/frames/<file>

# YOLO-World vocabulary, split by role. Only HAZARD_OBJECTS can open the gate; CONTEXT_OBJECTS go to Qwen as a hint.
HAZARD_OBJECTS = {  # detector label -> hazard kind (shared with scene keys so either signal feeds the same streak)
    "collapsed building": "collapsed_building", "damaged building": "damaged_building", "rubble": "debris",
    "debris on road": "debris", "flooded road": "flooded_road", "flood water": "flooded_road", "fire": "fire",
    "smoke": "smoke", "fallen tree": "fallen_tree", "downed power line": "downed_power_line",
    "landslide": "landslide", "crashed car": "vehicle_crash",
    "dust cloud": "collapsed_building", "collapsed wall": "collapsed_building", "burning building": "structure_fire"}   # 2026-09-25 (Shresth): disaster-video words
CONTEXT_OBJECTS = ["car", "truck", "bus", "person", "ambulance", "fire truck", "police car", "boat", "helicopter",
                   "injured person", "stretcher", "rescue worker", "firefighter"]
PEOPLE_LABELS = {"person", "injured person", "rescue worker", "firefighter"}
YOLO_CLASSES = list(HAZARD_OBJECTS) + CONTEXT_OBJECTS

# Scene classes (tuned on the team's clips in v1): key -> prompt ensemble (mean text embedding). Edit freely; no retraining.
SCENES = {
    "collapsed_building": ["a collapsed building with rubble after an earthquake", "a multi-storey building with its upper floors collapsed, exposed rooms and bent rebar", "a partially collapsed office building with a large concrete slab fallen against its wall"],
    "flooded_road":       ["a flooded road with water covering the street", "a street submerged in brown flood water", "people wading through flood water on a road"],
    "wildfire_smoke":     ["a wildfire with smoke near a road", "thick smoke from a wildfire over hills"],
    "structure_fire":     ["a burning building with flames"],
    "landslide":          ["a landslide of mud and rock covering a road"],
    "fallen_tree":        ["a fallen tree blocking a road"],
    "vehicle_crash":      ["a car crash with damaged vehicles on the road"],
    "blocked_road":       ["cars stopped behind broken concrete and bricks scattered across the road surface", "a street physically blocked by fallen debris with a barrier of rubble across all lanes, buildings intact", "a pickup truck halted at a pile of debris lying across the pavement at an intersection"],
    "normal":             ["normal traffic on a highway", "an intact street with no damage", "a clear road with cars driving normally", "bumper-to-bumper traffic congestion on a road with nothing blocking it", "a busy multi-lane road full of slow-moving cars, no debris"],
}

# Hazard kind -> (entity type looked up in camera_entities.json, claim in Kenil's vocabulary; docs/EVENT_CONTRACT.md).
# Road claims become `restricted` when Qwen says the road is still passable. Kinds not listed (fire, smoke,
# wildfire_smoke) have no entity to attach to and stay local (tier-2 event only) until someone owns that mapping.
CLAIM_RULES = {
    "collapsed_building": ("building", "collapsed"), "damaged_building": ("building", "damaged"),
    "structure_fire": ("building", "damaged"),
    "blocked_road": ("road", "blocked"), "debris": ("road", "blocked"), "flooded_road": ("road", "blocked"),
    "landslide": ("road", "blocked"), "fallen_tree": ("road", "blocked"), "vehicle_crash": ("road", "blocked"),
    "downed_power_line": ("road", "blocked"),
}

# ---- state ------------------------------------------------------------------
app = FastAPI(title="RescueGrid Vision v2")
EVENTS: deque = deque(maxlen=5000)       # tier-1 and tier-2 vision events (debug / provenance view)
CLAIMS: deque = deque(maxlen=2000)       # contract events produced, with the bus reply (or dry_run)
CAMS: dict = {}                          # source_id -> CamState (perception memory only; world state lives in Neo4j)
STATS = {"frames": 0, "frames_with_hazard": 0, "gate_triggers": 0, "vlm_calls": 0, "vlm_errors": 0, "vlm_merged": 0,
         "claims": 0, "claims_unmapped": 0, "bus_errors": 0,
         "tier1_ms": deque(maxlen=500), "tier2_ms": deque(maxlen=200), "trigger_to_claim_ms": deque(maxlen=200)}
LOCK = threading.Lock()
PENDING: "OrderedDict[str, dict]" = OrderedDict()   # source_id -> waiting Qwen job; a newer trigger merges into it
COND = threading.Condition()
POST_POOL = ThreadPoolExecutor(max_workers=1)       # bus posts in order, off the request path
MODEL = None
CLIP = {}
CAMERA_MAP: dict = {}

os.makedirs(FRAME_DIR, exist_ok=True)


class CamState:
    def __init__(self):
        self.buf = deque(maxlen=BUFFER_MAX)   # {"mono", "ts", "jpeg", "event_id"}
        self.streak = {}                      # kind -> consecutive frames seen
        self.best = {}                        # kind -> best confidence in the current streak
        self.active = {}                      # confirmed kind -> {"since", "assessed", "conf"}
        self.miss = {}                        # active kind -> consecutive frames missing
        self.normal = 0                       # consecutive confident 'normal' frames
        self.open_at = float("-inf")          # last `open` report (monotonic)
        self.prev_gray = None                 # previous frame (320 px gray) for the camera-motion estimate


def now_iso(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
def z(ts: str) -> str: return ts.replace("+00:00", "Z")


def load_camera_map():
    global CAMERA_MAP
    try:
        with open(CAMERA_MAP_FILE) as f:
            CAMERA_MAP = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except FileNotFoundError:
        CAMERA_MAP = {}
    log.info("camera map: %s", CAMERA_MAP)


# ---- tier 1: YOLO-World + CLIP ----------------------------------------------
@app.on_event("startup")
def load_models():
    global MODEL
    from ultralytics import YOLOWorld
    t0 = time.time()
    MODEL = YOLOWorld(YOLO_WEIGHTS)
    MODEL.set_classes(YOLO_CLASSES)
    MODEL.predict(Image.new("RGB", (YOLO_IMGSZ, YOLO_IMGSZ)), imgsz=YOLO_IMGSZ, conf=YOLO_CONF, verbose=False)  # warm-up
    log.info("YOLO-World loaded (%d classes) in %.1fs", len(YOLO_CLASSES), time.time() - t0)
    import clip, torch
    t0 = time.time()
    model, pre = clip.load(SCENE_MODEL, device="cuda"); model.eval()
    with torch.no_grad():
        feats = []
        for k in SCENES:
            e = model.encode_text(clip.tokenize(SCENES[k]).to("cuda")); e = e / e.norm(dim=-1, keepdim=True); e = e.mean(0); feats.append(e / e.norm())
        CLIP.update(model=model, pre=pre, text=torch.stack(feats), keys=list(SCENES), torch=torch)
    scene_head(Image.new("RGB", (224, 224)))
    log.info("CLIP %s scene head loaded (%d scenes) in %.1fs", SCENE_MODEL, len(SCENES), time.time() - t0)
    load_camera_map()
    threading.Thread(target=vlm_worker, daemon=True, name="vlm").start()


def scene_head(img: Image.Image):
    """Zero-shot scene classification of the whole frame. Returns (label, conf, top3, ms, multi)."""
    torch = CLIP["torch"]; t0 = time.perf_counter()
    with torch.no_grad():
        x = CLIP["pre"](img).unsqueeze(0).to("cuda")
        f = CLIP["model"].encode_image(x); f = f / f.norm(dim=-1, keepdim=True)
        p = (100 * f @ CLIP["text"].T).softmax(-1)[0].tolist()
    ms = (time.perf_counter() - t0) * 1000
    ranked = sorted(zip(CLIP["keys"], p), key=lambda x: -x[1])
    label, conf = ranked[0]
    if label != "normal" and conf < SCENE_CONF: label = "uncertain"
    multi = [(k, round(v, 3)) for k, v in ranked if v >= SCENE_MULTI and k != "normal"]
    return label, round(conf, 3), [(k, round(v, 3)) for k, v in ranked[:3]], ms, multi


def tier1(img: Image.Image):
    t0 = time.perf_counter()
    res = MODEL.predict(img, imgsz=YOLO_IMGSZ, conf=YOLO_CONF, verbose=False)[0]
    ms = (time.perf_counter() - t0) * 1000
    dets = [{"label": res.names[int(b.cls)], "conf": round(float(b.conf), 3), "bbox_xyxy": [round(v) for v in b.xyxy[0].tolist()]}
            for b in res.boxes]
    return dets, ms


def camera_motion(cam: "CamState", img: Image.Image):
    """Global frame-to-frame shift by phase correlation (how the camera moved between this frame and the last one).
    Added 2026-09-25 (Shresth) so the command twin can move its drone the way the footage moves; ~1 ms per frame.
    dx/dy are pixels at a 320 px reference width; the scene shifting left means the camera moved right."""
    w = 320; h = max(16, int(w * img.height / max(1, img.width)))
    g = cv2.cvtColor(np.asarray(img.resize((w, h))), cv2.COLOR_RGB2GRAY).astype(np.float32)
    prev, cam.prev_gray = cam.prev_gray, g
    if prev is None or prev.shape != g.shape: return None
    (dx, dy), resp = cv2.phaseCorrelate(prev, g, cv2.createHanningWindow((w, h), cv2.CV_32F))
    return {"dx": round(float(dx), 2), "dy": round(float(dy), 2), "response": round(float(resp), 3), "ref_width": w}


# ---- gate -------------------------------------------------------------------
def hazard_signals(dets: list, scene_multi: list) -> dict:
    """Hazard kind -> confidence for this frame. Context objects (cars, people...) never appear here."""
    sig = {k: c for k, c in scene_multi if c >= GATE_SCENE_CONF}
    for d in dets:
        k = HAZARD_OBJECTS.get(d["label"])
        if k and d["conf"] >= GATE_OBJ_CONF:
            sig[k] = max(sig.get(k, 0.0), d["conf"])
    return sig


def gate(cam: CamState, sig: dict, scene: str, scene_conf: float, ts: str, force: bool):
    """Update the camera's hazard memory with this frame. Returns (decision, trigger_kinds, cleared_kinds)."""
    now = time.monotonic()
    for k, c in sig.items():
        cam.streak[k] = cam.streak.get(k, 0) + 1
        cam.best[k] = max(cam.best.get(k, 0.0), c)
        cam.miss.pop(k, None)
        if k in cam.active: cam.active[k]["conf"] = max(cam.active[k]["conf"], c)
    for k in [k for k in cam.streak if k not in sig]:
        cam.streak.pop(k); cam.best.pop(k, None)
    cleared = []
    for k in [k for k in cam.active if k not in sig]:
        cam.miss[k] = cam.miss.get(k, 0) + 1
        if cam.miss[k] >= GATE_CLEAR:
            cam.active.pop(k); cam.miss.pop(k); cleared.append(k)
    cam.normal = cam.normal + 1 if (scene == "normal" and scene_conf >= OPEN_CONF and not sig) else 0

    new = [k for k, n in cam.streak.items() if n >= GATE_PERSIST and k not in cam.active]
    for k in new:
        cam.active[k] = {"since": ts, "assessed": now, "conf": cam.best[k]}
    due = [k for k in cam.active if k in sig and k not in new and now - cam.active[k]["assessed"] >= REASSESS_SEC]
    for k in due: cam.active[k]["assessed"] = now

    if force:
        kinds = {k: a["conf"] for k, a in cam.active.items()} or dict(sig)
        return "trigger:forced", kinds, cleared
    if new:
        return "trigger:new:" + ",".join(sorted(new)), {k: cam.active[k]["conf"] for k in new + due}, cleared
    if due:
        return "trigger:reassess:" + ",".join(sorted(due)), {k: cam.active[k]["conf"] for k in due}, cleared
    if not sig:
        return "no_hazard", {}, cleared
    if all(k in cam.active for k in sig):
        return "already_assessed", {}, cleared
    return "persisting", {}, cleared


# ---- tier 2: Qwen3-VL via ZRT -------------------------------------------------
# Compact schema (short keys, no whitespace) so the VLM spends ~40 output tokens; expanded in the event.
ASSESS_SCHEMA = {
    "type": "object",
    "properties": {
        "d":   {"type": "string", "maxLength": 160},
        "h":   {"type": "array", "items": {"type": "object", "properties": {
                   "t": {"type": "string"}, "s": {"type": "integer", "minimum": 1, "maximum": 5},
                   "c": {"type": "number", "minimum": 0, "maximum": 1}}, "required": ["t", "s", "c"]}},
        "p":   {"type": ["boolean", "null"]},
        "ppl": {"type": "boolean"},
    },
    "required": ["d", "h", "p", "ppl"],
}
VLM_SYSTEM = ("You are the vision analyst for a county EOC watch officer. Describe only what is visible, in EOC vocabulary. "
              "Reply with ONE line of compact JSON, no spaces or newlines, exactly these keys: "
              '{"d":"<one sentence, max 25 words>","h":[{"t":"<hazard>","s":<severity 1-5>,"c":<confidence 0-1>}],'
              '"p":<true|false|null road passable>,"ppl":<true|false people visible>}. h is [] if no real hazard.')


def expand(a: dict) -> dict:
    return {"description": a["d"], "hazards": [{"type": h["t"], "severity": h["s"], "confidence": h["c"]} for h in a["h"]],
            "road_passable": a["p"], "people_visible": a["ppl"]}


def to_data_uri(jpeg: bytes) -> str:
    im = Image.open(io.BytesIO(jpeg)).convert("RGB"); im.thumbnail((VLM_MAX_SIDE, VLM_MAX_SIDE))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def pick(frames: list, k: int) -> list:
    if len(frames) <= k: return list(frames)
    return [frames[round(i * (len(frames) - 1) / (k - 1))] for i in range(k)] if k > 1 else [frames[-1]]


def save_evidence(source_id: str, frames: list) -> list:
    refs = []
    for i, f in enumerate(frames):
        name = f"{source_id}_{int(time.time() * 1000)}_{i}.jpg"
        with open(os.path.join(FRAME_DIR, name), "wb") as fo: fo.write(f["jpeg"])
        refs.append(EVIDENCE_PREFIX + name)
    return refs


def submit(job: dict):
    """One waiting job per camera. A trigger that arrives while one is waiting merges into it (union of hazards,
    newest frames, earliest trigger time) instead of queueing a second Qwen call or being dropped."""
    with COND:
        old = PENDING.get(job["source"]["id"])
        if old:
            STATS["vlm_merged"] += 1
            job["kinds"] = {**old["kinds"], **job["kinds"]}; job["confirmed"] = old["confirmed"] | job["confirmed"]
            job["ts"], job["t_trigger"] = old["ts"], old["t_trigger"]
            job["reason"] = old["reason"] + " + " + job["reason"]
        PENDING[job["source"]["id"]] = job
        COND.notify()


def vlm_worker():
    while True:
        with COND:
            while not PENDING: COND.wait()
            _, job = PENDING.popitem(last=False)
        try: tier2(job)
        except Exception as e: log.exception("tier2 crashed: %s", e)


def tier2(job: dict):
    source, kinds = job["source"], job["kinds"]
    frames = pick(job["frames"], VLM_FRAMES)
    refs = save_evidence(source["id"], frames)
    span = frames[-1]["mono"] - frames[0]["mono"] if len(frames) > 1 else 0.0
    hint = (", ".join(f"{k} {c:.2f}" for k, c in sorted(kinds.items(), key=lambda x: -x[1])) +
            f" (held {GATE_PERSIST}+ frames); context objects: " + (", ".join(sorted(job["context"])) or "none"))
    what = f"{len(frames)} frames in time order from the last {span:.0f} s" if len(frames) > 1 else "one frame"
    content = [{"type": "image_url", "image_url": {"url": to_data_uri(f["jpeg"])}} for f in frames]
    content.append({"type": "text", "text": f"Source: {source['type']} {source['id']}. Input: {what}. Detector confirmed: {hint}. Assess the scene."})
    body = {"model": VLM_MODEL, "temperature": 0, "max_tokens": VLM_MAX_TOKENS,
            "messages": [{"role": "system", "content": VLM_SYSTEM}, {"role": "user", "content": content}],
            "structured_outputs": {"json": ASSESS_SCHEMA, "disable_any_whitespace": True}}
    t0 = time.perf_counter()
    try:
        r = httpx.post(f"{ZRT_URL}/chat/completions", json=body, timeout=120)
        r.raise_for_status()
        assessment, err = expand(json.loads(r.json()["choices"][0]["message"]["content"])), None
    except Exception as e:  # the detector's confirmed hazard still becomes a claim below, at lower confidence
        assessment, err = None, str(e)[:300]
        STATS["vlm_errors"] += 1
    ms = (time.perf_counter() - t0) * 1000
    STATS["tier2_ms"].append(ms); STATS["vlm_calls"] += 1
    ev = {"event_id": str(uuid.uuid4()), "parent_event_id": job["event_id"], "ts": now_iso(), "source": source, "tier": 2,
          "model": f"{VLM_MODEL} (Qwen3-VL via ZRT)", "latency_ms": round(ms), "frame_ref": refs[-1], "frame_refs": refs,
          "trigger": job["reason"], "hazards_confirmed": kinds, "assessment": assessment, "error": err}
    emit(ev)
    log.info("tier2 %s %s %.0fms %s", source["id"], "ok" if assessment else "ERR", ms, (assessment or {}).get("description", err)[:90])
    for claim in make_claims(job, assessment, err, refs, ev["event_id"]):
        POST_POOL.submit(post_claim, claim, job["t_trigger"])


def make_claims(job: dict, a: Optional[dict], err: Optional[str], refs: list, vision_event_id: str) -> list:
    """Detector hazard kind + Qwen's road_passable -> contract events, one per (entity, claim)."""
    out = {}
    vlm_c = max((h["confidence"] for h in a["hazards"]), default=None) if a else None
    for kind, det_c in job["kinds"].items():
        rule = CLAIM_RULES.get(kind)
        if not rule or kind not in job["confirmed"]: continue
        etype, claim = rule
        entity = job["entities"].get(etype)
        if not entity:
            STATS["claims_unmapped"] += 1
            log.warning("no %s entity mapped for camera %s; %s kept local", etype, job["source"]["id"], kind)
            continue
        if etype == "road" and a and a["road_passable"] is True: claim = "restricted"
        # Qwen agrees -> the weaker of the two; Qwen saw no hazard -> halve; Qwen unavailable -> detector alone, discounted
        conf = min(det_c, vlm_c) if vlm_c is not None else det_c * (0.5 if a else 0.8)
        key = (entity, claim)
        if key in out:
            out[key]["confidence"] = max(out[key]["confidence"], round(conf, 3)); out[key]["details"]["hazard"].append(kind)
            continue
        out[key] = contract_event(job, entity, etype, claim, round(conf, 3), refs, {
            "hazard": [kind], "detector_conf": round(det_c, 3), "vlm": a, "vlm_error": err, "vision_event_id": vision_event_id,
            "people_visible_count": job.get("people", 0), "injured_visible_count": job.get("injured", 0),
            "trigger": job["reason"]})
    return list(out.values())


def contract_event(job: dict, entity: str, etype: str, claim: str, conf: float, refs: list, extra: dict) -> dict:
    src = job["source"]
    eid = "vis-" + hashlib.sha1(f"{src['id']}|{entity}|{claim}|{job['ts']}".encode()).hexdigest()[:16]
    return {"source": "drone_vision", "timestamp": z(job["ts"]), "confidence": conf, "entity": entity, "claim": claim,
            "raw_evidence_ref": refs[-1], "event_id": eid,
            "details": {"camera": src["id"], "camera_type": src["type"], "entity_type": etype, "evidence_frames": refs,
                        "raw_bytes": job["raw_bytes"], **extra}}


def post_claim(ev: dict, t_trigger: float):
    reply = {"action": "dry_run"}
    if BUS_URL:
        try:
            r = httpx.post(f"{BUS_URL}/events", json=ev, timeout=30); r.raise_for_status(); reply = r.json()
        except Exception as e:
            STATS["bus_errors"] += 1; reply = {"action": "bus_error", "error": str(e)[:300]}
    STATS["claims"] += 1; STATS["trigger_to_claim_ms"].append((time.perf_counter() - t_trigger) * 1000)
    with LOCK: CLAIMS.append({"event": ev, "bus": reply, "posted_at": now_iso()})
    log.info("claim %s %s %s conf %.2f -> %s", ev["details"]["camera"], ev["entity"], ev["claim"], ev["confidence"], reply.get("action"))


def emit(ev: dict):
    with LOCK: EVENTS.append(ev)


# ---- API --------------------------------------------------------------------
@app.get("/health")
def health(): return {"status": "ok", "version": 2, "tier1": ["yolo-world", f"clip-{SCENE_MODEL}"], "tier2": VLM_MODEL,
                      "hazard_objects": list(HAZARD_OBJECTS), "context_objects": CONTEXT_OBJECTS, "scene_classes": list(SCENES),
                      "bus": BUS_URL or "dry_run", "cameras": CAMERA_MAP}


@app.post("/v1/vision/frame")
async def frame(file: UploadFile = File(...), source_id: str = Form("drone-1"), source_type: str = Form("drone"),
                ts: Optional[str] = Form(None), frame_ref: Optional[str] = Form(None), force_vlm: bool = Form(False),
                entities: Optional[str] = Form(None)):
    raw = await file.read()
    try: img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception: raise HTTPException(400, "file is not an image")
    try: override = json.loads(entities) if entities else {}
    except ValueError: raise HTTPException(400, 'entities must be JSON, e.g. {"building": "Building-14", "road": "Main Street"}')
    ts = ts or now_iso()
    dets, ms_det = tier1(img)
    scene, scene_conf, scene_top3, ms_scene, scene_multi = scene_head(img)
    ms = ms_det + ms_scene
    STATS["frames"] += 1; STATS["tier1_ms"].append(ms)

    cam = CAMS.setdefault(source_id, CamState())
    event_id = str(uuid.uuid4()); mono = time.monotonic()
    if cam.buf and mono - cam.buf[-1]["mono"] > BUFFER_SEC:
        # the feed paused (or a caller sends isolated stills): frames minutes apart are not "in a row"
        cam.buf.clear(); cam.streak.clear(); cam.best.clear(); cam.normal = 0
    cam.buf.append({"mono": mono, "ts": ts, "jpeg": raw, "event_id": event_id})
    while cam.buf and mono - cam.buf[0]["mono"] > BUFFER_SEC: cam.buf.popleft()

    motion = camera_motion(cam, img)
    sig = hazard_signals(dets, scene_multi)
    if sig: STATS["frames_with_hazard"] += 1
    decision, kinds, cleared = gate(cam, sig, scene, scene_conf, ts, force_vlm)
    ents = {**CAMERA_MAP.get(source_id, {}), **override}
    source = {"type": source_type, "id": source_id}

    ev = {"event_id": event_id, "ts": ts, "source": source, "tier": 1,
          "model": f"yolov8m-worldv2 + clip-{SCENE_MODEL}", "latency_ms": round(ms, 1),
          "latency_breakdown_ms": {"detector": round(ms_det, 1), "scene": round(ms_scene, 1)},
          "frame_ref": frame_ref, "detections": dets, "motion": motion, "image": {"w": img.width, "h": img.height},
          "scene": {"label": scene, "conf": scene_conf, "labels": scene_multi, "top3": scene_top3},
          "gate": {"decision": decision, "hazards": sig, "active": sorted(cam.active), "cleared": cleared},
          "keyframe_reason": decision if decision.startswith("trigger") else None}   # v1 field, kept for existing callers
    emit(ev)

    if decision.startswith("trigger"):
        STATS["gate_triggers"] += 1
        # Only gate-confirmed hazards become claims. force_vlm on an unconfirmed frame (e.g. the scenario runner's
        # single stills, which post their own scripted claim) gets a Qwen assessment but never a bus event.
        submit({"source": source, "event_id": event_id, "ts": ts, "t_trigger": time.perf_counter(), "reason": decision,
                "kinds": kinds, "confirmed": set(kinds) & set(cam.active), "frames": list(cam.buf), "entities": ents, "raw_bytes": sum(len(f["jpeg"]) for f in cam.buf),
                "context": {d["label"] for d in dets if d["label"] in CONTEXT_OBJECTS},
                "people": sum(1 for d in dets if d["label"] in PEOPLE_LABELS), "injured": sum(1 for d in dets if d["label"] == "injured person")})
    elif cam.normal >= GATE_PERSIST and not cam.active and ents.get("report_open") and ents.get("road") \
            and mono - cam.open_at >= REASSESS_SEC:
        # A road camera watching normal traffic reports the road `open` from tier 1 alone: no Qwen call needed.
        cam.open_at = mono
        job = {"source": source, "ts": ts, "raw_bytes": sum(len(f["jpeg"]) for f in cam.buf)}
        refs = save_evidence(source_id, [cam.buf[-1]])
        claim = contract_event(job, ents["road"], "road", "open", round(scene_conf, 3), refs,
                               {"hazard": [], "detector_conf": scene_conf, "vlm": None, "vlm_error": None,
                                "vision_event_id": event_id, "trigger": f"normal x{cam.normal}"})
        POST_POOL.submit(post_claim, claim, time.perf_counter())
        ev["gate"]["decision"] = "report_open"
    return ev


@app.get("/v1/vision/events")
def events(since: Optional[str] = None, limit: int = 200, tier: Optional[int] = None, source_id: Optional[str] = None):
    with LOCK: evs = list(EVENTS)
    if since: evs = [e for e in evs if e["ts"] > since]
    if tier: evs = [e for e in evs if e["tier"] == tier]
    if source_id: evs = [e for e in evs if e["source"]["id"] == source_id]
    return {"events": evs[-limit:]}


@app.get("/v1/vision/claims")
def claims(limit: int = 200, source_id: Optional[str] = None):
    with LOCK: cs = list(CLAIMS)
    if source_id: cs = [c for c in cs if c["event"]["details"]["camera"] == source_id]
    return {"claims": cs[-limit:]}


@app.get("/v1/vision/frames/{name}")
def evidence_frame(name: str):
    path = os.path.join(FRAME_DIR, os.path.basename(name))
    if not os.path.isfile(path): raise HTTPException(404, "no such frame")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/v1/vision/latest/{source_id}")
def latest_frame(source_id: str):
    """The newest frame this camera sent (from its in-memory buffer): lets the command twin show what tier 1 is looking at.
    Added 2026-09-25 (Shresth) for the live frontend; read-only, no effect on the gate."""
    cam = CAMS.get(source_id)
    if not cam or not cam.buf: raise HTTPException(404, "no frames from this camera yet")
    f = cam.buf[-1]
    return Response(f["jpeg"], media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Frame-Ts": str(f["ts"]), "X-Frame-Event": f["event_id"]})


@app.post("/v1/vision/reset")
def reset(source_id: Optional[str] = Form(None)):
    """Forget a camera's hazard memory (or all cameras'), e.g. between rehearsals. Also reloads camera_entities.json."""
    if source_id: CAMS.pop(source_id, None)
    else: CAMS.clear()
    load_camera_map()
    return {"reset": source_id or "all", "cameras": CAMERA_MAP}


@app.get("/v1/vision/stats")
def stats():
    def summ(d):
        if not d: return None
        s = sorted(d); return {"n": len(s), "p50_ms": round(s[len(s)//2], 1), "p95_ms": round(s[int(len(s)*0.95)], 1), "mean_ms": round(sum(s)/len(s), 1)}
    f, calls = STATS["frames"], STATS["vlm_calls"]
    return {"frames": f, "frames_with_hazard": STATS["frames_with_hazard"], "gate_triggers": STATS["gate_triggers"],
            "vlm_calls": calls, "vlm_merged": STATS["vlm_merged"], "vlm_errors": STATS["vlm_errors"],
            "vlm_calls_per_frame": round(calls / f, 3) if f else None,
            "claims": STATS["claims"], "claims_unmapped": STATS["claims_unmapped"], "bus_errors": STATS["bus_errors"],
            "tier1": summ(STATS["tier1_ms"]), "tier2": summ(STATS["tier2_ms"]), "trigger_to_claim": summ(STATS["trigger_to_claim_ms"]),
            "active_hazards": {sid: sorted(c.active) for sid, c in CAMS.items() if c.active}}
