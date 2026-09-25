# RescueGrid vision service (two-tier)

Drone and road-camera frames come in, structured hazard events with timestamp + provenance go out.
Inference is 100% local on the ZGX Nano. Inputs are replayed for the demo; the models never know.

```
frame ──► Tier 1 (every frame, ~30 ms) ──► tier-1 event ──► event bus / Neo4j
             ├─ YOLO-World  : boxes for person, car, truck, ambulance, boat, ...   (~9 ms)
             └─ CLIP ViT-L/14: scene class  collapsed_building | flooded_road |     (~7 ms)
                              wildfire_smoke | structure_fire | landslide | ...
                  │
                  ▼ keyframe? (scene changed, new object class, or every KEYFRAME_SEC)
          Tier 2 (Qwen3-VL-8B via ZRT proxy, ~2 s, async, one at a time)
             └─ one-sentence EOC description, hazards[type,severity,confidence],
                road_passable, people_visible  ──► tier-2 event (parent_event_id = tier-1 event)
```

Why two tiers: YOLO-World is fast but cannot see scene-level concepts (a flooded road is not an
"object"; it scores 0.00 even at a 5% threshold). CLIP zero-shot on the whole frame gets those right
in 7 ms. The VLM is the only thing that can write a sentence a watch officer would read, so it runs
only when something changed. Both tier-1 heads take a plain text class list: edit `HAZARD_CLASSES`
or `SCENES` in `server.py`, no retraining.

## Run

```
cd rescuegrid/vision
.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8091      # needs ZRT 'vision' model up for tier 2
.venv/bin/python replay.py samples/ --source-id drone-1 --fps 2 # replay adapter (folder of images or an .mp4)
.venv/bin/python bench.py 5                                     # latency benchmark on samples/
```

Setup (once): `python3 -m venv .venv && .venv/bin/pip install torch torchvision ultralytics fastapi "uvicorn[standard]" python-multipart pillow httpx git+https://github.com/ultralytics/CLIP.git`
and put `yolov8m-worldv2.pt` in `weights/` (ultralytics assets release v8.3.0). CLIP weights download on first start.

## API

| Method | Path | What |
|---|---|---|
| POST | `/v1/vision/frame` | multipart `file` + `source_id`, `source_type` (drone/roadcam), optional `ts`, `frame_ref`, `force_vlm`. Returns the tier-1 event immediately. |
| GET | `/v1/vision/events?since=<iso>&tier=1|2&limit=` | events, newest last |
| GET | `/v1/vision/stats` | p50/p95 latency per tier, VLM call counts |
| GET | `/health` | class lists |

Env: `ZRT_URL` (default `http://127.0.0.1:8080/v1`), `VLM_MODEL=vision`, `EVENT_SINK_URL` (webhook that receives every event as JSON, for Shresth's bus),
`KEYFRAME_SEC=10`, `SCENE_MODEL=ViT-L/14` (ViT-B/16 is 3x faster, weaker on flood), `SCENE_CONF=0.5`, `VLM_MAX_SIDE=640`, `FRAME_DIR` (keyframes saved for provenance).

## Event contract

```json
{"event_id": "...", "ts": "2026-09-24T04:30:01.123+00:00", "source": {"type": "drone", "id": "drone-1"},
 "tier": 1, "model": "yolov8m-worldv2 + clip-ViT-L/14", "latency_ms": 28.1,
 "latency_breakdown_ms": {"detector": 20.3, "scene": 7.8}, "frame_ref": "frames/drone-1_1790224201123.jpg",
 "detections": [{"label": "person", "conf": 0.89, "bbox_xyxy": [512, 300, 560, 410]}],
 "scene": {"label": "flooded_road", "conf": 0.98, "top3": [["flooded_road", 0.98], ["normal", 0.01], ["landslide", 0.0]]},
 "keyframe_reason": "scene:flooded_road"}
```
Tier-2 events have `"tier": 2`, `parent_event_id`, and
`"assessment": {"description": "...", "hazards": [{"type": "flooding", "severity": 4, "confidence": 1.0}], "road_passable": false, "people_visible": true}`.
If the VLM fails, the tier-2 event still arrives with `assessment: null` and `error`; the tier-1 event is never blocked.

## Measured on the ZGX Nano (GB10), 2026-09-24, with Nemotron 30B + Qwen3-VL-8B also loaded

| Stage | Latency |
|---|---|
| Tier 1 per frame (YOLO-World + CLIP-L, server side) | p50 28 ms, p95 48 ms |
| Tier 1 HTTP round trip incl. JPEG upload | ~50 ms |
| Tier 2 Qwen3-VL keyframe assessment | ~1.8 to 2.0 s (TTFT ~100 ms, 23.5 tok/s, ~42 output tokens) |
| Tier 2 with pretty-printed json_schema output (rejected) | 3.7 to 8.8 s, and it overran the token budget |

Scene head accuracy on the four CC samples: collapsed 1.00, flood 0.98, wildfire 1.00, normal traffic 0.88.
GPU memory for tier 1: ~2.5 GB (fits beside the 0.40 + 0.20 ZRT fractions).

## Update 2026-09-24 evening: scene classes tuned on the team's AI-generated drone clips

- Added `blocked_road` (cars stopped behind debris across the pavement). Clip 2 (Main Street) now reads 0.97 to 1.00; before it flip-flopped between normal and vehicle_crash.
- Tightened `collapsed_building` (fallen floors, rebar, slab against the wall) and `normal` (includes congestion with nothing blocking) so a traffic jam is not a blocked road.
- `scene.labels` now lists every non-normal scene above 30% (`SCENE_MULTI`). A collapse whose rubble also blocks the street carries both labels instead of a coin flip; the keyframe trigger fires on a change in that set.
- Clip 1 (Building 14): approach frames read blocked_road, close frames collapsed_building 0.83 to 0.92. Qwen3-VL wrote "Building collapse with emergency crews on scene and debris blocking roadway", severity 5, road not passable.
