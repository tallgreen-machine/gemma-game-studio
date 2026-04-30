# Game Design Document: Aetheria

## 1. Vision Statement
'Aetheria' is a massive, cinematic open-world RPG that blends the atmospheric, minimalist storytelling of *Another World* with the expansive, systemic depth of *Daggerfall*. The game emphasizes atmospheric exploration, emergent dialogue via local-LLM NPCs, and a hauntingly beautiful retro-polygon aesthetic.

## 2. Aesthetic & Art Direction
- **Visual Style**: 'Retro-Cinematic Polygon'.
  - Low-polygon counts, flat shading, and a limited, moody color palette.
  - Cinematic camera angles: Fixed or semi-fixed perspectives that emphasize the scale of the environment over the player character.
  - Heavy use of silhouettes, atmospheric fog, and dramatic lighting to create a sense of dread and wonder.
- **Audio**: Ambient, drone-heavy soundscapes with minimalist melodic motifs.

## 3. World & Scale
- **Scope**: An expansive world consisting of sprawling wildernesses, decaying megalopolises, and subterranean ruins.
- **World Generation**: A hybrid approach. Major hubs are handcrafted; the wilderness between them is procedurally generated using deterministic seeds to ensure consistency across clients in the MMO environment.
- **Atmosphere**: Desolate, enigmatic, and ancient. The world feels like it is in the twilight of its existence.

## 4. Core Gameplay Mechanics
- **Exploration**: Non-linear exploration of a massive map. Discovering landmarks and lore fragments.
- **Interaction**: A deep interaction system where players can converse with any NPC using natural language.
- **Progression**: Skill-based progression (similar to *Daggerfall*). Players improve by doing.
- **Combat**: Tactical, high-stakes combat where positioning and environment matter more than stat-checking.

## 5. The AI Integration (The Soul of Aetheria)
- **Local LLM**: Every NPC is powered by a quantized micro-LLM (Gemma 2B) running directly in the browser via WebGPU.
- **Emergent Narrative**: NPCs are given 'World Knowledge' and 'Personal Motivations' as system prompts. Dialogue is not scripted but emerges from the interaction between player input and NPC personality.
- **Persistence**: Key NPC state changes (e.g., a player convincing an NPC to betray their lord) are synced to the backend to ensure world-state consistency.

## 6. Technical Goals
- **Zero-Cost Scale**: No server-side LLM calls.
- **Performance**: 60fps target using PixiJS and optimized WebGPU shaders.
- **Connectivity**: WebSocket-based state synchronization for a shared world experience.
