import asyncio
import json
import logging
from aiohttp import web, WSMsgType
import asyncpg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Dashboard")

API_KEY = "epiphany_secret_2026"
chat_queue = []
connected_clients = set()
db_pool = None

async def init_db(app):
    """Initialize asyncpg connection pool on startup."""
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(
            user='gemma_user',
            password='epiphany_db_pass',
            database='gemma_db',
            host='127.0.0.1'
        )
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS chat_history (
                    id SERIAL PRIMARY KEY,
                    sender VARCHAR(50),
                    message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS screenshot_history (
                    id SERIAL PRIMARY KEY,
                    url TEXT NOT NULL,
                    label TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS action_log (
                    id SERIAL PRIMARY KEY,
                    iteration INT,
                    tool VARCHAR(50),
                    summary TEXT,
                    outcome VARCHAR(20),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        logger.info("Database connection pool established and tables verified.")
    except Exception as e:
        logger.error(f"Failed to connect to DB: {e}")

async def close_db(app):
    """Close asyncpg connection pool on shutdown."""
    global db_pool
    if db_pool:
        await db_pool.close()

# Security Middleware
@web.middleware
async def api_auth_middleware(request, handler):
    if request.path.startswith('/api/'):
        provided_key = request.headers.get("X-API-KEY")
        if provided_key != API_KEY:
            return web.json_response({"error": "Unauthorized"}, status=401)
    return await handler(request)

async def handle_index(request):
    return web.FileResponse('index.html')

async def handle_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    connected_clients.add(ws)
    logger.info("New WebSocket client connected")
    
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "chat":
                    user_msg = data.get("text")
                    chat_queue.append(user_msg)
                    if db_pool:
                        async with db_pool.acquire() as conn:
                            await conn.execute("INSERT INTO chat_history (sender, message) VALUES ('human', $1)", user_msg)
                    logger.info(f"Queued chat from UI: {user_msg}")
            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")
    finally:
        connected_clients.remove(ws)
        logger.info("WebSocket client disconnected")
    return ws

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
    """Supervisor logs a human message (from human_feedback.md) into chat history."""
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
    """Returns the last 50 chat messages."""
    if not db_pool: return web.json_response({"error": "No DB"}, status=500)
    async with db_pool.acquire() as conn:
        records = await conn.fetch("SELECT sender, message FROM (SELECT * FROM chat_history ORDER BY id DESC LIMIT 50) sub ORDER BY id ASC")
        history = [{"sender": r['sender'], "message": r['message']} for r in records]
    return web.json_response({"history": history})

async def api_clear_chat_history(request):
    """Deletes all chat history."""
    if not db_pool: return web.json_response({"error": "No DB"}, status=500)
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM chat_history")
    return web.json_response({"status": "ok"})

import os
import time

# Create media directory for screenshots
os.makedirs('media', exist_ok=True)

# --- Database API Endpoints ---

async def api_push_screenshot(request):
    """Mac Supervisor pushes a binary PNG screenshot."""
    if not db_pool: return web.json_response({"error": "No DB"}, status=500)
    
    # Read binary image data
    image_data = await request.read()
    if not image_data:
        return web.json_response({"error": "No image data"}, status=400)
        
    original_name = request.headers.get("X-Image-Name", "")
    # Use the original name if provided, stripping ComfyUI's numeric suffix (e.g. _00001_)
    if original_name:
        safe_name = original_name.replace("/", "_")
        # Strip ComfyUI counter suffix so the label reads cleanly
        import re as _re
        label = _re.sub(r'_\d{5}_', '', safe_name).replace('.png', '').replace('_', ' ').strip()
        filename = f"{int(time.time())}_{safe_name}"
    else:
        label = ""
        filename = f"screenshot_{int(time.time())}.png"
    filepath = os.path.join("media", filename)
    
    with open(filepath, "wb") as f:
        f.write(image_data)
        
    url = f"/media/{filename}"

    # Persist to DB
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO screenshot_history (url, label) VALUES ($1, $2)",
                url, label
            )

    # Broadcast to WebSockets
    for ws in connected_clients:
        await ws.send_json({"type": "screenshot", "url": url, "name": label})
        
    return web.json_response({"status": "ok", "url": url})

async def api_get_screenshots(request):
    """Return the last 200 screenshots for replay on page load."""
    if not db_pool: return web.json_response({"screenshots": []})
    async with db_pool.acquire() as conn:
        records = await conn.fetch(
            "SELECT url, label, EXTRACT(EPOCH FROM created_at)*1000 AS ts FROM screenshot_history ORDER BY id DESC LIMIT 200"
        )
    shots = [{"url": r["url"], "name": r["label"] or "", "timestamp": int(r["ts"])} for r in reversed(records)]
    return web.json_response({"screenshots": shots})

async def api_clear_screenshots(request):
    """Delete all screenshot history records (files on disk are left as-is)."""
    if not db_pool: return web.json_response({'error': 'No DB'}, status=500)
    async with db_pool.acquire() as conn:
        await conn.execute('DELETE FROM screenshot_history')
    return web.json_response({'status': 'ok'})

async def api_get_state(request):
    """Get entire agent_state table."""
    if not db_pool: return web.json_response({"error": "No DB"}, status=500)
    async with db_pool.acquire() as conn:
        records = await conn.fetch("SELECT key, value FROM agent_state")
        state = {r['key']: r['value'] for r in records}
    return web.json_response({"state": state})

async def api_update_state(request):
    """Update a specific key in agent_state."""
    if not db_pool: return web.json_response({"error": "No DB"}, status=500)
    data = await request.json()
    key = data.get("key")
    value = str(data.get("value", ""))
    
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agent_state (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = $2",
            key, value
        )
    return web.json_response({"status": "ok", "key": key})

async def api_get_reminders(request):
    """Get all reminders."""
    if not db_pool: return web.json_response({"error": "No DB"}, status=500)
    async with db_pool.acquire() as conn:
        records = await conn.fetch("SELECT note FROM reminders ORDER BY id ASC")
        reminders = [r['note'] for r in records]
    return web.json_response({"reminders": reminders})

async def api_add_reminder(request):
    """Add a new reminder."""
    if not db_pool: return web.json_response({"error": "No DB"}, status=500)
    data = await request.json()
    note = data.get("note")
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO reminders (note) VALUES ($1)", note)
    return web.json_response({"status": "ok"})

async def api_get_journal(request):
    """Serve the content of journal.md from the workspace."""
    try:
        journal_path = 'journal.md'
        if os.path.exists(journal_path):
            with open(journal_path, 'r') as f:
                return web.json_response({"content": f.read()})
        return web.json_response({"content": "Journal not found."}, status=404)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_log_action(request):
    """Supervisor logs one concise action entry per iteration."""
    if not db_pool: return web.json_response({"error": "No DB"}, status=500)
    data = await request.json()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO action_log (iteration, tool, summary, outcome) VALUES ($1, $2, $3, $4)",
            int(data.get("iteration", 0)), data.get("tool", ""), data.get("summary", ""), data.get("outcome", "ok")
        )
    return web.json_response({"status": "ok"})

async def api_get_action_log(request):
    """Return the last N action log entries (default 20)."""
    if not db_pool: return web.json_response({"log": []})
    n = min(int(request.rel_url.query.get("n", 20)), 100)
    async with db_pool.acquire() as conn:
        records = await conn.fetch(
            "SELECT iteration, tool, summary, outcome FROM "
            "(SELECT * FROM action_log ORDER BY id DESC LIMIT $1) sub ORDER BY id ASC",
            n
        )
    entries = [{"iteration": r["iteration"], "tool": r["tool"], "summary": r["summary"], "outcome": r["outcome"]} for r in records]
    return web.json_response({"log": entries})

async def api_get_manifesto(request):
    """Serve the content of manifesto.md."""
    try:
        if os.path.exists('manifesto.md'):
            with open('manifesto.md', 'r') as f:
                return web.json_response({"content": f.read()})
        return web.json_response({"content": "Manifesto not found."}, status=404)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_update_journal(request):
    """Update the content of journal.md on the server."""
    data = await request.json()
    content = data.get("content", "")
    with open('journal.md', 'w') as f:
        f.write(content)
    return web.json_response({"status": "ok"})

async def api_update_manifesto(request):
    """Update the content of manifesto.md on the server."""
    data = await request.json()
    content = data.get("content", "")
    with open('manifesto.md', 'w') as f:
        f.write(content)
    return web.json_response({"status": "ok"})

app = web.Application(middlewares=[api_auth_middleware])
app.on_startup.append(init_db)
app.on_cleanup.append(close_db)

app.add_routes([
    web.get('/', handle_index),
    web.get('/ws', handle_ws),
    web.post('/api/logs', api_push_log),
    web.get('/api/chat/pending', api_get_pending_chat),
    web.get('/api/chat/history', api_get_chat_history),
    web.delete('/api/chat/history', api_clear_chat_history),
    web.post('/api/chat/human', api_push_human_message),
    web.post('/api/chat/response', api_push_chat_response),
    web.post('/api/screenshot', api_push_screenshot),
    web.get('/api/screenshots', api_get_screenshots),
    web.delete('/api/screenshots', api_clear_screenshots),
    web.get('/api/state', api_get_state),
    web.post('/api/state', api_update_state),
    web.get('/api/reminders', api_get_reminders),
    web.post('/api/reminders', api_add_reminder),
    web.get('/api/manifesto', api_get_manifesto),
    web.post('/api/manifesto', api_update_manifesto),
    web.get('/api/journal', api_get_journal),
    web.post('/api/journal', api_update_journal),
    web.post('/api/action_log', api_log_action),
    web.get('/api/action_log', api_get_action_log),
    web.static('/media', 'media')
])

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8080)
