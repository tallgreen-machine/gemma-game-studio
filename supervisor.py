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
                "num_ctx": 16384,
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

    async def push_remote_chat(self, message: str):
        """Pushes Gemma's response back to the Droplet UI."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": "epiphany_secret_2026"}
                payload = {"message": message}
                await session.post(f"http://{DROPLET_IP}:8080/api/chat/response", json=payload, headers=headers, timeout=5)
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

    async def push_screenshot(self, filepath: str):
        """Reads a PNG file and posts the binary data to the Droplet."""
        if not os.path.exists(filepath):
            return
        try:
            with open(filepath, "rb") as f:
                image_data = f.read()
            async with aiohttp.ClientSession() as session:
                headers = {
                    "X-API-KEY": "epiphany_secret_2026",
                    "Content-Type": "image/png"
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
            last_search = state.get("last_search_result", "None")
            last_thought = state.get("last_thought", "None")
            current_phase = state.get("current_phase", "CREATIVE")

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
            
            # Formulate human feedback
            human_feedback_str = ""
            if os.path.exists(FEEDBACK_PATH):
                with open(FEEDBACK_PATH, "r") as f:
                    content = f.read().strip()
                if content and not content.startswith("<!-- Write your feedback"):
                    human_feedback_str = f"[NEW MESSAGE FROM HUMAN]\n{content}\n[MANDATORY]: You MUST respond to this message using the 'chat_respond' tool THIS iteration before doing anything else. Do not run tests, write code, or take any other action first.\n"
                    # Clear it after reading
                    with open(FEEDBACK_PATH, "w") as f:
                        f.write("<!-- Write your feedback or ideas here. The agent will read this on the next loop and then clear the file. -->\n")
            
            # Format cognitive history
            cognitive_history_str = "\n".join(self.cognitive_history) if self.cognitive_history else "No history yet."
            
            # Loop Detection Logic
            loop_warning = ""
            if len(self.action_history) >= 3:
                last_3 = self.action_history[-3:]
                if all(a == last_3[0] for a in last_3):
                    loop_warning += f"\n\n[CRITICAL LOOP DETECTED]\nYou have performed the exact same action '{last_3[0]}' three times in a row and are failing. YOU ARE STUCK. Do NOT repeat this exact action. Instead of abandoning the task, gather new context to diagnose WHY it is failing. For example, use `run_bash` to run `ls -la` and check if your file paths are correct, use `read_file` to verify syntax, or use `search_web`. Identify the root cause and fix it before trying again."
                
                # Semantic Loop Detection for Exploration Paralysis
                tools_used = [a.split(":")[0] if ":" in a else a for a in self.action_history]
                read_only_tools = {"run_bash", "read_file", "search_codebase", "search_web"}
                
                if len(tools_used) >= 4 and all(t in read_only_tools for t in tools_used[-4:]):
                    cmd_history = " ".join(self.action_history[-4:])
                    if ("ls" in cmd_history or "find" in cmd_history or "grep" in cmd_history or "cat" in cmd_history):
                        loop_warning += "\n\n[EXPLORATION PARALYSIS DETECTED]\nYou are repeatedly listing files, searching, or exploring the filesystem without making modifications. STOP EXPLORING. You must formulate a fix, write the code, and use 'create_file' to submit your progress immediately."
            
            # Construct context window — phase-dependent
            if current_phase == "CREATIVE":
                system_prompt = f"""System:
{manifesto}

[PHASE: CREATIVE — WORLD BUILDING & LORE]
You are not a programmer right now. You are the author and creator of this world.
Your purpose is to research deeply, imagine freely, and write the foundational lore,
mythology, aesthetics, factions, characters, and history of the game world.
No code. No tests. Only world-building.

This phase ends only when YOU declare it complete by creating lore/PHASE_COMPLETE.md.
Do not rush it. The world must feel genuinely deep, specific, and alive before it
is built. Tolkien spent years on Middle-earth before writing a single scene.

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

Available tools: run_bash, create_file, read_file, chat_respond, update_state, add_reminder, analyze_image, search_web, search_codebase.
Example (Write lore): {{"thought": "write creation myth", "tool": "create_file", "filename": "lore/world/creation_myth.md", "content": "..."}}
Example (Research): {{"thought": "research tidal locking", "tool": "search_web", "query": "tidal locking effects on planet mythology history"}}
Example (Analyze image): {{"thought": "study visual seed", "tool": "analyze_image", "filename": "lore/references/coastal_ruins.png", "question": "What color palette and mood does this suggest?"}}
Example (Read back): {{"thought": "self-critique my last work", "tool": "read_file", "filename": "lore/world/history.md"}}
Example (Declare ready): {{"thought": "the world is ready", "tool": "create_file", "filename": "lore/PHASE_COMPLETE.md", "content": "The world is ready to build. Summary: ..."}}
"""
                # Creative phase procedural triggers
                if self.iteration > 0 and self.iteration % 20 == 0:
                    system_prompt += f"\n[CREATIVE SELF-CRITIQUE DIRECTIVE]: Before writing anything new this iteration, use read_file to re-read your most recent lore document. Identify what feels thin, generic, or contradictory. Write a brief self-critique in your thought, then deepen those areas before expanding into new territory.\n"

                if self.iteration > 0 and self.iteration % 100 == 0:
                    system_prompt += f"\n[PRESENTATION DEADLINE — ITERATION {self.iteration}]: You must create or update your curated world presentation at lore/presentations/presentation_current.md. This is written for an audience — not as notes. It should showcase the world's name, central mystery, visual language, factions, key locations, and whatever you consider most essential right now. Revise and improve it each cycle. This is your portfolio.\n"

            else:
                # TECHNICAL phase prompt
                system_prompt = f"""System:
{manifesto}

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

[LAST COMMAND OUTPUT]
{last_cmd}

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

You are in iteration {self.iteration}. Think step-by-step, then output a JSON object with your next action.

[ACTION MANDATE]: Stop over-analyzing. If you have read a specification or file once, DO NOT read it again. Trust your memory and take action. Bias heavily towards creating files, writing code, and running tests rather than endless reading.
"""
                # Technical phase procedural triggers
                if self.iteration > 0 and self.iteration % 20 == 0:
                    system_prompt += "\n[MANDATORY SYSTEM DIRECTIVE]: This is a milestone iteration (#" + str(self.iteration) + "). You MUST use the `run_bash` tool to APPEND (not overwrite) to `journal.md` with your current progress and next steps. Use: run_bash with command: printf '\\n## Iteration X\\n...' >> journal.md — never use create_file for journal.md.\n"

                if "vitest" in last_cmd.lower() and "failed | 0 passed" not in last_cmd.lower() and "fail " not in last_cmd.lower():
                    system_prompt += "\n[MANDATORY SYSTEM DIRECTIVE]: It looks like you just had a successful test run! Consider using `capture_screenshot` to verify visuals, or `run_bash` to back up your changes with `git commit`.\n"

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

Available tools: run_bash, create_file, read_file, chat_respond, update_state, add_reminder, capture_screenshot, search_codebase, search_web.
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
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                if json_start != -1 and json_end != 0:
                    action = json.loads(response[json_start:json_end])
                    # Fallback support for hallucination patterns
                    if "tool" not in action and "action" in action:
                        if action["action"] in ["create_file", "write_file"]:
                            action["tool"] = "create_file"
                        elif action["action"] in ["chat", "chat_respond"]:
                            action["tool"] = "chat_respond"
                            if "text" in action and "message" not in action:
                                action["message"] = action["text"]
                            
                    tool = action.get("tool")
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
                        
                    elif tool == "update_state":
                        # Support top-level keys, 'parameters', or 'params'
                        data = action.get("parameters") or action.get("params") or action
                        
                        updates = {}
                        for k in ["overarching_goal", "current_task"]:
                            if k in data:
                                updates[k] = data[k]
                        
                        if "key" in action and "value" in action: # Legacy support
                            updates[action["key"]] = action["value"]
                            
                        if not updates:
                            logger.warning(f"update_state called but no valid keys found in: {action}")
                        
                        for k, v in updates.items():
                            logger.info(f"Updating state: {k}={v}")
                            await self.push_agent_state(k, v)
                            await self.push_remote_log(f"State Updated: {k}={v}")
                        
                    elif tool == "add_reminder":
                        note = action.get("note", "")
                        logger.info(f"Adding reminder: {note}")
                        await self.push_reminder(note)
                        await self.push_remote_log(f"Reminder Added: {note}")
                        
                    elif tool == "capture_screenshot":
                        logger.info("Capturing screenshot via Playwright...")
                        await self.push_remote_log("Capturing screenshot via Playwright...")
                        # Start Vite dev server temporarily, wait for it to be ready, then screenshot
                        import subprocess as _sp, signal as _sig, time as _time
                        vite_proc = None
                        screenshot_success = False
                        try:
                            vite_proc = _sp.Popen(
                                ["npx", "vite", "--port", "5173"],
                                cwd=WORKSPACE_DIR,
                                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                                preexec_fn=os.setsid
                            )
                            # Wait up to 10s for Vite to be ready
                            import socket as _sock
                            for _ in range(20):
                                _time.sleep(0.5)
                                try:
                                    s = _sock.create_connection(("localhost", 5173), timeout=1)
                                    s.close()
                                    break
                                except OSError:
                                    pass
                            code, out, err = await self.execute_native("node capture_screenshot.js")
                            if code == 0:
                                screenshot_path = os.path.join(WORKSPACE_DIR, "latest_screenshot.png")
                                await self.push_screenshot(screenshot_path)
                                await self.push_remote_log("Screenshot uploaded to Dashboard.")
                                self.pending_screenshot = screenshot_path  # Feed to vision on next loop
                                screenshot_success = True
                            else:
                                await self.push_remote_log(f"Screenshot failed: {err}")
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
                        except Exception as e:
                            await self.push_remote_log(f"Web search failed: {str(e)}")
                            
                    elif tool == "run_bash":
                        cmd = action.get("command", "")
                        msg = f"Executing Natively: {cmd}"
                        logger.info(msg)
                        await self.push_remote_log(msg)
                        
                        code, out, err = await self.execute_native(cmd)
                        
                        # Truncate output to prevent blowing up the context window (Ollama limit/speed)
                        if len(out) > 10000:
                            out = "...[TRUNCATED]...\n" + out[-10000:]
                        if len(err) > 2000:
                            err = "...[TRUNCATED]...\n" + err[-2000:]
                            
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
                        
                    elif tool == "create_file":
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
                        except Exception as e:
                            res_msg = f"Failed to create file: {str(e)}"
                            await self.push_agent_state("last_command_output", res_msg)
                            await self.push_remote_log(res_msg)
                            self.failures += 1
                            await self.push_agent_state("consecutive_failures", str(self.failures))
                            
                    elif tool == "read_file":
                        filename = action.get("filename", "")
                        msg = f"Reading file: {filename}"
                        logger.info(msg)
                        await self.push_remote_log(msg)
                        
                        try:
                            full_path = os.path.join(WORKSPACE_DIR, filename)
                            with open(full_path, 'r') as f:
                                content = f.read()
                            
                            # Truncate if insanely large, though we have a 300-line mandate
                            if len(content) > 15000:
                                content = content[:15000] + "\n...[TRUNCATED]..."
                                
                            res_msg = f"File Contents of {filename}:\n{content}"
                            await self.push_agent_state("last_command_output", res_msg)
                            await self.push_remote_log(f"Successfully read {filename}")
                            self.failures = 0
                            await self.push_agent_state("consecutive_failures", "0")
                            
                            self.cognitive_history.append(f"Iteration {self.iteration}: Read '{filename}'.")
                            if len(self.cognitive_history) > 10:
                                self.cognitive_history.pop(0)
                        except Exception as e:
                            res_msg = f"Failed to read file: {str(e)}"
                            await self.push_agent_state("last_cmd", res_msg)
                            await self.push_remote_log(res_msg)
                            self.failures += 1
                            await self.push_agent_state("consecutive_failures", str(self.failures))
                            
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
    supervisor = GemmaSupervisor()
    asyncio.run(supervisor.run_loop())
