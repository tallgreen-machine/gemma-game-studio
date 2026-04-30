# Aetheria: Project Design Specification

## 1. Vision
'Aetheria' is a massive, open-world RPG designed to evoke the scale of *Daggerfall* and the cinematic, minimalist polygonal aesthetic of *Another World*. It is an online experience where the world is persistent, but the intelligence is local.

## 2. Core Pillars
- **Massive Scale**: A procedurally generated or vast hand-crafted world with deep exploration.
- **Retro-Cinematic Aesthetic**: Low-poly geometry, high-contrast lighting, and cinematic framing. Use PixiJS to simulate a pseudo-3D or high-fidelity 2.5D environment that feels like 90s cinematic adventures.
- **Infinite Scalability (Local AI)**: NPCs are powered by local WebGPU-based LLMs (Gemma 2B via WebLLM). Dialogue is generated on the client, eliminating server costs and latency for NPC interactions.
- **Ethical Monetization**: Design for cosmetics and expansion passes. No pay-to-win.

## 3. Technical Architecture

### Frontend (The Client)
- **Engine**: PixiJS (v8) for rendering.
- **Language**: TypeScript.
- **Bundler**: Vite.
- **AI Engine**: WebLLM / Transformers.js for client-side inference.
- **State Management**: Local game state synchronized via WebSockets with the backend.

### Backend (The World State)
- **Environment**: Node.js.
- **Communication**: WebSockets (socket.io or ws) for real-time state synchronization.
- **Persistence**: Lightweight database for user accounts, inventory, and world-state changes.

### NPC Intelligence Loop
1. **Trigger**: Player interacts with NPC.
2. **Context**: Client gathers local environment data + player history + NPC persona.
3. **Inference**: Context is fed into the local quantized LLM (WebGPU).
4. **Output**: Dialogue is rendered on screen; actions are triggered in the game world.

## 4. Visual Style Guide
- **Polygon Art**: Flat shading, minimal textures, stark silhouettes.
- **Color Palette**: Cinematic, mood-driven palettes (e.g., deep purples, industrial greys, neon accents).
- **Camera**: Fixed or semi-fixed cinematic angles to enhance the 'Another World' feel.

## 5. Development Roadmap
- **Phase 1**: Foundation (Project setup, Basic Rendering Engine, State Management).
- **Phase 2**: World Logic (Movement, Collision, Basic Map Generation).
- **Phase 3**: Local Intelligence (WebLLM integration, NPC Dialogue system).
- **Phase 4**: Networking (Multiplayer synchronization, Backend persistence).
- **Phase 5**: Content & Polish (World building, Visual effects, Sound).
