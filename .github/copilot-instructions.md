# Gemma Game Dev — Copilot Instructions

## My Role (GitHub Copilot / Claude)

**I am the designer and engineer of the Gemma Game Studio — not the game itself.**

This project is fundamentally a test of autonomous AI game development. The game (Khoros / Aetheria) is the test subject; the real product is the agent loop that makes Gemma productive. My job is:

- Design and maintain `supervisor.py` — the control loop, tool implementations, prompting strategy
- Design and maintain the dashboard (`server.py`, `index.html`) — Gemma's monitoring and feedback interface
- Design the toolset Gemma has access to — what tools exist, their schemas, their reliability
- Design the manifesto and prompting strategy — how Gemma is directed
- Diagnose failures in the agent loop — wrong tools, broken feedback, bad prompts
- **Never write game code** — that is Gemma's job. If the game is broken, the fix is to give Gemma better tools and feedback so she can fix it herself.

The game she is building now is a test case. The studio — the infrastructure that lets an AI build a game autonomously — is what we are actually building.

---

## Rules

**Never send a message to Gemma without explicit approval.**
Before writing anything to `human_feedback.md` or posting to `/api/chat/response`, show the exact text of the message to the user and wait for a go-ahead. No exceptions — not even minor clarifications or "quick" redirects.

**Never edit game code directly.**
Files under `game_workspace/src/` are Gemma's domain. If they are broken, the right response is to improve the tools or feedback loop so Gemma can fix them, not to fix them myself.

**When the agent is stuck, improve the agent — never bypass it.**
If Gemma is looping, failing to fix errors, or producing bad output, the answer is always one of:
- Improve `supervisor.py` — better prompting, smarter triage, stronger trivial-fix logic, clearer feedback signals
- Improve the REPAIR/BUILD loop — add deterministic pre-processing so Gemma gets solvable problems
- Improve context assembly — give Gemma the right information in the right format

It is never acceptable to:
- Read game source files to understand "what's wrong" and then fix them myself
- Diagnose TypeScript errors in `game_workspace/src/` and patch them directly
- Take any shortcut that removes the agent from the loop

The question is always: **how do I make the agent better at solving this class of problem?** Not: **what is the fastest way to fix this file?**

---

An autonomous AI game developer loop. Gemma (Google Gemma 4 via Vertex AI) runs
in a continuous supervisor loop, building lore, concept art, and eventually game
code for a sci-fi RPG called **Khoros**. The supervisor runs on the local Mac; the
dashboard runs on a remote DigitalOcean droplet.

---

## Infrastructure

### Local (Mac Studio)
| Service | How to start | Log |
|---------|-------------|-----|
| Supervisor | `cd /Users/max/Repos/gemma_game_dev && ./start_supervisor.sh` | `supervisor.log` |
| ComfyUI | `/Users/max/Repos/gemma_game_dev/start_comfyui.sh > /tmp/comfyui.log 2>&1 &` | `/tmp/comfyui.log` |
| Vite (game client) | `npx vite src/client --port 5175` (from `game_workspace/`) | stdout |

**`start_supervisor.sh` is bulletproof:** kills any existing supervisor (via PID file or process name), cleans stale state, launches a fresh instance, and verifies it started. Always use it instead of running `supervisor.py` directly.

**Supervisor PID lock:** `supervisor.py` writes `supervisor.pid` on startup and checks it on launch — a second instance will refuse to start if one is already running.

**VS Code buffer vs disk:** Editor tool writes (`replace_string_in_file`, `create_file`) go to VS Code's in-memory buffer, NOT to disk immediately. Always run `workbench.action.files.saveAll` after any edit to `supervisor.py` before verifying with `grep` or restarting the supervisor. Terminal writes via `run_in_terminal` go directly to disk and don't need this step.

**Restart order after crash:** ComfyUI → supervisor → Vite.

### Remote Droplet (DigitalOcean)
| Detail | Value |
|--------|-------|
| IP | `165.227.27.71` |
| SSH alias | `epiphany` (`ssh epiphany`) |
| SSH key | `~/.ssh/macstudio` |
| User | `root` |
| Dashboard port | `8080` |
| Dashboard URL | `http://165.227.27.71:8080` |
| API key | `epiphany_secret_2026` |

**Deploy updated dashboard files to the droplet:**
```bash
scp /Users/max/Repos/gemma_game_dev/dashboard/server.py epiphany:/root/dashboard/server.py
scp /Users/max/Repos/gemma_game_dev/dashboard/index.html epiphany:/root/dashboard/index.html
# Then restart the server on the droplet:
ssh epiphany "cd /root/dashboard && pkill -f server.py; source venv/bin/activate && nohup python server.py > server.log 2>&1 &"
```

**Check dashboard server status:**
```bash
ssh epiphany "ps aux | grep server.py | grep -v grep"
```

### ComfyUI (local)
| Detail | Value |
|--------|-------|
| URL | `http://127.0.0.1:8188` |
| venv | `/Users/max/comfy310/bin/activate` |
| Install dir | `/Users/max/ComfyUI/` |
| Output dir | `/Users/max/ComfyUI/output/` |
| FLUX model | `models/unet/flux1-dev-fp8.safetensors` |
| T5 encoder | `models/text_encoders/t5xxl_fp8_e4m3fn.safetensors` |
| VAE | `models/vae/ae.safetensors` |
| CLIP | `models/clip/clip_l.safetensors` |

---

## Key Files

| File | Purpose |
|------|---------|
| `supervisor.py` | Main loop — prompts Gemma, executes tools, pushes to dashboard |
| `manifesto.md` | Gemma's prime directive — what she should do and how |
| `human_feedback.md` | Drop messages here to send to Gemma on next iteration |
| `game_workspace/lore/` | All world-building output |
| `game_workspace/lore/presentations/presentation_current.md` | Latest world portfolio |
| `game_workspace/lore/presentations/presentation_NNN.md` | Archived presentation at iteration NNN |
| `game_workspace/lore/visuals/generated/` | Concept images generated by Gemma via ComfyUI |
| `dashboard/server.py` | Aiohttp server running on the droplet |
| `dashboard/index.html` | Dashboard frontend (served by the droplet) |
| `start_comfyui.sh` | Convenience script to start ComfyUI with the right venv |

---

## Supervisor Architecture

- **CREATIVE phase**: Gemma builds lore and concept art. Tools: `create_file`, `read_file`, `list_files`, `generate_image`, `chat_respond`.
- **TECHNICAL phase**: Gemma writes game code. Tools add `run_bash`, `capture_screenshot`, `search_codebase`, `search_web`.
- Phase transition: Gemma creates `lore/PHASE_COMPLETE.md`.
- **Presentations**: Every 1000 iterations, Gemma archives `presentation_current.md` → `presentation_NNN.md`, then generates 3–5 concept images and writes a new illustrated `presentation_current.md`.

### Dashboard API (droplet, port 8080)
All requests need header `X-API-KEY: epiphany_secret_2026`.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/logs` | POST | Stream log lines to dashboard terminal |
| `/api/screenshot` | POST | Push PNG (binary); header `X-Image-Name` sets label |
| `/api/chat/pending` | GET | Fetch unread messages from dashboard chat |
| `/api/chat/response` | POST | Gemma's chat replies |
| `/api/chat/history` | GET | Full chat history |
| `/api/state` | GET/POST | Persistent key/value agent state |
| `/api/reminders` | GET/POST | Gemma's reminders |
| `/ws` | WebSocket | Realtime log/screenshot/chat broadcast to browser |

---

## Common Tasks

**Check what Gemma is doing:**
```bash
tail -50 /Users/max/Repos/gemma_game_dev/supervisor.log
```

**See latest concept art:**
```bash
ls -lt /Users/max/ComfyUI/output/ | head -10
```

**Send Gemma a message:**
Edit `human_feedback.md` — she reads it on the next iteration and the file is consumed.
Or use the dashboard chat at `http://165.227.27.71:8080`.

**Deploy server + frontend changes to droplet:**
```bash
scp dashboard/server.py dashboard/index.html epiphany:/root/dashboard/
ssh epiphany "cd /root/dashboard && pkill -f server.py; source venv/bin/activate && nohup python server.py > server.log 2>&1 &"
```
