# Gemma Game Studio — Architecture v0.2

> Working model for the autonomous AI game development system.
> Updated when structure, tooling, or conventions change.

---

## 1. Repository Layout

```
gemma_game_dev/
├── supervisor.py          # Orchestration loop (runs locally on Mac Studio)
├── manifesto.md           # Gemma's prime directive — creative & technical north star
├── human_feedback.md      # Drop a message here; Gemma reads it next iteration
├── studio_config.json     # { active_game: "aetheria", games: { slug: info } }
├── start_supervisor.sh    # Bulletproof start script (kills stale, verifies PID)
├── start_comfyui.sh       # Start ComfyUI for image generation
│
├── games/                 # All game workspaces (gitignored — local only)
│   └── aetheria/          # Current active game
│       ├── index.html     # Game entry point (Vite root)
│       ├── package.json   # phaser, @xenova/transformers, fastify, socket.io
│       ├── tsconfig.json  # strict: false (Phaser-friendly)
│       ├── vite.config.ts # root: '.', publicDir: 'public'
│       ├── src/
│       │   ├── main.ts              # Phaser.Game config + scene list
│       │   └── scenes/
│       │       ├── BootScene.ts     # Config → PreloadScene
│       │       ├── PreloadScene.ts  # Asset loading → GameScene
│       │       ├── GameScene.ts     # Main gameplay hub
│       │       └── Zone*.ts         # One file per world zone
│       ├── public/
│       │   └── assets/
│       │       ├── img/             # Sprites, backgrounds, UI
│       │       ├── maps/            # Tiled JSON tilemaps
│       │       └── audio/           # Music, SFX
│       ├── data/
│       │   └── souls/               # NPC soul files (JSON, one per NPC)
│       ├── lore/                    # World-building (75+ concept images, lore docs)
│       ├── specs/                   # Design specs (GDD, system specs)
│       └── agent/                   # Gemma's working memory
│           ├── brief.md             # Creative north star
│           ├── manifest.json        # Current mode, phase, task pointer
│           ├── task_queue.md        # Ordered task list
│           ├── journal.md           # Decision log
│           └── plan.md              # Current PLAN turn output
│
├── dashboard/
│   ├── server.py          # Aiohttp dashboard API (deployed to droplet)
│   └── index.html         # SPA frontend (login → game manager → studio)
│
└── chroma_db/             # Vector store for code search (local)
```

---

## 2. Infrastructure

### Local — Mac Studio

| Service | Command | Notes |
|---------|---------|-------|
| Supervisor | `./start_supervisor.sh` | Always use the script — handles PID lock |
| ComfyUI | `./start_comfyui.sh` | FLUX image generation for concept art |
| Vite dev server | `cd games/aetheria && npm run dev:client` | Port 3000, HMR |

**Active game resolution**: `supervisor.py` reads `studio_config.json` on startup to determine `ACTIVE_GAME`, then sets `WORKSPACE_DIR = games/{active_game}/`.

### Remote — DigitalOcean Droplet

| Detail | Value |
|--------|-------|
| IP | `165.227.27.71` |
| SSH alias | `epiphany` |
| Dashboard URL | `http://165.227.27.71:8080` |
| API key | `epiphany_secret_2026` |

**Deploy command:**
```bash
scp dashboard/server.py dashboard/index.html epiphany:/root/dashboard/
ssh epiphany "fuser -k 8080/tcp; sleep 1; cd /root/dashboard && source venv/bin/activate && nohup python server.py > server.log 2>&1 &"
```

---

## 3. Supervisor State Machine

```
BOOTSTRAP ──► CREATIVE ──► ARCHITECT ──► BUILD ──⇄── REPAIR
                                            │
                                            ▼
                                        PLAYTEST ──► BUILD (new tasks)
```

| Mode | What Gemma does | Exits when |
|------|----------------|-----------|
| BOOTSTRAP | Interactive intake (brief.md + manifest.json) | manifest saved |
| CREATIVE | World-building, lore, concept art | Gemma creates `lore/PHASE_COMPLETE.md` |
| ARCHITECT | Designs full task_queue.md from brief | task_queue written |
| BUILD | PLAN turn → EXECUTE turn per task | task marked complete |
| REPAIR | SAR: deterministic triage → LLM targeted rewrite | `tsc --noEmit` clean |
| PLAYTEST | Screenshot review → new tasks | tasks added → BUILD |

**SAR (Structured Autonomous Repair):**
1. Run `npx tsc --noEmit` → parse errors by file
2. Pick worst file (most errors)
3. Assemble task card: file content + errors + related context
4. Send to Gemma for targeted rewrite
5. Write response, verify, repeat
6. After `MAX_RETRIES=5` on same file → mark stuck, skip, notify

---

## 4. Dashboard API Surface

All supervisor calls use `X-API-KEY: epiphany_secret_2026`.
Browser calls use JWT cookie (httpOnly, 30-day TTL).

### Auth
| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/auth/check` | GET | None | Returns `{configured, authenticated}` |
| `/auth/setup` | POST | None | First-time password setup |
| `/auth/login` | POST | None | Login → sets JWT cookie |
| `/auth/logout` | POST | Cookie | Clears cookie |

### Game Manager
| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/games` | GET | Cookie/Key | List all games |
| `/api/games` | POST | Cookie | Create new game (from wizard) |
| `/api/games/switch` | POST | Cookie/Key | Set active game |
| `/api/studio/config` | POST | Key | Supervisor pushes config on startup |
| `/api/studio/pending` | GET | Key | Supervisor polls for switch/new_game events |
| `/api/studio/progress` | POST | Key | Supervisor reports task progress |

### Live Data (existing v1, preserved)
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/logs` | POST | Stream log lines |
| `/api/screenshot` | POST | Push PNG (binary); header `X-Image-Name` |
| `/api/chat/pending` | GET | Unread messages from dashboard |
| `/api/chat/response` | POST | Gemma's reply |
| `/api/chat/history` | GET | Full chat history |
| `/api/state` | GET/POST | Agent state key-value store |
| `/api/reminders` | GET/POST | Gemma's reminder notes |
| `/api/manifesto` | GET/POST | Read/write manifesto |
| `/api/journal` | GET/POST | Read/write journal |
| `/ws` | WebSocket | Realtime broadcast (logs, chat, screenshots) |

---

## 5. Dashboard SPA — Page Flow

```
load → GET /auth/check
         │
         ├─► {configured: false}    → #setup  (first-time password form)
         ├─► {authenticated: false} → #login  (login form)
         └─► {authenticated: true}  → #games  (game manager)
                                         │
                                         ├─► [+ New Game] → #wizard (5-step wizard)
                                         └─► [Open Studio] → #studio (per-game dashboard)
                                                              (logs | chat | observation deck | tabs)
```

### New Game Wizard — 5 Steps
1. **Basics** — name + one-line premise
2. **Style** — side-scroller / top-down RPG / metroidvania / isometric / point-and-click
3. **Scale** — Vignette (20 tasks) / Indie (60) / Classic (150) / Epic (350) / Vast (procedural)
4. **Seeds** — visual seed, story seed, tone pills (Dark/Epic/Surreal/etc.)
5. **Mechanics** — combat, progression, multiplayer (Single/Co-op/MMO-lite)

---

## 6. Game Architecture — Aetheria (Phaser 3)

### Scene Lifecycle
```
BootScene → PreloadScene → GameScene
                                └──► ZoneXxxScene (zone transitions)
                                     UIScene (always parallel)
                                     DialogueScene (NPC conversations)
```

### NPC Soul Files
Each NPC has a JSON soul file at `data/souls/npc_name.json`:
```json
{
  "id": "lyra_valdren",
  "name": "Lyra Valdren",
  "faction": "The Keepers",
  "archetype": "mystic",
  "voice": "measured, riddling, never alarmed",
  "goals": ["protect the Archive", "find the moon's tether"],
  "secrets": ["she remembers before the approach"],
  "dialogue_seeds": ["What you call a disaster, we called a correction."]
}
```
Soul files ship in the game bundle (Vite includes `data/` via `publicDir`).
NPC dialogue is generated at runtime by `@xenova/transformers` in a Web Worker,
seeded from the soul file — no external server needed.

### Asset Conventions
```
public/assets/img/           Sprites, backgrounds, particles
public/assets/maps/          Tiled JSON tilemaps (*.tmj)
public/assets/audio/         BGM (ogg), SFX (wav/ogg)
public/assets/fonts/         Bitmap fonts
```
Reference in Phaser: `this.load.image('key', 'assets/img/file.png')`

### TypeScript Config
```json
{
  "strict": false,
  "target": "ESNext",
  "module": "ESNext",
  "moduleResolution": "bundler",
  "skipLibCheck": true
}
```
`strict: false` eliminates the false-positive TS repair spiral.
The build target for CI is: `npx tsc --noEmit` exits with code 0.

---

## 7. Multiplayer Roadmap

| Phase | When | Tech |
|-------|------|------|
| Single-player | Now | Phaser 3, local NPC LLM |
| Co-op (2–8) | When core loop is complete | Colyseus rooms + Socket.IO |
| MMO-lite (1000+) | When co-op is proven | Colyseus zone-instanced rooms, authoritative server |

Design principle: **design for co-op from the start, ship single-player first.**
Player position, inventory, and dialogue state must be serializable as of day 1.

---

## 8. ComfyUI — Image Generation

| Setting | Value |
|---------|-------|
| URL | `http://127.0.0.1:8188` |
| Model | FLUX 1 Dev FP8 (`flux1-dev-fp8.safetensors`) |
| Output | `/Users/max/ComfyUI/output/` |
| Venv | `/Users/max/comfy310/bin/activate` |

Gemma uses the `generate_image` tool which calls ComfyUI's API.
Generated images are saved to `games/{active_game}/lore/visuals/generated/`.

---

## 9. Key Conventions

| Convention | Rule |
|-----------|------|
| File size | Max 400 lines per source file |
| Naming | `ZoneRuinsScene.ts`, `lyra_valdren.json` (snake_case for data) |
| Imports | `import Phaser from 'phaser'` (default import) |
| Physics | Arcade physics for all moving objects |
| Camera | One main camera per Zone scene; always configure bounds |
| State | `GameState` singleton for cross-scene data |
| Build check | `npx tsc --noEmit` — zero errors is the bar |
| Lore | Zone names / faction names come from `lore/` — never invent outside it |
