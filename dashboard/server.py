# =============================================================================
# Gemma Game Studio — dashboard/server.py v2
# Multi-game studio dashboard server.
#
# New in v2:
#   - JWT auth (bcrypt password, httpOnly cookie)
#   - Game manager API (list, create, switch active game)
#   - Studio config sync (supervisor <-> dashboard)
#   - Pending actions queue (supervisor polls for switch/new_game events)
#   - All v1 endpoints preserved unchanged
# =============================================================================

import asyncio
import json
import logging
import os
import re as _re
import time
from datetime import datetime, timedelta, timezone
from aiohttp import web, WSMsgType
import asyncpg
import bcrypt
import jwt as pyjwt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Dashboard")

# Config
API_KEY       = os.environ.get("API_KEY", "epiphany_secret_2026")
STUDIO_SECRET = os.environ.get("STUDIO_SECRET", "gemma-studio-v2-secret")
TOKEN_COOKIE  = "studio_token"
TOKEN_TTL     = timedelta(days=30)

chat_queue        = []
connected_clients = set()
db_pool           = None

os.makedirs("media", exist_ok=True)

# =============================================================================
# DB
# =============================================================================

async def init_db(app):
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(
            user="gemma_user", password="epiphany_db_pass",
            database="gemma_db", host="127.0.0.1"
        )
        async with db_pool.acquire() as conn:
            await conn.execute("""CREATE TABLE IF NOT EXISTS chat_history (
                id SERIAL PRIMARY KEY, sender VARCHAR(50),
                message TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS screenshot_history (
                id SERIAL PRIMARY KEY, url TEXT NOT NULL, label TEXT,
                game_slug VARCHAR(50) DEFAULT 'aetheria',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS action_log (
                id SERIAL PRIMARY KEY, iteration INT, tool VARCHAR(50),
                summary TEXT, outcome VARCHAR(20),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS agent_state (
                key VARCHAR(50) PRIMARY KEY, value TEXT)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS reminders (
                id SERIAL PRIMARY KEY, note TEXT)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS studio_state (
                key VARCHAR(50) PRIMARY KEY, value TEXT)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS games (
                id SERIAL PRIMARY KEY,
                slug VARCHAR(50) UNIQUE NOT NULL,
                name VARCHAR(100) NOT NULL,
                style VARCHAR(50) DEFAULT '',
                scale VARCHAR(50) DEFAULT 'indie',
                multiplayer VARCHAR(50) DEFAULT 'single',
                phase VARCHAR(50) DEFAULT 'CREATIVE',
                tasks_complete INT DEFAULT 0,
                config JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS pending_actions (
                id SERIAL PRIMARY KEY, action_type VARCHAR(50),
                payload JSONB, consumed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            # add game_slug column to screenshot_history if missing (migration)
            try:
                await conn.execute("ALTER TABLE screenshot_history ADD COLUMN IF NOT EXISTS game_slug VARCHAR(50) DEFAULT 'aetheria'")
            except Exception:
                pass
            await conn.execute("""INSERT INTO games (slug, name, style, scale, multiplayer, phase, tasks_complete)
                VALUES ('aetheria', 'Aetheria', 'side-scroller', 'epic', 'single', 'BUILD', 83)
                ON CONFLICT (slug) DO NOTHING""")
        logger.info("DB initialized (v2).")
    except Exception as e:
        logger.error(f"DB init failed: {e}")

async def close_db(app):
    global db_pool
    if db_pool:
        await db_pool.close()

# =============================================================================
# Auth helpers
# =============================================================================

def _make_token() -> str:
    payload = {"exp": datetime.now(timezone.utc) + TOKEN_TTL, "iss": "studio"}
    return pyjwt.encode(payload, STUDIO_SECRET, algorithm="HS256")

def _check_token(request) -> bool:
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return False
    try:
        pyjwt.decode(token, STUDIO_SECRET, algorithms=["HS256"])
        return True
    except pyjwt.InvalidTokenError:
        return False

def _set_token(response):
    response.set_cookie(TOKEN_COOKIE, _make_token(),
                        httponly=True, samesite="Strict", max_age=30 * 86400)
    return response

# =============================================================================
# Middleware
# =============================================================================

@web.middleware
async def auth_middleware(request, handler):
    path = request.path
    if path.startswith("/api/") or path == "/ws":
        if request.headers.get("X-API-KEY") == API_KEY:
            return await handler(request)
        if _check_token(request):
            return await handler(request)
        return web.json_response({"error": "Unauthorized"}, status=401)
    # auth endpoints, media, index always public
    return await handler(request)

# =============================================================================
# Auth routes
# =============================================================================

async def handle_auth_check(request):
    if not db_pool:
        return web.json_response({"configured": False, "authenticated": False})
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM studio_state WHERE key = 'password_hash'")
    return web.json_response({"configured": row is not None, "authenticated": _check_token(request)})

async def handle_auth_setup(request):
    if not db_pool:
        return web.json_response({"error": "DB not ready"}, status=503)
    async with db_pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT value FROM studio_state WHERE key = 'password_hash'")
    if existing:
        return web.json_response({"error": "Already configured"}, status=403)
    data = await request.json()
    password = data.get("password", "")
    if len(password) < 8:
        return web.json_response({"error": "Password must be >= 8 characters"}, status=400)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO studio_state (key, value) VALUES ('password_hash', $1)", hashed)
    return _set_token(web.json_response({"status": "ok"}))

async def handle_auth_login(request):
    if not db_pool:
        return web.json_response({"error": "DB not ready"}, status=503)
    data = await request.json()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM studio_state WHERE key = 'password_hash'")
    if not row:
        return web.json_response({"error": "Not configured"}, status=503)
    if not bcrypt.checkpw(data.get("password", "").encode(), row["value"].encode()):
        return web.json_response({"error": "Invalid password"}, status=401)
    return _set_token(web.json_response({"status": "ok"}))

async def handle_auth_logout(request):
    response = web.json_response({"status": "ok"})
    response.del_cookie(TOKEN_COOKIE)
    return response

# =============================================================================
# Game manager routes
# =============================================================================

async def api_list_games(request):
    if not db_pool:
        return web.json_response({"games": [], "active_game": ""})
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug, name, style, scale, multiplayer, phase, tasks_complete, created_at "
            "FROM games ORDER BY created_at ASC")
        active_game = await conn.fetchval("SELECT value FROM studio_state WHERE key = 'active_game'")
    games = []
    for r in rows:
        g = dict(r)
        g["created_at"] = g["created_at"].isoformat() if g.get("created_at") else ""
        games.append(g)
    return web.json_response({"games": games, "active_game": active_game or ""})

async def api_create_game(request):
    if not db_pool:
        return web.json_response({"error": "DB not ready"}, status=503)
    data = await request.json()
    slug = _re.sub(r"[^a-z0-9-]", "-", data.get("name", "game").lower().strip())
    slug = _re.sub(r"-+", "-", slug).strip("-") or "game"
    async with db_pool.acquire() as conn:
        existing = await conn.fetchval("SELECT COUNT(*) FROM games WHERE slug = $1", slug)
        if existing:
            slug = f"{slug}-{int(time.time()) % 10000}"
        await conn.execute(
            "INSERT INTO games (slug, name, style, scale, multiplayer, phase, config) "
            "VALUES ($1, $2, $3, $4, $5, 'CREATIVE', $6)",
            slug, data.get("name", slug), data.get("style", "side-scroller"),
            data.get("scale", "indie"), data.get("multiplayer", "single"),
            json.dumps(data))
        await conn.execute(
            "INSERT INTO pending_actions (action_type, payload) VALUES ('new_game', $1)",
            json.dumps({**data, "slug": slug}))
    for ws in connected_clients:
        await ws.send_json({"type": "game_created", "slug": slug})
    return web.json_response({"status": "ok", "slug": slug})

async def api_switch_game(request):
    if not db_pool:
        return web.json_response({"error": "DB not ready"}, status=503)
    data = await request.json()
    slug = data.get("slug", "")
    async with db_pool.acquire() as conn:
        exists = await conn.fetchval("SELECT COUNT(*) FROM games WHERE slug = $1", slug)
        if not exists:
            return web.json_response({"error": "Unknown game"}, status=404)
        await conn.execute(
            "INSERT INTO studio_state (key, value) VALUES ('active_game', $1) "
            "ON CONFLICT (key) DO UPDATE SET value = $1", slug)
        await conn.execute(
            "INSERT INTO pending_actions (action_type, payload) VALUES ('switch_game', $1)",
            json.dumps({"slug": slug}))
    for ws in connected_clients:
        await ws.send_json({"type": "game_switched", "slug": slug})
    return web.json_response({"status": "ok"})

async def api_get_pending_actions(request):
    if not db_pool:
        return web.json_response({"actions": []})
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, action_type, payload FROM pending_actions "
            "WHERE consumed = FALSE ORDER BY id ASC")
        if rows:
            await conn.execute(
                "UPDATE pending_actions SET consumed = TRUE WHERE id = ANY($1)",
                [r["id"] for r in rows])
    return web.json_response({"actions": [
        {"type": r["action_type"], "payload": json.loads(r["payload"])} for r in rows]})

async def api_push_studio_config(request):
    if not db_pool:
        return web.json_response({"error": "DB not ready"}, status=503)
    data = await request.json()
    active = data.get("active_game", "")
    async with db_pool.acquire() as conn:
        if active:
            await conn.execute(
                "INSERT INTO studio_state (key, value) VALUES ('active_game', $1) "
                "ON CONFLICT (key) DO UPDATE SET value = $1", active)
        for slug, info in data.get("games", {}).items():
            await conn.execute(
                "INSERT INTO games (slug, name, style, scale, multiplayer, phase, tasks_complete, config) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
                "ON CONFLICT (slug) DO UPDATE SET phase = EXCLUDED.phase, tasks_complete = EXCLUDED.tasks_complete",
                slug, info.get("name", slug), info.get("style", ""),
                info.get("scale", "indie"), info.get("multiplayer", "single"),
                info.get("phase", "CREATIVE"), int(info.get("tasks_complete", 0)),
                json.dumps(info))
    if active:
        for ws in connected_clients:
            await ws.send_json({"type": "active_game_changed", "slug": active})
    return web.json_response({"status": "ok"})

async def api_update_game_progress(request):
    if not db_pool:
        return web.json_response({"error": "DB not ready"}, status=503)
    data = await request.json()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE games SET phase = $2, tasks_complete = $3 WHERE slug = $1",
            data.get("slug", ""), data.get("phase", "BUILD"),
            int(data.get("tasks_complete", 0)))
    return web.json_response({"status": "ok"})

async def api_studio_pause(request):
    if not db_pool:
        return web.json_response({"error": "DB not ready"}, status=503)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pending_actions (action_type, payload) VALUES ('pause', $1)",
            json.dumps({}))
    for ws in connected_clients:
        await ws.send_json({"type": "supervisor_command", "command": "pause"})
    return web.json_response({"status": "ok"})

async def api_studio_resume(request):
    if not db_pool:
        return web.json_response({"error": "DB not ready"}, status=503)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pending_actions (action_type, payload) VALUES ('resume', $1)",
            json.dumps({}))
    for ws in connected_clients:
        await ws.send_json({"type": "supervisor_command", "command": "resume"})
    return web.json_response({"status": "ok"})

# =============================================================================
# WebSocket
# =============================================================================

async def handle_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    connected_clients.add(ws)
    logger.info("WS client connected")
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "chat":
                    user_msg = data.get("text", "")
                    chat_queue.append(user_msg)
                    if db_pool:
                        async with db_pool.acquire() as conn:
                            await conn.execute(
                                "INSERT INTO chat_history (sender, message) VALUES ('human', $1)", user_msg)
            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WS error: {ws.exception()}")
    finally:
        connected_clients.discard(ws)
    return ws

# =============================================================================
# v1 API endpoints (unchanged)
# =============================================================================

async def handle_index(request):
    return web.FileResponse("index.html")

async def api_push_log(request):
    data = await request.json()
    log_text = data.get("log", "")
    for ws in connected_clients:
        await ws.send_json({"type": "log", "text": log_text})
    return web.json_response({"status": "ok"})

async def api_get_pending_chat(request):
    global chat_queue
    pending = list(chat_queue)
    chat_queue.clear()
    return web.json_response({"messages": pending})

async def api_push_chat_response(request):
    data = await request.json()
    msg_text = data.get("message", "")
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO chat_history (sender, message) VALUES ('agent', $1)", msg_text)
    for ws in connected_clients:
        await ws.send_json({"type": "agent_chat", "text": msg_text})
    return web.json_response({"status": "ok"})

async def api_push_human_message(request):
    data = await request.json()
    msg_text = data.get("message", "")
    if not msg_text:
        return web.json_response({"error": "No message"}, status=400)
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO chat_history (sender, message) VALUES ('human', $1)", msg_text)
    for ws in connected_clients:
        await ws.send_json({"type": "human_chat", "text": msg_text})
    return web.json_response({"status": "ok"})

async def api_get_chat_history(request):
    if not db_pool:
        return web.json_response({"history": []})
    async with db_pool.acquire() as conn:
        records = await conn.fetch(
            "SELECT sender, message FROM (SELECT * FROM chat_history ORDER BY id DESC LIMIT 50) sub ORDER BY id ASC")
    return web.json_response({"history": [{"sender": r["sender"], "message": r["message"]} for r in records]})

async def api_clear_chat_history(request):
    if not db_pool:
        return web.json_response({"error": "No DB"}, status=500)
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM chat_history")
    return web.json_response({"status": "ok"})

async def api_push_screenshot(request):
    image_data = await request.read()
    if not image_data:
        return web.json_response({"error": "No image data"}, status=400)
    original_name = request.headers.get("X-Image-Name", "")
    game_slug = request.headers.get("X-Game-Slug", "aetheria")
    if original_name:
        safe_name = original_name.replace("/", "_")
        label = _re.sub(r"_\d{5}_", "", safe_name).replace(".png", "").replace("_", " ").strip()
        filename = f"{int(time.time())}_{safe_name}"
    else:
        label = ""
        filename = f"screenshot_{int(time.time())}.png"
    with open(os.path.join("media", filename), "wb") as f:
        f.write(image_data)
    url = f"/media/{filename}"
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO screenshot_history (url, label, game_slug) VALUES ($1, $2, $3)",
                url, label, game_slug)
    for ws in connected_clients:
        await ws.send_json({"type": "screenshot", "url": url, "name": label, "game": game_slug})
    return web.json_response({"status": "ok", "url": url})

async def api_get_screenshots(request):
    if not db_pool:
        return web.json_response({"screenshots": []})
    game_slug = request.rel_url.query.get("game", "")
    async with db_pool.acquire() as conn:
        if game_slug:
            records = await conn.fetch(
                "SELECT url, label, EXTRACT(EPOCH FROM created_at)*1000 AS ts "
                "FROM screenshot_history WHERE game_slug = $1 ORDER BY id DESC LIMIT 200", game_slug)
        else:
            records = await conn.fetch(
                "SELECT url, label, EXTRACT(EPOCH FROM created_at)*1000 AS ts "
                "FROM screenshot_history ORDER BY id DESC LIMIT 200")
    shots = [{"url": r["url"], "name": r["label"] or "", "timestamp": int(r["ts"])}
             for r in reversed(records)]
    return web.json_response({"screenshots": shots})

async def api_clear_screenshots(request):
    if not db_pool:
        return web.json_response({"error": "No DB"}, status=500)
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM screenshot_history")
    return web.json_response({"status": "ok"})

async def api_get_state(request):
    if not db_pool:
        return web.json_response({"state": {}})
    async with db_pool.acquire() as conn:
        records = await conn.fetch("SELECT key, value FROM agent_state")
    return web.json_response({"state": {r["key"]: r["value"] for r in records}})

async def api_update_state(request):
    if not db_pool:
        return web.json_response({"error": "No DB"}, status=500)
    data = await request.json()
    key = data.get("key")
    value = str(data.get("value", ""))
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agent_state (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = $2",
            key, value)
    for ws in connected_clients:
        await ws.send_json({"type": "state", "key": key, "value": value})
    return web.json_response({"status": "ok"})

async def api_get_reminders(request):
    if not db_pool:
        return web.json_response({"reminders": []})
    async with db_pool.acquire() as conn:
        records = await conn.fetch("SELECT note FROM reminders ORDER BY id ASC")
    return web.json_response({"reminders": [r["note"] for r in records]})

async def api_add_reminder(request):
    if not db_pool:
        return web.json_response({"error": "No DB"}, status=500)
    data = await request.json()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO reminders (note) VALUES ($1)", data.get("note", ""))
    return web.json_response({"status": "ok"})

async def api_get_journal(request):
    try:
        if os.path.exists("journal.md"):
            with open("journal.md") as f:
                return web.json_response({"content": f.read()})
        return web.json_response({"content": "Journal not found."}, status=404)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_update_journal(request):
    data = await request.json()
    with open("journal.md", "w") as f:
        f.write(data.get("content", ""))
    return web.json_response({"status": "ok"})

async def api_get_manifesto(request):
    try:
        if os.path.exists("manifesto.md"):
            with open("manifesto.md") as f:
                return web.json_response({"content": f.read()})
        return web.json_response({"content": "Manifesto not found."}, status=404)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_update_manifesto(request):
    data = await request.json()
    with open("manifesto.md", "w") as f:
        f.write(data.get("content", ""))
    return web.json_response({"status": "ok"})

async def api_log_action(request):
    if not db_pool:
        return web.json_response({"error": "No DB"}, status=500)
    data = await request.json()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO action_log (iteration, tool, summary, outcome) VALUES ($1, $2, $3, $4)",
            int(data.get("iteration", 0)), data.get("tool", ""),
            data.get("summary", ""), data.get("outcome", "ok"))
    return web.json_response({"status": "ok"})

async def api_get_action_log(request):
    if not db_pool:
        return web.json_response({"log": []})
    n = min(int(request.rel_url.query.get("n", 20)), 100)
    async with db_pool.acquire() as conn:
        records = await conn.fetch(
            "SELECT iteration, tool, summary, outcome FROM "
            "(SELECT * FROM action_log ORDER BY id DESC LIMIT $1) sub ORDER BY id ASC", n)
    return web.json_response({"log": [dict(r) for r in records]})

# =============================================================================
# App
# =============================================================================

app = web.Application(middlewares=[auth_middleware])
app.on_startup.append(init_db)
app.on_cleanup.append(close_db)

app.add_routes([
    web.get("/", handle_index),
    web.static("/media", "media"),
    web.get("/ws", handle_ws),
    # Auth
    web.get("/auth/check", handle_auth_check),
    web.post("/auth/setup", handle_auth_setup),
    web.post("/auth/login", handle_auth_login),
    web.post("/auth/logout", handle_auth_logout),
    # Game manager (v2)
    web.get("/api/games", api_list_games),
    web.post("/api/games", api_create_game),
    web.post("/api/games/switch", api_switch_game),
    web.post("/api/studio/config", api_push_studio_config),
    web.get("/api/studio/pending", api_get_pending_actions),
    web.post("/api/studio/progress", api_update_game_progress),
    web.post("/api/studio/pause", api_studio_pause),
    web.post("/api/studio/resume", api_studio_resume),
    # Agent comms (v1)
    web.post("/api/logs", api_push_log),
    web.get("/api/chat/pending", api_get_pending_chat),
    web.get("/api/chat/history", api_get_chat_history),
    web.delete("/api/chat/history", api_clear_chat_history),
    web.post("/api/chat/human", api_push_human_message),
    web.post("/api/chat/response", api_push_chat_response),
    web.post("/api/screenshot", api_push_screenshot),
    web.get("/api/screenshots", api_get_screenshots),
    web.delete("/api/screenshots", api_clear_screenshots),
    web.get("/api/state", api_get_state),
    web.post("/api/state", api_update_state),
    web.get("/api/reminders", api_get_reminders),
    web.post("/api/reminders", api_add_reminder),
    web.get("/api/manifesto", api_get_manifesto),
    web.post("/api/manifesto", api_update_manifesto),
    web.get("/api/journal", api_get_journal),
    web.post("/api/journal", api_update_journal),
    web.post("/api/action_log", api_log_action),
    web.get("/api/action_log", api_get_action_log),
])

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)