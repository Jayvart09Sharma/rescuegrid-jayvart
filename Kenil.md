# RescueGrid — Kenil's brief
Role: Reasoning layer — fusion, graph writes, and Q&A
See `00-master-plan.md` for the full architecture and ground rules first.

## Your mission
You're the brain in the middle. Everyone else's adapters produce raw perceptions (a detected
building collapse, a transcribed radio line, a sensor spike) — you turn those into the graph
writes that make RescueGrid a coherent system instead of four separate feeds, and you build the
layer that lets the watch officer actually talk to it.

## What you own

### 1. The event/correlation fusion agent
Takes raw events from Aditya (vision), Pranay (ASR + radio), and Jayvant (GPS, sensors, field
reports) and turns them into graph writes:
- Every write needs a **timestamp** (when it became true) and **provenance** (source + confidence
  — carry through the frame/audio reference the source events already include).
- Correlate related events into relationships, not just isolated facts — e.g. "Rescue Team 4" +
  "near" + "Building 14" isn't two disconnected facts, it's a relationship the graph should
  represent as such, because that's what makes "who's at risk?" answerable later.
- Work closely with Shresth on the exact Neo4j schema — you're the main consumer of it.

### 2. Local LLM natural-language Q&A layer
Takes a plain-language question ("who's in the most danger?", "can Ambulance 2 still reach the
hospital?") and:
- Translates it into a graph query (Cypher) against Neo4j — **query the graph and recent event
  history directly, never have a model inspect the 3D rendering to "see" what's happening.**
  Neo4j is the single source of truth; keep it that way.
- Returns both an answer in plain language and a signal for what the 3D twin should highlight or
  fly the camera to. **Coordinate the exact signal shape with Pranay** — he owns the frontend now,
  so this is the main handoff point between your two pieces. Agree it early, not the night before.
- Keep a small set of pre-baked queries that are guaranteed to work as a fallback for the live
  demo, in case the general-purpose version is still flaky on Friday — this is explicitly a
  stretch-goal safety net, not optional insurance.

**Check with Shresth before finalizing which local LLM (and size/quantization) to run** — it's
sharing the Nano's memory budget with the vision and ASR models running at the same time.

### 3. Conflict-handling design — stretch goal, not a must-ship
This is a genuinely good idea and worth designing properly, but it's new logic on top of an
already tight week — don't let it block your must-ship work above.
- The case to handle: two sources disagree about the same fact (radio says a bridge is open,
  drone footage shows debris blocking it).
- Don't silently pick one. Surface both, flagged as **"conflicting reports,"** with both
  sources' provenance attached, and let the watch officer see the disagreement rather than have
  the system quietly resolve it for them.
- If you have time after the must-ship and should-ship layers are solid, build this. If not,
  document the design (even a paragraph in the README) so it's a credible "here's what's next"
  answer in Q&A rather than something that never got thought through.

## Using the Nano properly
- Serve your local LLM with `zrt serve`, check `zrt status`/`zrt models` first.
- Your Cypher-query-generation step and your fusion agent are probably the most compute-light of
  the four AI components — flag to Shresth if you find you have headroom, since it might free up
  budget for someone else's model choice.
- Don't kick off any heavy fine-tuning or serving job without posting in the group chat first.

## Definition of done
- [ ] LLM choice discussed and confirmed with Shresth
- [ ] Fusion agent correctly writes timestamped, provenance-tagged facts and relationships to
      Neo4j from all three upstream sources (vision, ASR, GPS/sensors/field reports)
- [ ] Natural-language Q&A correctly answers the demo's scripted questions via real graph queries
- [ ] Camera-fly-to signal shape agreed and working with Pranay's frontend
- [ ] A small set of pre-baked fallback queries exists and works reliably
- [ ] Conflict-handling either built, or clearly documented as designed-but-deferred
