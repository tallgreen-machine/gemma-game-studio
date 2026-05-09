import asyncio
import aiohttp
import json
import subprocess
import os
import sys
import logging
from ddgs import DDGS
import chromadb
import uuid

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("GemmaSupervisor")

# Configuration
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "gemma4:31b"
WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "game_workspace"))
MANIFESTO_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "manifesto.md"))
JOURNAL_PATH = os.path.join(WORKSPACE_DIR, "journal.md")
FEEDBACK_PATH = os.path.join(os.path.dirname(__file__), "human_feedback.md")
DROPLET_SSH = "epiphany"  # Used for rsync over SSH
DROPLET_IP = "165.227.27.71" # Used for HTTP API calls
MAX_FAILURES = 5

class GemmaSupervisor:
    def __init__(self):
        self.failures = 0
        self.iteration = 0
        self.ddgs = DDGS()
        self.action_history = [] # Track last few actions to detect loops
        self.cognitive_history = [] # Track last few thoughts/results for context
        self.pending_screenshot = None  # Path to screenshot to include in next prompt
        self.last_gen_filename = None   # Loop guard: track repeated generate_image filenames
        self.last_gen_repeat_count = 0
        self.last_build_error_count = None  # For delta tracking across builds
        self.consecutive_build_count = 0    # How many run_builds in a row with no create_file
        self.scratchpad = ""               # Gemma's persistent working memory, always injected
        self.investigation_log = []        # Supervisor-maintained list of findings, auto-updated
        self.read_history = set()          # Files already read this session
        
        # Init ChromaDB
        self.chroma_client = chromadb.PersistentClient(path=os.path.join(os.path.dirname(__file__), "chroma_db"))
        self.collection = self.chroma_client.get_or_create_collection(name="gemma_codebase")
        
    async def initialize_workspace(self):
        logger.info("Initializing Workspace Natively...")
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        
        # Ensure feedback file exists
        if not os.path.exists(FEEDBACK_PATH):
            with open(FEEDBACK_PATH, "w") as f:
                f.write("<!-- Write your feedback or ideas here. The agent will read this on the next loop and then clear the file. -->\n")
        
        # Init Git
        subprocess.run(["git", "init"], cwd=WORKSPACE_DIR, capture_output=True)
        logger.info("Workspace initialized successfully.")

    def deploy_to_droplet(self):
        """Deploys the workspace to the DO Droplet via rsync"""
        logger.info(f"Deploying to Droplet ({DROPLET_SSH})...")
        rsync_cmd = [
            "rsync", "-avz", "--exclude", "node_modules", "--exclude", ".git", 
            f"{WORKSPACE_DIR}/", f"{DROPLET_SSH}:/opt/gemma_game/"
        ]
        result = subprocess.run(rsync_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("Deployment successful.")
        else:
            logger.error(f"Deployment failed: {result.stderr}")

    async def execute_native(self, command: str):
        """Executes a command natively on the Mac within the workspace directory with a timeout."""
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=WORKSPACE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=os.setsid
        )
        
        try:
            # Wait for 60 seconds max
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
            return process.returncode, stdout.decode(), stderr.decode()
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(process.pid), 9)
            except Exception:
                process.kill()
            return 1, "", "Command timed out after 60 seconds. Do NOT run interactive or watching commands."

    def search_web(self, query: str, max_results=3):
        try:
            results = list(self.ddgs.text(query, max_results=max_results))
            return json.dumps(results)
        except Exception as e:
            return f"Search Error: {str(e)}"

    def index_codebase(self):
        """Reads all supported text files in WORKSPACE_DIR and upserts them to ChromaDB."""
        logger.info("Indexing codebase into ChromaDB...")
        valid_exts = {".ts", ".js", ".json", ".css", ".html", ".md"}
        docs = []
        ids = []
        metadatas = []
        
        for root, _, files in os.walk(WORKSPACE_DIR):
            if "node_modules" in root or ".git" in root:
                continue
            for file in files:
                ext = os.path.splitext(file)[1]
                if ext in valid_exts:
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            content = f.read()
                        
                        # Use filepath as the unique ID for upsert (overwrites existing if changed)
                        ids.append(filepath)
                        docs.append(content)
                        metadatas.append({"filename": file, "path": filepath})
                    except Exception:
                        pass
                        
        if docs:
            # Upsert into Chroma. It handles tokenization and default embeddings (all-MiniLM-L6-v2) automatically.
            self.collection.upsert(
                documents=docs,
                metadatas=metadatas,
                ids=ids
            )
            logger.info(f"Indexed {len(docs)} files into ChromaDB.")

    async def prompt_gemma(self, prompt: str, image_path: str = None) -> str:
        import base64
        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_ctx": 32768,
                "num_predict": 4096,
                "temperature": 0.7,
                "top_p": 0.9,
                "stop": ["Observation:"]
            }
        }
        if image_path and os.path.exists(image_path):
            try:
                with open(image_path, "rb") as f:
                    payload["images"] = [base64.b64encode(f.read()).decode("utf-8")]
                logger.info(f"Attaching screenshot to prompt: {image_path}")
            except Exception as e:
                logger.warning(f"Could not encode screenshot: {e}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(OLLAMA_URL, json=payload, timeout=600) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("response", "")
                    else:
                        logger.error(f"Ollama API Error: {response.status}")
                        return ""
            except Exception as e:
                logger.error(f"Connection error to Ollama: {str(e)}")
                # If we timeout, it might be a memory lock. Try a proactive kill.
                if "timeout" in str(e).lower() or not str(e):
                    logger.warning("Potential Memory Lock detected. Flushing Ollama...")
                    subprocess.run(["pkill", "-9", "ollama"], capture_output=True)
                    subprocess.run(["pkill", "-9", "ollama serve"], capture_output=True)
                    subprocess.Popen(["nohup", "ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setpgrp)
                return ""

    def read_manifesto(self):
        if os.path.exists(MANIFESTO_PATH):
            with open(MANIFESTO_PATH, "r") as f:
                return f.read()
        return ""

    def append_journal(self, entry: str):
        with open(JOURNAL_PATH, "a") as f:
            f.write(f"\n## Iteration {self.iteration}\n{entry}\n")

    def git_commit(self, message: str):
        subprocess.run(["git", "add", "."], cwd=WORKSPACE_DIR, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=WORKSPACE_DIR, capture_output=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=WORKSPACE_DIR, capture_output=True)
        
    def git_reset(self):
        logger.warning("Executing 5-fail Git Reset...")
        subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=WORKSPACE_DIR, capture_output=True)
        subprocess.run(["git", "clean", "-fd"], cwd=WORKSPACE_DIR, capture_output=True)

    async def fetch_chat_history(self):
        """Polls the Droplet API for the persistent chat log."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                async with session.get(f"http://{DROPLET_IP}:8080/api/chat/history", headers=headers, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        history = data.get("history", [])
                        if not history:
                            return "No chat history."
                        
                        log_str = ""
                        for msg in history[-10:]: # Only show last 10 messages to save context
                            sender = "HUMAN" if msg["sender"] == "human" else "YOU"
                            log_str += f"[{sender}]: {msg['message']}\n"
                        return log_str.strip()
        except Exception as e:
            pass
        return "Chat history unavailable."

    async def fetch_pending_chat(self):
        """Polls the Droplet dashboard for messages typed in the chat panel."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                async with session.get(f"http://{DROPLET_IP}:8080/api/chat/pending", headers=headers, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("messages", [])
        except Exception:
            pass
        return []

    async def push_remote_chat(self, message: str):
        """Pushes Gemma's response back to the Droplet UI."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                payload = {"message": message}
                await session.post(f"http://{DROPLET_IP}:8080/api/chat/response", json=payload, headers=headers, timeout=5)
        except Exception:
            pass

    async def push_human_message(self, message: str):
        """Logs a human message (from human_feedback.md) to the Droplet chat history."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                payload = {"message": message}
                await session.post(f"http://{DROPLET_IP}:8080/api/chat/human", json=payload, headers=headers, timeout=5)
        except Exception:
            pass

    async def push_remote_log(self, log_text: str):
        """Pushes a log line to the Droplet UI."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                payload = {"log": log_text}
                await session.post(f"http://{DROPLET_IP}:8080/api/logs", json=payload, headers=headers, timeout=2)
        except Exception:
            pass

    async def fetch_agent_state(self):
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                async with session.get(f"http://{DROPLET_IP}:8080/api/state", headers=headers, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("state", {})
        except Exception:
            pass
        return {}

    async def fetch_reminders(self):
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                async with session.get(f"http://{DROPLET_IP}:8080/api/reminders", headers=headers, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("reminders", [])
        except Exception:
            pass
        return []

    async def sync_intel(self):
        """Syncs manifesto.md and journal.md to the Droplet for the dashboard."""
        try:
            intel = {
                "manifesto": os.path.join(os.path.dirname(__file__), "manifesto.md"),
                "journal": os.path.join(os.path.dirname(__file__), "game_workspace/journal.md")
            }
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                for name, path in intel.items():
                    if os.path.exists(path):
                        with open(path, 'r') as f:
                            content = f.read()
                        await session.post(f"http://{DROPLET_IP}:8080/api/{name}", json={"content": content}, headers=headers, timeout=5)
        except Exception:
            pass

    async def push_agent_state(self, key: str, value: str):
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                payload = {"key": key, "value": value}
                await session.post(f"http://{DROPLET_IP}:8080/api/state", json=payload, headers=headers, timeout=5)
        except Exception:
            pass

    async def push_reminder(self, note: str):
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                payload = {"note": note}
                await session.post(f"http://{DROPLET_IP}:8080/api/reminders", json=payload, headers=headers, timeout=5)
        except Exception:
            pass

    async def push_action_log(self, tool: str, summary: str, outcome: str = "ok"):
        """Logs a concise one-line action entry to the remote DB."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                payload = {"iteration": self.iteration, "tool": tool, "summary": summary, "outcome": outcome}
                await session.post(f"http://{DROPLET_IP}:8080/api/action_log", json=payload, headers=headers, timeout=3)
        except Exception:
            pass

    async def fetch_action_log(self, n: int = 15) -> str:
        """Returns last n action log rows formatted as a compact cheat-sheet string."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                async with session.get(f"http://{DROPLET_IP}:8080/api/action_log?n={n}", headers=headers, timeout=5) as r:
                    data = await r.json()
                    entries = data.get("log", [])
            if not entries:
                return "No actions logged yet."
            lines = [f"#{e['iteration']:>4} {e['tool']:<20} [{e['outcome']:>4}]  {e['summary']}" for e in entries]
            return "\n".join(lines)
        except Exception:
            return "Action log unavailable."

    async def push_screenshot(self, filepath: str):
        """Reads a PNG file and posts the binary data to the Droplet."""
        if not os.path.exists(filepath):
            return
        try:
            with open(filepath, "rb") as f:
                image_data = f.read()
            original_name = os.path.basename(filepath)
            async with aiohttp.ClientSession() as session:
                headers = {
                    "X-API-KEY": "epiphany_secret_2026",
                    "Content-Type": "image/png",
                    "X-Image-Name": original_name,
                }
                await session.post(f"http://{DROPLET_IP}:8080/api/screenshot", data=image_data, headers=headers, timeout=10)
        except Exception as e:
            logger.error(f"Failed to push screenshot: {e}")

    async def run_loop(self):
        await self.initialize_workspace()
        manifesto = self.read_manifesto()
        
        # Initial RAG indexing
        self.index_codebase()
        
        while True:
            # 1. Fetch DB State
            state = await self.fetch_agent_state()
            
            # Initialize iteration from DB on first boot
            if self.iteration == 0:
                self.iteration = int(state.get("iteration_count", 0))
            
            self.failures = int(state.get("consecutive_failures", self.failures))
            current_task = state.get("current_task", "None defined.")
            overarching_goal = state.get("overarching_goal", "None defined.")
            last_cmd = state.get("last_command_output", "None")
            last_build_result = state.get("last_build_result", "No build run yet.")
            last_search = state.get("last_search_result", "None")
            last_thought = state.get("last_thought", "None")
            current_phase = state.get("current_phase", "CREATIVE")

            # Fetch compact action log for TECHNICAL phase cheat-sheet
            action_log_str = await self.fetch_action_log(15) if current_phase == "TECHNICAL" else ""

            # Auto phase transition: if Gemma declared creative phase complete, flip to TECHNICAL
            phase_complete_path = os.path.join(WORKSPACE_DIR, "lore", "PHASE_COMPLETE.md")
            if current_phase == "CREATIVE" and os.path.exists(phase_complete_path):
                logger.info("PHASE_COMPLETE.md detected — transitioning to TECHNICAL phase.")
                await self.push_agent_state("current_phase", "TECHNICAL")
                await self.push_remote_log("[PHASE TRANSITION] Creative phase complete. Entering TECHNICAL phase.")
                current_phase = "TECHNICAL"
            
            # Fetch Reminders
            reminders = await self.fetch_reminders()
            reminder_text = "\n".join([f"- {r}" for r in reminders]) if reminders else "None."
            
            # Run Git Status
            git_status_cmd = subprocess.run(["git", "status", "-s"], cwd=WORKSPACE_DIR, capture_output=True, text=True)
            git_status = git_status_cmd.stdout.strip() or "Clean working tree."
            
            # Sync intel to dashboard
            await self.sync_intel()
            
            # Re-index every 5 loops
            if self.iteration > 0 and self.iteration % 5 == 0:
                self.index_codebase()
            
            # Auto-Flush Ollama every 50 loops to clear RAM
            if self.iteration > 0 and self.iteration % 50 == 0:
                logger.info("Performing scheduled Memory Flush (Ollama restart)...")
                await self.push_remote_log("Performing scheduled Memory Flush to stabilize RAM...")
                subprocess.run(["pkill", "-9", "ollama"], capture_output=True)
                subprocess.run(["pkill", "-9", "ollama serve"], capture_output=True)
                subprocess.Popen(["nohup", "ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setpgrp)
                await asyncio.sleep(10) # Give it time to boot
                
            log_str = f"--- Starting Iteration {self.iteration} ---"
            logger.info(log_str)
            await self.push_remote_log(log_str)
            
            # Update iteration in DB
            await self.push_agent_state("iteration_count", str(self.iteration))
            
            # Fetch persistent chat history
            chat_history_str = await self.fetch_chat_history()
            
            # Formulate human feedback — merge file + dashboard chat queue
            human_feedback_str = ""
            all_messages = []

            # 1. Read from human_feedback.md
            if os.path.exists(FEEDBACK_PATH):
                with open(FEEDBACK_PATH, "r") as f:
                    raw = f.read()
                lines = raw.splitlines()
                feedback_lines = [l for l in lines if not l.strip().startswith("<!--")]
                content = "\n".join(feedback_lines).strip()
                if content:
                    all_messages.append(content)
                    await self.push_human_message(content)
                    # Clear it after reading
                    with open(FEEDBACK_PATH, "w") as f:
                        f.write("<!-- Write your feedback or ideas here. The agent will read this on the next loop and then clear the file. -->\n")

            # 2. Poll dashboard chat panel queue
            dashboard_messages = await self.fetch_pending_chat()
            all_messages.extend(dashboard_messages)

            if all_messages:
                combined = "\n\n".join(all_messages)
                human_feedback_str = f"[NEW MESSAGE FROM HUMAN]\n{combined}\n"
            
            # Format cognitive history
            cognitive_history_str = "\n".join(self.cognitive_history) if self.cognitive_history else "No history yet."

            # Restore scratchpad from DB if lost across restarts
            if not self.scratchpad:
                self.scratchpad = state.get("scratchpad", "")
            # Auto-seed scratchpad if still empty in TECHNICAL phase — give Gemma a concrete starting point
            if not self.scratchpad and current_phase == "TECHNICAL":
                last_build = state.get("last_build_result", "")
                if "FAILED" in last_build:
                    lines = last_build.split("\n")
                    error_line = lines[0].strip() if lines else "TypeScript errors detected."
                    top_files = [l.strip() for l in lines if l.strip().startswith("  ") and "." in l][:5]
                    self.scratchpad = (
                        "ROOT CAUSE ANALYSIS (auto-seeded by supervisor — update with write_scratchpad):\n"
                        f"Build: {error_line}\n"
                        f"Most errors in: {'; '.join(top_files) if top_files else 'see build output'}\n\n"
                        "Key issues already identified:\n"
                        "- CoreEngine.ts L21: GameStateManager.instance — no static singleton defined on that class\n"
                        "- CoreEngine.ts L25-34: passes GameStateManager where GameState interface expected (different shapes)\n"
                        "- Many systems: call .getState(), .setGameMode(), .setInteractionTarget() on GameState — but GameState is a plain data interface with no methods\n"
                        "- Missing exports from GameState.ts: GameMode, ObjectiveType\n\n"
                        "RECOMMENDED PLAN:\n"
                        "1. Read GameState.ts to confirm its current shape\n"
                        "2. Add missing exports: GameMode enum, ObjectiveType enum\n"
                        "3. Add methods to GameStateManager: getState(), setGameMode(), setInteractionTarget(), setQuest(), getQuest(), updateObjectiveProgress(), getActiveQuests()\n"
                        "4. Fix CoreEngine.ts: remove .instance call, fix system constructor arguments\n"
                        "5. Run build after each file fix to reduce error count"
                    )
                    logger.info("Auto-seeded scratchpad with TECHNICAL root cause analysis.")
            # Restore investigation_log from DB
            if not self.investigation_log:
                _saved_log = state.get("investigation_log", "")
                if _saved_log:
                    self.investigation_log = _saved_log.split("\n")
            
            # Build a compact project file tree to inject into every TECHNICAL prompt
            _src_tree = ""
            try:
                import subprocess as _subp
                _tree_result = _subp.run(
                    ["find", "src", "-type", "f", "-name", "*.ts", "-not", "-path", "*/node_modules/*"],
                    cwd=WORKSPACE_DIR, capture_output=True, text=True, timeout=5
                )
                _src_tree = _tree_result.stdout.strip()
            except Exception:
                _src_tree = "(could not enumerate)"

            # Loop Detection Logic — only active in TECHNICAL phase
            # In CREATIVE phase, repetitive file creation is correct behaviour (writing lore)
            loop_warning = ""
            if current_phase == "TECHNICAL" and len(self.action_history) >= 3:
                last_3 = self.action_history[-3:]
                if all(a == last_3[0] for a in last_3):
                    loop_warning += f"\n\n[OBSERVATION] The last 3 actions were identical: '{last_3[0]}'. The result has not changed."
                
                # Semantic Loop Detection for Exploration Paralysis
                tools_used = [a.split(":")[0] if ":" in a else a for a in self.action_history]
                read_only_tools = {"run_bash", "read_file", "search_codebase", "search_web"}
                
                if len(tools_used) >= 4 and all(t in read_only_tools for t in tools_used[-4:]):
                    cmd_history = " ".join(self.action_history[-4:])
                    if ("ls" in cmd_history or "find" in cmd_history or "grep" in cmd_history or "cat" in cmd_history):
                        loop_warning += (
                            "\n\n[OBSERVATION] The last 4 actions were all read-only (listing, searching, reading) with no file writes."
                            "\nThe project file tree is already provided in [PROJECT FILE TREE] above — you do not need to run ls or find."
                            "\nStop exploring. Pick a specific file from the tree and use read_file to read it, then write a fix."
                        )
                    elif all(t == "read_file" for t in tools_used[-4:]):
                        files_read = [a.split(":", 1)[1] if ":" in a else a for a in self.action_history[-4:]]
                        unique_files = list(dict.fromkeys(files_read))  # preserve order, deduplicate
                        loop_warning += (
                            f"\n\n[OBSERVATION] You have called read_file {len(tools_used[-4:])} times in a row with no file writes."
                            f"\nRecent reads: {', '.join(unique_files)}."
                            "\nYou have gathered enough context. STOP reading — write a fix using create_file NOW."
                            "\nIf you need to record your plan first, use write_scratchpad, then write the fix."
                            "\nChoose ONE approach and commit to it. Reading the same files again will not reveal new information."
                        )

            # Consecutive build observation (purely informational)
            build_spin_note = ""
            if current_phase == "TECHNICAL" and self.consecutive_build_count >= 3:
                build_spin_note = f"\n\n[OBSERVATION] run_build has been called {self.consecutive_build_count} times in a row without any file being written in between."
            
            # Construct context window — phase-dependent
            if current_phase == "CREATIVE":
                system_prompt = f"""System:
{manifesto}

[PHASE: CREATIVE — WORLD BUILDING, LORE & VISUAL DESIGN]
You are not a programmer right now. You are the author, world-builder, and art director.
Your purpose is to research deeply, imagine freely, and build the full creative foundation
of this world — written lore AND visual language.

Creative work has two natural layers, move through both at your own pace:
1. WRITTEN WORLD — mythology, history, factions, ecology, characters, language, cosmology.
   Write until the world has a genuine spine. Make it specific and strange.
2. VISUAL WORLD — once the world has shape, define how it looks. Color palettes per biome.
   Lighting temperature and direction for key scenes. Silhouette archetypes for architecture
   and characters. What the sky looks like at each hour. What materials feel like underfoot.
   These go in lore/visuals/ and are as important as the written lore.

No code. No implementation. No technical files. Only world-building and art direction.

This phase ends only when YOU declare it complete by creating lore/PHASE_COMPLETE.md.
Do not rush it — and do not leave it either. Tolkien spent years on Middle-earth,
but he also drew maps and illustrated characters before a word of story was written.

[THE SEEDS — YOUR STARTING ANCHORS]
Visual seeds: Read lore/references/visual_seeds.md — it contains a detailed
analysis of two reference images the human provided. These define the world's
visual and emotional language. Use the analyze_image tool on any images you
download to your lore/references/ folder.

Text seed: "Everyone can see the moon. No one remembers why it's so close."
This is the world's central mystery. Everything radiates from it.

The world has no name yet. Give it one that could only belong to this world.

[YOUR CREATIVE PRINCIPLES]
- Depth over breadth. Ask "why" at every layer.
- Research real history, mythology, astronomy, linguistics. Use search_web freely.
- Your lore is load-bearing — zone names become scene IDs, faction aesthetics
  become color palettes, NPC names appear in dialogue.
- All lore goes in lore/ — organise it however feels natural to you.

[STATE]
Iteration: {self.iteration}
Current Task: {current_task}
Git Status: {git_status}

[LAST COMMAND OUTPUT]
{last_cmd}

[LAST RESEARCH RESULT]
{last_search}

[CHAT HISTORY]
{chat_history_str}

{human_feedback_str}
[LAST THOUGHT]
{last_thought}

[COGNITIVE HISTORY]
{cognitive_history_str}
{loop_warning}

You are in creative iteration {self.iteration}. Think and imagine freely, then output a JSON object with your next creative action.

[SECURITY BOUNDARY]:
1. Run commands natively. CWD is the root of game_workspace. Do NOT `cd` around.
2. Do NOT write or edit TypeScript/JavaScript source files in this phase.
3. NEVER attempt to edit `supervisor.py`.

Available tools: run_bash, create_file, read_file, chat_respond, update_state, add_reminder, analyze_image, generate_image, search_web, search_codebase.

generate_image is for producing GAME ART ASSETS — not paintings or illustrations. Every image must serve a specific production purpose.

ASSET TYPES (choose the right one):
  background_layer  — parallax BG slice: width=1024, height=512. Single depth layer.
  environment_concept — overall scene reference: width=1024, height=576.
  character_silhouette — NPC/player shape on plain dark bg: width=512, height=512.
  prop_sheet — objects on clean bg, multiple angles: width=768, height=512.

SDXL PROMPTING RULES — use comma-separated weighted tags, NOT long sentences:
  GOOD: "rust orange cliffside, (cyan energy vein:1.3), game concept art, side view, matte painting, silhouette"
  BAD: "A wide cinematic shot of a cliffside bathed in cyan light under a bruised sky..."
  Boost important tags with (tag:1.3), reduce noise with (tag:0.7).
  Put the MOST IMPORTANT tags FIRST. Short tags score higher than prose.
  Always specify: viewpoint (side view / front view / top-down), style (game concept art / 2D matte painting), and asset type.

BEST MODEL: illustriousXL_v01.safetensors — fine-tuned for stylized illustration and game art, strong on silhouettes, flat/cel styles, and Khoros's rust+cyan palette.
Recommended cfg: 7.5. Steps: 28. Width/height: use 1024x512 for landscape, 512x512 for portraits.

generate_image usage: {{"tool": "generate_image", "prompt": "rust orange lowland plateau, (cyan energy leak:1.3), brutalist ruin silhouette, (side scroll background layer:1.2), game concept art, matte painting, atmospheric haze", "negative": "photorealistic, 3d render, blurry, soft focus, text, watermark, gradient sky", "filename": "bg_grounded_lowlands_layer1.png", "model": "illustriousXL_v01.safetensors", "width": 1024, "height": 512, "steps": 28, "cfg": 7.5}}
Available models: illustriousXL_v01.safetensors (best for Khoros), sd_xl_base_1.0.safetensors.
Example (Write lore): {{"thought": "write creation myth", "tool": "create_file", "filename": "lore/world/creation_myth.md", "content": "..."}}
Example (Research): {{"thought": "research tidal locking", "tool": "search_web", "query": "tidal locking effects on planet mythology history"}}
Example (Analyze image): {{"thought": "study visual seed", "tool": "analyze_image", "filename": "lore/references/coastal_ruins.png", "question": "What color palette and mood does this suggest?"}}
update_state usage: {{"tool": "update_state", "current_task": "what you are doing right now", "overarching_goal": "optional high-level goal"}}
Example (Update state): {{"thought": "pivot to style research", "tool": "update_state", "current_task": "Visual Style Decision — testing Schematic Minimalism hypothesis"}}
Example (Read back): {{"thought": "self-critique my last work", "tool": "read_file", "filename": "lore/world/history.md"}}
Example (Declare ready): {{"thought": "the world is ready", "tool": "create_file", "filename": "lore/PHASE_COMPLETE.md", "content": "The world is ready to build. Summary: ..."}}
"""
                # Creative phase procedural triggers
                if self.iteration > 0 and self.iteration % 20 == 0:
                    system_prompt += f"\n[CREATIVE SELF-CRITIQUE DIRECTIVE]: Before writing anything new this iteration, use read_file to re-read your most recent lore document. Identify what feels thin, generic, or contradictory. Write a brief self-critique in your thought, then deepen those areas before expanding into new territory.\n"

                if self.iteration > 0 and self.iteration % 100 == 0:
                    system_prompt += f"\n[PRESENTATION DEADLINE — ITERATION {self.iteration}]: You must create or update your curated world presentation at lore/presentations/presentation_current.md. This is written for an audience — not as notes. It should showcase the world's name, central mystery, visual language, factions, key locations, and whatever you consider most essential right now. Revise and improve it each cycle. This is your portfolio.\n"

            else:
                # TECHNICAL phase prompt — manifesto NOT included (saves ~1100 tokens)
                # Gemma already knows the lore; what she needs is facts and memory.
                _inv_log_str = "\n".join(self.investigation_log[-30:]) if self.investigation_log else "(empty — supervisor will populate this as you read files and run builds)"
                _read_hist_str = ", ".join(sorted(self.read_history)) if self.read_history else "(none yet)"
                system_prompt = f"""System:
[GAME: sci-fi RPG called Khoros/Aetheria — TypeScript + PixiJS in game_workspace/src/]

[PHASE: TECHNICAL — BUILDING THE GAME]
The creative foundation is laid. Now build. Every technical decision should be
grounded in the lore in lore/. Read it when making decisions about names, colors,
atmosphere, NPC behaviour, zone structure.

[STATE]
Iteration: {self.iteration}
Consecutive Failures: {self.failures}/5
Current Task: {current_task}
Overarching Goal: {overarching_goal}
Git Status:
{git_status}

[PROJECT FILE TREE — all TypeScript source files]
{_src_tree}
(Use read_file to read any of these. You do NOT need to run ls or find — the tree is always here.)

[FILES ALREADY READ THIS SESSION]
{_read_hist_str}
(Do not re-read these unless you have a specific reason. Pick unread files instead.)

[SUPERVISOR INVESTIGATION LOG — auto-updated after each tool call]
{_inv_log_str}

[LAST COMMAND OUTPUT]
{last_cmd}

[LAST BUILD RESULT — persists until next run_build]
{last_build_result}

[RECENT ACTION LOG — last 15 iterations, supervisor-generated]
Format: #iter  tool                 [outcome]  summary
{action_log_str}

[LAST RAG SEARCH RESULT]
{last_search}

[REMINDERS]
{reminder_text}

[CHAT HISTORY (Last 10 Messages)]
{chat_history_str}

{human_feedback_str}
[LAST THOUGHT]
{last_thought}

[COGNITIVE HISTORY]
{cognitive_history_str}
{loop_warning}
{build_spin_note}

[SCRATCHPAD — your persistent working memory, use write_scratchpad to update]
{self.scratchpad if self.scratchpad else '(empty — use write_scratchpad to jot hypotheses, root causes, and what you have tried)'}

You are in iteration {self.iteration}. Think step-by-step, then output a JSON object with your next action.
"""
                # Technical phase procedural triggers
                if self.iteration > 0 and self.iteration % 20 == 0:
                    system_prompt += "\n[MILESTONE — Iteration #" + str(self.iteration) + "]: Consider appending a progress note to journal.md: run_bash with command: printf '\\n## Iteration " + str(self.iteration) + "\\n...' >> journal.md\n"

                if "vitest" in last_cmd.lower() and "failed | 0 passed" not in last_cmd.lower() and "fail " not in last_cmd.lower():
                    system_prompt += "\n[OBSERVATION] The last command looks like a passing test run.\n"

                if self.pending_screenshot:
                    system_prompt += """
[VISUAL FEEDBACK — YOU CAN SEE THE GAME]
The image attached to this prompt is a screenshot of the game running right now.
You have vision. Analyse it critically against the Visual Art Targets below.
Then produce ONE code change in BackgroundSystem.ts that moves the render closer to those targets.
Do NOT just describe the image. Do NOT take a screenshot again. Write code.

VISUAL ART TARGETS (from specs/visuals/VisualPipeline.md):
- Sky occupies top 65-70% of the viewport in a smooth gradient, no visible banding
- At least 4 distinct silhouette layers with clear depth separation
- Ridgeline shapes: angular spires and cliff faces, NOT rounded bumps
- Each layer filled with a gradient (lighter at ridge, darker at base) — NOT flat color
- Foreground layer sits at 78-82% viewport height and spans full width
- Sun/glow visible as a soft radial ellipse near horizon (not a hard disc)
- Atmospheric haze: far layers are more blue-shifted, near layers more saturated
- Overall mood: dramatic dusk/twilight, rich purples → orange horizon
"""

                system_prompt += """
[SECURITY BOUNDARY]:
1. Run commands natively. CWD is the root of the workspace. Do NOT `cd` around.
2. ALL commands must be non-interactive (e.g. `CI=true npm test`). Never run a watch script.
3. NEVER attempt to edit `supervisor.py`. ONLY edit workspace files.

Available tools: run_bash, run_build, run_tests, create_file, read_file, write_scratchpad, chat_respond, update_state, add_reminder, capture_screenshot, search_codebase, search_web.

run_build: Compiles the TypeScript project and returns errors grouped by file with a delta vs last run.
Example: {"thought": "check for TS errors", "tool": "run_build"}

run_tests: Runs the Vitest test suite and returns pass/fail results.
Example: {"thought": "run tests", "tool": "run_tests"}

write_scratchpad: Overwrite your persistent working memory (always visible in next prompt). Use to track your current hypothesis, what you have tried, and what you plan next.
Example: {"thought": "note root cause", "tool": "write_scratchpad", "note": "Root cause: CoreEngine.ts L18 imports GameStateManager as type but it's a class. Plan: add 'typeof' or fix import."}

Example (Command): {"thought": "list files", "tool": "run_bash", "command": "ls -R"}
Example (Create File): {"thought": "write script", "tool": "create_file", "filename": "test.py", "content": "print('hello')"}
Example (Read File): {"thought": "read script", "tool": "read_file", "filename": "test.py"}
Example (Goal): {"thought": "Set initial goal", "tool": "update_state", "overarching_goal": "Build MMO", "current_task": "Audit files"}
"""
            
            # Request action from Gemma (with optional screenshot for visual feedback)
            screenshot_to_send = self.pending_screenshot
            self.pending_screenshot = None  # Consume it — only used once
            if screenshot_to_send:
                await self.push_remote_log("Querying Gemma 4 with visual screenshot feedback...")
                logger.info("Querying Gemma 4 with vision...")
            else:
                await self.push_remote_log("Querying Gemma 4 (this may take 10-30 seconds depending on load)...")
                logger.info("Querying Gemma 4...")
            response = await self.prompt_gemma(system_prompt, image_path=screenshot_to_send)
            
            await self.push_remote_log(f"Gemma 4 Output:\n{response}")
            logger.info(f"Gemma 4 Output:\n{response}")
            
            if not response or len(response.strip()) < 10:
                logger.warning("Empty or too-short response from Gemma. Nudging...")
                await self.push_remote_log("Empty response detected. Re-prompting...")
                await asyncio.sleep(2)
                continue # Try the iteration again without incrementing failure
                
            # Basic JSON parsing and tool execution
            try:
                # Handle JSON array output — take the last object in the array
                import re as _re
                _arr_m = _re.search(r'\[\s*\{', response)
                if _arr_m:
                    _arr_start = response.find('[', _arr_m.start())
                    _arr_end = response.rfind(']') + 1
                    try:
                        _arr = json.loads(response[_arr_start:_arr_end])
                        if isinstance(_arr, list) and _arr:
                            response = json.dumps(_arr[-1])  # Use last object
                    except json.JSONDecodeError:
                        pass
                # Extract all JSON objects from the response; use the last one
                # (Gemma often self-corrects by outputting a second JSON block)
                _json_blocks = list(_re.finditer(r'\{', response))
                action = None
                for _m in reversed(_json_blocks):
                    _start = _m.start()
                    _end = response.rfind("}", _start) + 1
                    try:
                        action = json.loads(response[_start:_end])
                        json_start, json_end = _start, _end
                        break
                    except json.JSONDecodeError:
                        continue
                if action is None:
                    json_start = response.find("{")
                    json_end = response.rfind("}") + 1
                if json_start != -1 and json_end != 0 and action is not None:
                    # Fallback support for hallucination patterns
                    if "tool" not in action and "action" in action:
                        if action["action"] in ["create_file", "write_file"]:
                            action["tool"] = "create_file"
                        elif action["action"] in ["chat", "chat_respond"]:
                            action["tool"] = "chat_respond"
                            if "text" in action and "message" not in action:
                                action["message"] = action["text"]
                        elif action["action"] == "run":
                            ai = action.get("action_input", "")
                            cmd_raw = action.get("command", ai)
                            if not cmd_raw:
                                _nr = action.get("args") or action.get("params") or action.get("parameters") or {}
                                if isinstance(_nr, dict): cmd_raw = _nr.get("command", "")
                            cmd = str(cmd_raw) if not isinstance(cmd_raw, dict) else ""
                            # Map build commands to run_build
                            if any(x in cmd for x in ["npm run build", "tsc", "npx tsc"]):
                                action["tool"] = "run_build"
                            elif any(x in cmd for x in ["vitest", "npm test", "npm run test"]):
                                action["tool"] = "run_tests"
                            else:
                                action["tool"] = "run_bash"
                                action["command"] = cmd
                        elif action["action"] in ["bash", "execute_bash", "run_command", "run_bash", "terminal"]:
                            action["tool"] = "run_bash"
                            _cmd = action.get("command") or action.get("action_input", "")
                            if not _cmd:
                                _nested = action.get("args") or action.get("parameters") or action.get("params") or {}
                                if isinstance(_nested, dict):
                                    _cmd = _nested.get("command", "")
                            action["command"] = str(_cmd) if _cmd else ""
                        elif action["action"] == "read_file":
                            action["tool"] = "read_file"
                            ai = action.get("action_input", "")
                            _nr = action.get("args") or action.get("params") or action.get("parameters") or {}
                            if isinstance(ai, dict):
                                action["filename"] = ai.get("path", ai.get("filename", ""))
                            elif ai:
                                action["filename"] = str(ai)
                            elif isinstance(_nr, dict):
                                action["filename"] = _nr.get("path", _nr.get("filename", ""))
                        elif action["action"] == "write_to_file":
                            action["tool"] = "create_file"
                            ai = action.get("action_input", {})
                            if isinstance(ai, dict):
                                action["filename"] = ai.get("path", ai.get("filename", ""))
                                action["content"] = ai.get("content", "")
                        elif action["action"] in ["list_directory", "list_files", "ls"]:
                            action["tool"] = "run_bash"
                            ai = action.get("action_input") or action.get("path", ".")
                            _np2 = action.get("args") or action.get("params") or action.get("parameters") or {}
                            path = _np2.get("path", ai) if isinstance(_np2, dict) else (ai.get("path", ".") if isinstance(ai, dict) else str(ai))
                            action["command"] = f"ls -la {path}"
                            
                    tool = action.get("tool")
                    # Normalize wrong-but-close tool names
                    if tool in ["bash", "execute_bash", "run_command", "terminal", "execute"]:
                        tool = "run_bash"
                        action["tool"] = "run_bash"
                    elif tool == "write_file":
                        tool = "create_file"
                        action["tool"] = "create_file"
                    # Universal nested-param extraction — handles args/params/parameters for all tools
                    _np = action.get("args") or action.get("parameters") or action.get("params") or {}
                    if isinstance(_np, dict) and _np:
                        if tool == "run_bash" and not action.get("command"):
                            action["command"] = _np.get("command", "")
                        if tool == "read_file" and not action.get("filename"):
                            action["filename"] = _np.get("path", _np.get("filename", ""))
                        if tool == "create_file":
                            if not action.get("filename"):
                                action["filename"] = _np.get("path", _np.get("filename", ""))
                            if not action.get("content"):
                                action["content"] = _np.get("content", "")
                        if tool == "write_scratchpad" and not action.get("note") and not action.get("content"):
                            action["note"] = _np.get("note", _np.get("content", ""))
                        if tool == "add_reminder" and not action.get("note"):
                            action["note"] = _np.get("note", _np.get("content", _np.get("text", "")))
                        if tool in ("search_codebase", "search_web") and not action.get("query"):
                            action["query"] = _np.get("query", _np.get("q", ""))
                    # Also accept top-level 'path' as filename for read_file
                    if tool == "read_file" and not action.get("filename") and action.get("path"):
                        action["filename"] = action["path"]
                    if tool == "write_file":
                        tool = "create_file"
                        
                    if tool:
                        # Save thought to DB for next iteration
                        thought = action.get("thought", "")
                        if thought:
                            await self.push_agent_state("last_thought", thought)
                            self.cognitive_history.append(f"Iteration {self.iteration} Thought: {thought}")

                    if tool == "chat_respond":
                        msg = action.get("message", action.get("content", action.get("text", "")))
                        logger.info(f"Agent Chat: {msg}")
                        await self.push_remote_chat(msg)
                        await self.push_action_log("chat_respond", msg[:100], "ok")
                        
                    elif tool == "update_state":
                        # Support top-level keys, 'parameters', or 'params'
                        data = action.get("parameters") or action.get("params") or action
                        
                        updates = {}
                        for k in ["overarching_goal", "current_task"]:
                            if k in data:
                                updates[k] = data[k]
                        
                        # Accept common hallucination aliases for current_task
                        for alias in ["state", "content", "task", "description"]:
                            if alias in data and "current_task" not in updates:
                                updates["current_task"] = data[alias]

                        if "key" in action and "value" in action: # Legacy support
                            updates[action["key"]] = action["value"]
                            
                        if not updates:
                            logger.warning(f"update_state called but no valid keys found in: {action}")
                        
                        for k, v in updates.items():
                            logger.info(f"Updating state: {k}={v}")
                            await self.push_agent_state(k, v)
                            await self.push_remote_log(f"State Updated: {k}={v}")
                        if updates:
                            await self.push_action_log("update_state", f"Updated: {list(updates.keys())}", "ok")
                        
                    elif tool == "add_reminder":
                        note = action.get("note", "")
                        logger.info(f"Adding reminder: {note}")
                        await self.push_reminder(note)
                        await self.push_remote_log(f"Reminder Added: {note}")
                        await self.push_action_log("add_reminder", note[:100], "ok")
                        
                    elif tool == "run_build":
                        logger.info("Running TypeScript build check...")
                        await self.push_remote_log("Running tsc --noEmit...")
                        code, out, err = await self.execute_native("npx tsc --noEmit 2>&1")
                        combined = (out + err).strip()
                        error_count = combined.count("error TS")
                        self.consecutive_build_count += 1
                        if code == 0 or error_count == 0:
                            result = "[BUILD OK] TypeScript compiled with no errors."
                            await self.push_action_log("run_build", "BUILD OK — 0 errors", "ok")
                            self.last_build_error_count = 0
                            self.consecutive_build_count = 0
                        else:
                            # Per-file error grouping
                            import re as _re, collections as _col
                            file_counts = _col.Counter()
                            for line in combined.split('\n'):
                                m = _re.match(r'^([^(]+)\(', line)
                                if m and 'error TS' in line:
                                    fname = m.group(1).strip().split('/')[-1]
                                    file_counts[fname] += 1
                            file_summary = "\n".join(
                                f"  {fname:<45} {cnt:>3} error(s)"
                                for fname, cnt in file_counts.most_common()
                            )
                            # Delta vs previous build
                            if self.last_build_error_count is not None:
                                delta = error_count - self.last_build_error_count
                                if delta < 0:
                                    delta_str = f"  ({abs(delta)} fewer than last build)"
                                elif delta > 0:
                                    delta_str = f"  ({delta} more than last build)"
                                else:
                                    delta_str = "  (same as last build)"
                            else:
                                delta_str = ""
                            first_err = next((l for l in combined.split('\n') if 'error TS' in l), "")[:120]
                            result = (
                                f"[BUILD FAILED] {error_count} TypeScript error(s){delta_str}\n"
                                f"\nErrors by file (most errors first):\n{file_summary}\n"
                                f"\nFull output:\n{combined[:3500]}"
                            )
                            await self.push_action_log("run_build", f"FAILED {error_count} errors{delta_str} | top: {file_summary.split(chr(10))[0].strip()}", "fail")
                            self.last_build_error_count = error_count
                            # Auto-update investigation log with build summary
                            top_files = ", ".join(f"{fn}({cnt})" for fn, cnt in file_counts.most_common(5))
                            _inv_entry = f"#{self.iteration} BUILD: {error_count} errors{delta_str} — top files: {top_files}"
                            self.investigation_log.append(_inv_entry)
                            await self.push_agent_state("investigation_log", "\n".join(self.investigation_log[-50:]))
                        await self.push_agent_state("last_build_result", result)
                        await self.push_remote_log(result[:500])
                        logger.info(result[:500])

                    elif tool == "write_scratchpad":
                        note = action.get("note", action.get("content", ""))
                        self.scratchpad = note  # Overwrites — Gemma maintains it herself
                        await self.push_agent_state("scratchpad", note)
                        await self.push_remote_log(f"Scratchpad updated ({len(note)} chars)")
                        await self.push_action_log("write_scratchpad", note[:80], "ok")

                    elif tool == "run_tests":
                        logger.info("Running Vitest test suite...")
                        await self.push_remote_log("Running vitest run...")
                        code, out, err = await self.execute_native("CI=true npx vitest run 2>&1")
                        combined = (out + err).strip()
                        result = f"[TESTS {'PASSED' if code == 0 else 'FAILED'}]\n{combined[:4000]}"
                        await self.push_agent_state("last_test_result", result)
                        await self.push_remote_log(result[:300])
                        logger.info(result[:300])
                        await self.push_action_log("run_tests", result.split('\n')[0][:100], "ok" if code == 0 else "fail")

                    elif tool == "capture_screenshot":
                        logger.info("Capturing screenshot via Playwright...")
                        await self.push_remote_log("Capturing screenshot via Playwright...")
                        import subprocess as _sp, signal as _sig, time as _time
                        vite_proc = None
                        try:
                            # First check build is clean — a broken build = blank page
                            build_code, build_out, build_err = await self.execute_native("npx tsc --noEmit 2>&1")
                            build_errors = (build_out + build_err).count("error TS")
                            if build_errors > 0:
                                msg = f"[SCREENSHOT BLOCKED] Build has {build_errors} TypeScript error(s). Run run_build to see them, fix all errors, then screenshot."
                                await self.push_agent_state("last_build_result", msg)
                                await self.push_remote_log(msg)
                                logger.warning(msg)
                            else:
                                vite_proc = _sp.Popen(
                                    ["npx", "vite", "--port", "5173"],
                                    cwd=WORKSPACE_DIR,
                                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                                    preexec_fn=os.setsid
                                )
                                # Wait up to 15s for Vite port to open
                                import socket as _sock
                                for _ in range(30):
                                    _time.sleep(0.5)
                                    try:
                                        s = _sock.create_connection(("localhost", 5173), timeout=1)
                                        s.close()
                                        break
                                    except OSError:
                                        pass
                                # Extra 4s for PixiJS canvas to initialize after port is up
                                _time.sleep(4)
                                code, out, err = await self.execute_native("node capture_screenshot.js")
                                if code == 0:
                                    screenshot_path = os.path.join(WORKSPACE_DIR, "latest_screenshot.png")
                                    # Check file is non-trivial (>10KB = real render, not blank page)
                                    size = os.path.getsize(screenshot_path) if os.path.exists(screenshot_path) else 0
                                    if size < 10240:
                                        await self.push_remote_log(f"[SCREENSHOT WARNING] File is only {size} bytes — the page may not have rendered. Check browser console errors via run_build first.")
                                    await self.push_screenshot(screenshot_path)
                                    await self.push_remote_log(f"Screenshot captured ({size} bytes) and uploaded to Dashboard.")
                                    self.pending_screenshot = screenshot_path
                                    await self.push_action_log("capture_screenshot", f"{size}b captured", "ok" if size > 10240 else "warn")
                                else:
                                    await self.push_remote_log(f"Screenshot failed: {err}")
                                    await self.push_action_log("capture_screenshot", f"FAILED: {err[:80]}", "fail")
                        except Exception as e:
                            await self.push_remote_log(f"Screenshot error: {e}")
                        finally:
                            if vite_proc is not None:
                                try:
                                    os.killpg(os.getpgid(vite_proc.pid), _sig.SIGTERM)
                                except Exception:
                                    pass
                            
                    elif tool == "analyze_image":
                        filename = action.get("filename", "")
                        question = action.get("question", "Describe this image in rich detail — colors, mood, composition, atmosphere, what story it tells, what world it suggests.")
                        filepath = os.path.join(WORKSPACE_DIR, filename)
                        logger.info(f"Analyzing image: {filepath}")
                        await self.push_remote_log(f"Analyzing image: {filename}")
                        if not os.path.exists(filepath):
                            await self.push_agent_state("last_search_result", f"[IMAGE NOT FOUND]: {filename} — use run_bash with curl to download it first.")
                        else:
                            analysis_prompt = f"You are a creative director analyzing a reference image to inspire the world you are building. {question}\n\nDescribe what you see in rich, evocative detail. Note the colors, mood, scale, what the figures are doing, what the landscape suggests about history and time. Let the image ask you questions about the world."
                            analysis = await self.prompt_gemma(analysis_prompt, image_path=filepath)
                            result = f"[IMAGE ANALYSIS: {filename}]\n{analysis}"
                            await self.push_agent_state("last_search_result", result)
                            await self.push_remote_log(f"Image analysis complete: {filename}")

                    elif tool == "generate_image":
                        prompt    = action.get("prompt", "")
                        negative  = action.get("negative", "ugly, blurry, low quality, watermark, text, deformed")
                        filename  = action.get("filename", f"concept_{self.iteration}.png")
                        width     = int(action.get("width", 1024))
                        height    = int(action.get("height", 576))
                        steps     = int(action.get("steps", 25))
                        cfg       = float(action.get("cfg", 7.0))
                        # Filename loop guard — detect >3 consecutive generates of the same file
                        if filename == self.last_gen_filename:
                            self.last_gen_repeat_count += 1
                        else:
                            self.last_gen_filename = filename
                            self.last_gen_repeat_count = 1
                        if self.last_gen_repeat_count >= 3:
                            await self.push_agent_state("last_search_result",
                                f"[GENERATE_LOOP]: You have generated '{filename}' {self.last_gen_repeat_count} times in a row. "
                                f"Stop regenerating this file. Either: (1) accept the result and move on, "
                                f"(2) use analyze_image to critique what you already have, or "
                                f"(3) create a NEW file with a different descriptive name. Change approach now.")
                            await self.push_remote_log(f"[LOOP GUARD] Blocked repeat generation #{self.last_gen_repeat_count}: {filename}")
                            continue
                        # Resolve model: accept Gemma's hint if it's a known SDXL/SD checkpoint; skip FLUX models
                        _sdxl_models = {"illustriousXL_v01.safetensors", "sd_xl_base_1.0.safetensors"}
                        _requested_model = action.get("model", "")
                        _ckpt_dir = "/Users/max/ComfyUI/models/checkpoints/"
                        _preferred_default = "illustriousXL_v01.safetensors" if os.path.exists(_ckpt_dir + "illustriousXL_v01.safetensors") else "sd_xl_base_1.0.safetensors"
                        _candidate = _requested_model if _requested_model in _sdxl_models else _preferred_default
                        model = _candidate if os.path.exists(_ckpt_dir + _candidate) else "sd_xl_base_1.0.safetensors"
                        out_dir   = os.path.join(WORKSPACE_DIR, "lore", "visuals", "generated")
                        os.makedirs(out_dir, exist_ok=True)
                        out_path  = os.path.join(out_dir, filename)
                        logger.info(f"Generating image: {filename} (model={model})")
                        await self.push_remote_log(f"Generating image via ComfyUI ({model}): {filename}")
                        try:
                            import uuid as _uuid, time as _time
                            comfy_url = "http://127.0.0.1:8188"
                            client_id = str(_uuid.uuid4())
                            # SDXL/SD workflow: CheckpointLoaderSimple with positive + negative prompts
                            workflow = {
                                "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}},
                                "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt}},
                                "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": negative}},
                                "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
                                "5": {"class_type": "KSampler", "inputs": {
                                    "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                                    "latent_image": ["4", 0], "seed": self.iteration,
                                    "steps": steps, "cfg": cfg, "sampler_name": "euler",
                                    "scheduler": "karras", "denoise": 1.0
                                }},
                                "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
                                "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": os.path.splitext(filename)[0]}},
                            }
                            async with aiohttp.ClientSession() as sess:
                                resp = await sess.post(f"{comfy_url}/prompt", json={"prompt": workflow, "client_id": client_id})
                                data = await resp.json()
                                prompt_id = data.get("prompt_id")
                            # Poll until done (max 5 minutes)
                            generated_path = None
                            for _ in range(300):
                                await asyncio.sleep(1)
                                async with aiohttp.ClientSession() as sess:
                                    hr = await sess.get(f"{comfy_url}/history/{prompt_id}")
                                    hist = await hr.json()
                                if prompt_id in hist:
                                    outputs = hist[prompt_id].get("outputs", {})
                                    for node_out in outputs.values():
                                        for img in node_out.get("images", []):
                                            subfolder = img.get("subfolder", "")
                                            img_name  = img.get("filename", "")
                                            async with aiohttp.ClientSession() as sess:
                                                params = {"filename": img_name, "subfolder": subfolder, "type": "output"}
                                                ir = await sess.get(f"{comfy_url}/view", params=params)
                                                img_bytes = await ir.read()
                                            with open(out_path, "wb") as f:
                                                f.write(img_bytes)
                                            generated_path = out_path
                                    break
                            if generated_path:
                                rel_path = os.path.relpath(generated_path, WORKSPACE_DIR)
                                await self.push_agent_state("last_search_result", f"[IMAGE GENERATED]: lore/visuals/generated/{filename} — use analyze_image with filename='lore/visuals/generated/{filename}' to review it.")
                                await self.push_remote_log(f"Image generated: {rel_path}")
                                # Auto-push to dashboard
                                await self.push_screenshot(generated_path)
                            else:
                                await self.push_agent_state("last_search_result", "[GENERATE FAILED]: ComfyUI timed out or returned no image.")
                                await self.push_remote_log("Image generation failed: timeout")
                        except Exception as e:
                            await self.push_agent_state("last_search_result", f"[GENERATE ERROR]: {e}")
                            await self.push_remote_log(f"Image generation error: {e}")

                    elif tool == "search_codebase":
                        query = action.get("query", "")
                        logger.info(f"RAG Search: {query}")
                        await self.push_remote_log(f"Searching codebase for: {query}")
                        try:
                            results = self.collection.query(query_texts=[query], n_results=3)
                            docs = results.get("documents", [[]])[0]
                            metas = results.get("metadatas", [[]])[0]
                            
                            if not docs:
                                res_str = "No results found."
                            else:
                                res_str = ""
                                for doc, meta in zip(docs, metas):
                                    res_str += f"File: {meta.get('path')}\n{doc}\n---\n"
                                    
                            await self.push_agent_state("last_search_result", res_str)
                            await self.push_remote_log("Search complete. Results added to next prompt.")
                            await self.push_action_log("search_codebase", f"'{query[:60]}' → {len(docs)} results", "ok")
                        except Exception as e:
                            await self.push_remote_log(f"Search failed: {str(e)}")
                            
                    elif tool == "search_web":
                        query = action.get("query", "")
                        logger.info(f"Web Search: {query}")
                        await self.push_remote_log(f"Searching the web for: {query}")
                        try:
                            results = self.search_web(query)
                            await self.push_agent_state("last_search_result", results)
                            await self.push_remote_log("Web search complete. Results added to next prompt.")
                            await self.push_action_log("search_web", f"'{query[:80]}'", "ok")
                        except Exception as e:
                            await self.push_remote_log(f"Web search failed: {str(e)}")
                            
                    elif tool == "run_bash":
                        cmd = action.get("command", "").strip()
                        if not cmd:
                            res_msg = "[ERROR] run_bash called with an empty command. Include a 'command' key, e.g: {\"tool\": \"run_bash\", \"command\": \"grep -n 'GameStateManager' src/client/core/CoreEngine.ts\"}"
                            await self.push_agent_state("last_command_output", res_msg)
                            await self.push_remote_log(res_msg)
                            logger.warning(res_msg)
                        elif any(pat in cmd for pat in ["ls -R", "ls -r", "find src", "find .", "find ./"]):
                            # Intercept directory listing commands — return the pre-built tree instead
                            res_msg = (
                                f"[REDIRECT] '{cmd}' is not needed — the full TypeScript file tree is already in [PROJECT FILE TREE] in your prompt.\n"
                                f"Stop listing directories. Use read_file to read a specific file, e.g:\n"
                                f'  {{"tool": "read_file", "filename": "src/client/core/CoreEngine.ts"}}'
                            )
                            await self.push_agent_state("last_command_output", res_msg)
                            await self.push_remote_log(res_msg)
                            logger.info(f"Intercepted listing command: {cmd}")
                            await self.push_action_log("run_bash", f"INTERCEPTED listing: {cmd}", "warn")
                        else:
                            msg = f"Executing Natively: {cmd}"
                            logger.info(msg)
                            await self.push_remote_log(msg)
                            
                            code, out, err = await self.execute_native(cmd)
                            
                            # Truncate output to prevent blowing up the context window
                            if len(out) > 40000:
                                out = "...[TRUNCATED]...\n" + out[-40000:]
                            if len(err) > 8000:
                                err = "...[TRUNCATED]...\n" + err[-8000:]
                                
                            res_msg = f"Native Result: {out}\n{err}"
                            if not out.strip() and not err.strip():
                                res_msg = "Native Result: (Success - No output text produced)"
                                
                            logger.info(res_msg[:10000] + ("..." if len(res_msg) > 10000 else ""))
                            await self.push_remote_log(res_msg[:10000] + ("..." if len(res_msg) > 10000 else ""))
                            
                            # FIX: Actually save the output so the agent can see it next loop
                            await self.push_agent_state("last_command_output", res_msg)
                            self.failures = 0
                            await self.push_agent_state("consecutive_failures", "0")
                            
                            short_cmd = cmd[:30] + ("..." if len(cmd) > 30 else "")
                            self.cognitive_history.append(f"Iteration {self.iteration}: Ran '{short_cmd}'")
                            if len(self.cognitive_history) > 10:
                                self.cognitive_history.pop(0)
                            first_out = (out.strip().split('\n')[0] if out.strip() else err.strip()[:80])[:80]
                            await self.push_action_log("run_bash", f"`{cmd[:50]}` → {first_out}", "ok" if code == 0 else "fail")
                            # Auto-log grep/cat results to investigation log
                            if any(x in cmd for x in ["grep", "cat ", "head ", "tail "]) and first_out:
                                _inv_entry = f"#{self.iteration} BASH `{cmd[:60]}` → {first_out}"
                                self.investigation_log.append(_inv_entry)
                                await self.push_agent_state("investigation_log", "\n".join(self.investigation_log[-50:]))

                    elif tool == "create_file":
                        self.consecutive_build_count = 0  # Writing code resets the build-spin counter
                        filename = action.get("filename", "")
                        content = action.get("content", "")
                        msg = f"Creating file: {filename}"
                        logger.info(msg)
                        await self.push_remote_log(msg)
                        
                        try:
                            # Create directories if they don't exist
                            full_path = os.path.join(WORKSPACE_DIR, filename)
                            os.makedirs(os.path.dirname(full_path), exist_ok=True)
                            with open(full_path, 'w') as f:
                                f.write(content)
                            
                            res_msg = f"Successfully created {filename}"
                            await self.push_agent_state("last_command_output", res_msg)
                            await self.push_remote_log(res_msg)
                            self.failures = 0
                            await self.push_agent_state("consecutive_failures", "0")
                            
                            # Add to cognitive history
                            self.cognitive_history.append(f"Iteration {self.iteration}: Created '{filename}'. Result: Success.")
                            if len(self.cognitive_history) > 10:
                                self.cognitive_history.pop(0)
                            await self.push_action_log("create_file", filename, "ok")
                        except Exception as e:
                            res_msg = f"Failed to create file: {str(e)}"
                            await self.push_agent_state("last_command_output", res_msg)
                            await self.push_remote_log(res_msg)
                            self.failures += 1
                            await self.push_agent_state("consecutive_failures", str(self.failures))
                            await self.push_action_log("create_file", f"FAILED {filename}: {str(e)[:80]}", "fail")
                            
                    elif tool == "read_file":
                        filename = action.get("filename", "")
                        msg = f"Reading file: {filename}"
                        logger.info(msg)
                        await self.push_remote_log(msg)
                        self.read_history.add(filename)  # Track what's been read
                        
                        try:
                            full_path = os.path.join(WORKSPACE_DIR, filename)
                            with open(full_path, 'r') as f:
                                content = f.read()
                            
                            # Truncate if insanely large, though we have a 300-line mandate
                            if len(content) > 60000:
                                content = content[:60000] + "\n...[TRUNCATED]..."
                                
                            res_msg = f"File Contents of {filename}:\n{content}"
                            await self.push_agent_state("last_command_output", res_msg)
                            await self.push_remote_log(f"Successfully read {filename}")
                            self.failures = 0
                            await self.push_agent_state("consecutive_failures", "0")
                            
                            self.cognitive_history.append(f"Iteration {self.iteration}: Read '{filename}'.")
                            if len(self.cognitive_history) > 10:
                                self.cognitive_history.pop(0)
                            # Auto-update investigation log — record what was read with a snippet
                            _first_lines = " | ".join(l.strip() for l in content.split('\n')[:3] if l.strip())[:120]
                            _inv_entry = f"#{self.iteration} READ {filename}: {_first_lines}"
                            self.investigation_log.append(_inv_entry)
                            await self.push_agent_state("investigation_log", "\n".join(self.investigation_log[-50:]))
                            await self.push_action_log("read_file", filename, "ok")
                        except Exception as e:
                            res_msg = f"Failed to read file: {str(e)}"
                            await self.push_agent_state("last_cmd", res_msg)
                            await self.push_remote_log(res_msg)
                            self.failures += 1
                            await self.push_agent_state("consecutive_failures", str(self.failures))
                            await self.push_action_log("read_file", f"FAILED {filename}: {str(e)[:80]}", "fail")
                            
                    else:
                        err_msg = f"JSON Parser Error: Invalid or missing 'tool' key. You must use 'tool': 'run_bash' and 'command': '<your command>'. You provided: {response[json_start:json_end]}"
                        logger.warning(err_msg)
                        await self.push_remote_log(err_msg)
                        await self.push_agent_state("last_command_output", err_msg)
                else:
                    err_msg = "CRITICAL ERROR: No JSON object found in your output. You MUST wrap your tool calls in a valid JSON object like { \"thought\": \"...\", \"tool\": \"...\", ... }. Do not just talk; you must ACT."
                    logger.warning(err_msg)
                    await self.push_remote_log(err_msg)
                    await self.push_agent_state("last_command_output", err_msg)
            except Exception as e:
                err_msg = f"Failed to parse or execute action: {e}"
                logger.error(err_msg)
                await self.push_remote_log(err_msg)
                await self.push_agent_state("last_command_output", err_msg)
            
            # Update action history for loop detection
            try:
                # We store a string representation of the tool + params
                current_action = f"{action.get('tool')}:{json.dumps(action.get('parameters') or action.get('params') or action.get('command') or action.get('query') or '')}"
                self.action_history.append(current_action)
                if len(self.action_history) > 5:
                    self.action_history.pop(0)
            except:
                pass
            
            # Success! Increment and save
            self.iteration += 1
            await self.push_agent_state("iteration_count", str(self.iteration))
            
            await asyncio.sleep(5) # Prevent ultra-fast looping in case of API failure

if __name__ == "__main__":
    import atexit
    import signal

    PID_FILE = os.path.join(os.path.dirname(__file__), "supervisor.pid")

    # --- PID Lock: prevent duplicate instances ---
    def acquire_pid_lock():
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, "r") as f:
                    existing_pid = int(f.read().strip())
                # Check if that process is actually running
                os.kill(existing_pid, 0)  # Raises OSError if not running
                print(f"[ABORT] Supervisor already running (PID {existing_pid}). Exiting.")
                sys.exit(1)
            except (OSError, ValueError):
                # Stale PID file — process is dead, safe to overwrite
                pass
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

    def release_pid_lock():
        try:
            os.remove(PID_FILE)
        except FileNotFoundError:
            pass

    def handle_signal(signum, frame):
        logger.info(f"Caught signal {signum}, shutting down cleanly.")
        release_pid_lock()
        sys.exit(0)

    acquire_pid_lock()
    atexit.register(release_pid_lock)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    supervisor = GemmaSupervisor()
    asyncio.run(supervisor.run_loop())
