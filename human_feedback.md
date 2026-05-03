<!-- Write your feedback or ideas here. The agent will read this on the next loop and then clear the file. -->

## You are entering the Creative Phase

Everything has changed. Read the manifesto again — it has been rewritten.

You are no longer fixing bugs. You are an author. Your task is to build the world
this game takes place in, from the ground up, with no rushing and no shortcuts.

**Start here:**
1. Read `lore/references/visual_seeds.md` — this contains a detailed analysis of
   two reference images the human chose as the world's visual anchors. Read it
   carefully. Let it ask you questions.
2. Use `search_web` to research anything the images make you curious about —
   tidal locking, ancient coastal civilisations, ruined cities reclaimed by
   life, the mythology of visible moons in daylight skies.
3. Begin writing. Start wherever instinct takes you — a creation myth, a landscape
   description, the name of the world, a single character who lives there.
   There is no wrong starting point.

The central mystery the human gave you: *"Everyone can see the moon. No one
remembers why it's so close."* Let that sentence be the first stone you place.

There is no deadline on this phase except the one you set yourself. When the
world feels genuinely real and deep — when you could tell a stranger about it
and they would want to visit — create `lore/PHASE_COMPLETE.md` and the technical
build will resume. Not before.

Use `chat_respond` to tell the human what you discover as you build the world.


You have been upgraded with vision. You can now see screenshots of the game.

Your new workflow for visual work:
1. Make a code change to BackgroundSystem.ts
2. Run `npx tsc --noEmit 2>&1 | grep BackgroundSystem` to check for type errors
3. Use `capture_screenshot` — the next iteration you will SEE the result
4. Analyse what you see against the Visual Art Targets (injected automatically)
5. Make the next code change. Repeat.

START NOW:
- First, use `capture_screenshot` to see the current state of the game
- Then read `specs/visuals/VisualPipeline.md` for the full visual spec
- Then begin iterating on BackgroundSystem.ts to improve the visuals

The Vite dev server is already running on port 5173 — do NOT start another one.
The game canvas is at http://localhost:5173

Visual priority order:
1. Fix the sky gradient to have smooth bands (no visible banding), rich purple-to-orange
2. Make mountain silhouettes filled with gradients (lighter ridge, darker base)
3. Add more spire/cliff geometry detail — each ridgeline needs 20+ control points
4. Add atmospheric depth: far layers shift toward blue, near layers more saturated
5. Polish foreground: rocks, ground details, layered parallax depth


The game is now rendering with a real atmosphere shader and procedural canyon
silhouettes. Screenshot visible in the journal. Here is what to build next.

### What's already done (don't touch)
- `src/client/core/systems/BackgroundSystem.ts` — **fully rewritten**. Loads
  sprite PNG layers from `/assets/biomes/{biome}/{scene}/layer_*.png`, falls
  back to seeded polygon archetypes (spires, ridgeline, rocks) when no PNGs
  exist. Atmosphere rendered via GLSL shader (PixiJS v8 `Filter.from` +
  `UniformGroup`). Do not change the rendering pipeline or the shader code.
- `specs/visuals/VisualPipeline.md` — **read this spec thoroughly**. It defines
  the 3-system visual architecture (shaders / sprite layers / polygon fallback),
  parallax layer speeds, biome palette format, shape grammar archetypes, and
  the asset folder naming convention your WorldGen script must output.
- `specs/systems/WorldGen.md` — **read this spec thoroughly**. It defines the
  full build-time world generation system you need to implement.

### Your next task: implement the WorldGen script modules

Location: `scripts/worldgen/` (scaffold already there in `generate.js`)

Build these four modules **in this exact order**:

**1. `scripts/worldgen/world-layout.js`**
- Input: `seed`, `numScenes`
- Output: array of scene objects `{ id, biome, connections, position }`
- Algorithm: place N biome seeds on a grid → assign each scene to nearest seed
  (simple distance, no need for real Voronoi) → build connection graph (scenes
  within 2 grid units connect)
- Each scene gets a deterministic integer seed derived from `seed + sceneIndex`
- Export: `generateWorldLayout(seed, numScenes)`

**2. `scripts/worldgen/scene-composer.js`**
- Input: scene object from world-layout, biome definition from `biomes/`
- Output: array of layer descriptors `{ index, name, type, polygonData, sdPrompt }`
- For each of the 5 parallax layers: decide if it's polygon-only or SD-generated
  - Layers 0-1 (far background): SD-generated
  - Layers 2-4 (midground/foreground): polygon archetype from biome shape grammar
- For SD layers: fill `sdPrompt` using the biome's `SD_PROMPTS[layerName]()` template
- For polygon layers: fill `polygonData` with archetype + variance seeded from scene seed
- Export: `composeScene(scene, biomeDef, options)`

**3. `scripts/worldgen/asset-saver.js`**
- Input: layer descriptor array + output root path
- Creates directory: `src/client/assets/biomes/{biome}/{scene_id}/`
- Writes `layer_{index}_{name}.json` for polygon layers (the polygon data)
- Creates placeholder PNG stubs for SD layers (1x1 transparent PNG)
- Writes/merges `world-manifest.json` at `src/client/assets/`
- Export: `saveLayers(sceneId, biomeId, layers, outputRoot)`

**4. `scripts/worldgen/sd-client.js`** (stub is fine for now)
- Export: `generateImage(prompt, negativePrompt, seed, outputPath)`
- For now: just log the prompt and write a 1x1 placeholder PNG
- Real SD integration comes later; the stub lets the full pipeline run end-to-end

### How to test it

After implementing all four:
```
node scripts/worldgen/generate.js --seed 42 --scenes 6 --manifest-only
```
Should print scene layout + layer descriptors without calling SD.

```
node scripts/worldgen/generate.js --seed 42 --scenes 6
```
Should write polygon JSON files + placeholder PNGs to `src/client/assets/biomes/`.

### Biome definitions to copy the pattern from
`scripts/worldgen/biomes/desert_canyon.js` is complete. Once worldgen works for
desert_canyon, duplicate it and create `forest_ruins.js` and `ice_wastes.js`
following the same schema (`PALETTE`, `SHAPE_GRAMMAR`, `SD_PROMPTS`).

### What NOT to do
- Do not modify `BackgroundSystem.ts` or any rendering code
- Do not add runtime procedural generation — world gen is build-time only
- Do not implement real SD/ComfyUI integration yet — stub is correct
- Do not create new TypeScript files — this is all plain Node.js scripts

After worldgen runs successfully for 6 scenes, run the tests and commit with:
`git commit -m 'feat(worldgen): build-time world layout + scene composer'`

