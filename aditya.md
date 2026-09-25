# RescueGrid — Aditya's brief
Role: Vision pipeline
See `00-master-plan.md` for the full architecture and ground rules first.

## Your mission
Turn drone and road-camera footage into structured facts about the disaster — "Building 14
collapsed," "Main Street blocked" — running live and local on the Nano.

## What you own

### 1. The vision model itself
Detects/classifies damage and hazards from video frames: collapsed structures, blocked roads,
visible hazards. Runs via `zrt serve` on the Nano.

**Before you pick a specific model or size, check with Shresth.** He's tracking total memory
footprint across everyone's models running on the Nano at once — don't finalize a model choice
in isolation. Bring him: expected model size, quantization, and roughly how much VRAM/compute
you expect it to need, before you commit.

### 2. Drone video replay adapter
- Source stock/creative-commons disaster drone footage (collapsed buildings, blocked roads —
  match what the scenario timeline needs).
- Build a `replay` adapter that plays frames on a timer into the exact same interface the `live`
  adapter would use (RTSP stream or a timestamped frame folder — agree the exact shape with
  Shresth so it matches his event-bus contract).
- Your code shouldn't know or care whether it's reading a real camera or a folder of frames —
  that's the whole point of the adapter pattern.

### 3. Road-camera replay adapter
Same pattern, reusing your vision model's ingestion endpoint (per the data-sources table in the
master brief, road cameras and drones share the same vision pipeline — don't build a second one).

### 4. Structured event output
Every detection your model makes gets turned into an event matching Shresth's contract — source,
timestamp, confidence, entity, claim, and a reference back to the actual frame that produced it
(this reference is what makes the provenance click-through work later, don't skip it).

## Using the Nano properly
- Connect via ZTK, serve your model with `zrt serve <model> --host 0.0.0.0 --port <port>`.
- Check `zrt status` / `zrt models` before assuming a port or model slot is free — others are
  sharing this device.
- Don't run heavy training/serving jobs without posting in the group chat first — see ground
  rule #2 in the master brief.
- If you hit errors like FlashInfer, that's a known rough edge — ask the team or use cloud AI to
  help debug rather than losing hours to it solo.

## Definition of done
- [ ] Model choice discussed and confirmed with Shresth (footprint-aware)
- [ ] Vision model correctly detects the scenario's key events (Building 14 collapse, Main
      Street blockage) from your sourced footage
- [ ] Drone replay adapter working, matches the live-adapter interface
- [ ] Road-camera replay adapter reusing the same vision endpoint
- [ ] Every detection emits a correctly-shaped event with a frame reference for provenance
