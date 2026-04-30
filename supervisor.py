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
        """Executes a command natively on the Mac within the workspace directory."""
        # Using shell=True so node/npm commands work from PATH
        result = subprocess.run(command, shell=True, cwd=WORKSPACE_DIR, capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr

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

    async def prompt_gemma(self, prompt: str) -> str:
        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(OLLAMA_URL, json=payload, timeout=300) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("response", "")
                    else:
                        logger.error(f"Ollama API Error: {response.status}")
                        return ""
            except Exception as e:
                logger.error(f"Connection error to Ollama: {str(e)}")
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
            
            # Initialize iteration from DB on first boot, then increment manually
            if self.iteration == 0:
                self.iteration = int(state.get("iteration_count", 0))
            self.iteration += 1
            
            self.failures = int(state.get("consecutive_failures", self.failures))
            current_task = state.get("current_task", "None defined.")
            overarching_goal = state.get("overarching_goal", "None defined.")
            last_cmd = state.get("last_command_output", "None")
            last_search = state.get("last_search_result", "None")
            last_thought = state.get("last_thought", "None")
            
            # Fetch Reminders
            reminders = await self.fetch_reminders()
            reminder_text = "\n".join([f"- {r}" for r in reminders]) if reminders else "None."
            
            # Run Git Status
            git_status_cmd = subprocess.run(["git", "status", "-s"], cwd=WORKSPACE_DIR, capture_output=True, text=True)
            git_status = git_status_cmd.stdout.strip() or "Clean working tree."
            
            # Re-index every 5 loops
            if self.iteration > 0 and self.iteration % 5 == 0:
                self.index_codebase()
                
            log_str = f"--- Starting Iteration {self.iteration} ---"
            logger.info(log_str)
            await self.push_remote_log(log_str)
            
            # Update iteration in DB
            await self.push_agent_state("iteration_count", str(self.iteration))
            
            # Fetch persistent chat history
            chat_history_str = await self.fetch_chat_history()
            
            # Format cognitive history
            cognitive_history_str = "\n".join(self.cognitive_history) if self.cognitive_history else "No history yet."
            
            # Loop Detection Logic
            loop_warning = ""
            if len(self.action_history) >= 3:
                last_3 = self.action_history[-3:]
                if all(a == last_3[0] for a in last_3):
                    loop_warning = f"\n\n[CRITICAL LOOP DETECTED]\nYou have performed the action '{last_3[0]}' three times in a row. YOU ARE STUCK. Do NOT repeat this action. You must pivot your strategy, search the web, or audit a different part of the codebase to break the loop."
            
            # Construct context window
            system_prompt = f"""System:
{manifesto}

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

[LAST THOUGHT]
{last_thought}

[COGNITIVE HISTORY]
{cognitive_history_str}
{loop_warning}

You are in iteration {self.iteration}. Think step-by-step, then output a JSON object with your next action. 
Available tools: run_bash, chat_respond, update_state, add_reminder, capture_screenshot, search_codebase.
Example (Command): {{"thought": "list files", "tool": "run_bash", "command": "ls -R"}}
Example (Goal): {{"thought": "Set initial goal", "tool": "update_state", "overarching_goal": "Build MMO", "current_task": "Audit files"}}
"""
            
            # Request action from Gemma
            await self.push_remote_log("Querying Gemma 4 (this may take 10-30 seconds depending on load)...")
            logger.info("Querying Gemma 4...")
            response = await self.prompt_gemma(system_prompt)
            
            await self.push_remote_log(f"Gemma 4 Output:\n{response}")
            logger.info(f"Gemma 4 Output:\n{response}")
            
            # Basic JSON parsing and tool execution
            try:
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                if json_start != -1 and json_end != 0:
                    action = json.loads(response[json_start:json_end])
                    tool = action.get("tool")
                    
                    if tool:
                        # Save thought to DB for next iteration
                        thought = action.get("thought", "")
                        if thought:
                            await self.push_agent_state("last_thought", thought)
                            self.cognitive_history.append(f"Iteration {self.iteration} Thought: {thought}")

                    if tool == "chat_respond":
                        msg = action.get("message", "")
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
                        code, out, err = await self.execute_native("node capture_screenshot.js")
                        if code == 0:
                            screenshot_path = os.path.join(WORKSPACE_DIR, "latest_screenshot.png")
                            await self.push_screenshot(screenshot_path)
                            await self.push_remote_log("Screenshot uploaded to Dashboard.")
                        else:
                            await self.push_remote_log(f"Screenshot failed: {err}")
                            
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
                            
                    elif tool == "run_bash":
                        cmd = action.get("command", "")
                        msg = f"Executing Natively: {cmd}"
                        logger.info(msg)
                        await self.push_remote_log(msg)
                        
                        code, out, err = await self.execute_native(cmd)
                        
                        # Truncate output to prevent blowing up the context window (Ollama limit/speed)
                        if len(out) > 3000:
                            out = "...[TRUNCATED]...\n" + out[-3000:]
                        if len(err) > 1000:
                            err = "...[TRUNCATED]...\n" + err[-1000:]
                            
                        res_msg = f"Native Result: {out}\n{err}"
                        logger.info(res_msg[:500] + ("..." if len(res_msg) > 500 else ""))
                        await self.push_remote_log(res_msg[:500] + ("..." if len(res_msg) > 500 else ""))
                        
                        # Save last command output to DB
                        await self.push_agent_state("last_command_output", res_msg)
                        
                        # Add to cognitive history
                        self.cognitive_history.append(f"Iteration {self.iteration}: Ran '{cmd}'. Result: {res_msg[:200]}...")
                        if len(self.cognitive_history) > 10: # Keep last 5 iterations (thought + result pairs)
                            self.cognitive_history.pop(0)
                        
                        if code != 0:
                            self.failures += 1
                            await self.push_agent_state("consecutive_failures", str(self.failures))
                            if self.failures >= MAX_FAILURES:
                                self.git_reset()
                                self.append_journal("Executed Git Reset due to 5 consecutive failures.")
                                self.failures = 0
                                await self.push_agent_state("consecutive_failures", "0")
                        else:
                            self.failures = 0
                            await self.push_agent_state("consecutive_failures", "0")
                    else:
                        err_msg = f"JSON Parser Error: Invalid or missing 'tool' key. You must use 'tool': 'run_bash' and 'command': '<your command>'. You provided: {response[json_start:json_end]}"
                        logger.warning(err_msg)
                        await self.push_remote_log(err_msg)
                        await self.push_agent_state("last_command_output", err_msg)
                else:
                    err_msg = "JSON Parser Error: No JSON object found in your output. You must output raw JSON."
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
            
            await asyncio.sleep(5) # Prevent ultra-fast looping in case of API failure

if __name__ == "__main__":
    supervisor = GemmaSupervisor()
    asyncio.run(supervisor.run_loop())
