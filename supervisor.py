# =============================================================================
# Gemma Game Studio — supervisor.py v2
# Reusable autonomous game development agent framework.
#
# Modes (state machine):
#   BOOTSTRAP  → Interactive intake → brief.md + manifest.json
#   CREATIVE   → Autonomous world-building, lore, art direction
#   ARCHITECT  → One-shot: Gemma designs task_queue.md from brief + specs
#   BUILD      → Two-turn loop: PLAN turn → EXECUTE turn per task
#   REPAIR     → Structured Autonomous Repair (SAR): deterministic triage + LLM rewrite
#   PLAYTEST   → Screenshot feedback → new tasks back into queue
#
# Memory (file-based, per game in games/{active_game}/agent/):
#   brief.md        — creative north star (from BOOTSTRAP)
#   manifest.json   — technical config + current mode (source of truth)
#   task_queue.md   — ordered task list (from ARCHITECT, updated by BUILD)
#   journal.md      — decision log, appended after each completed task
#   plan.md         — current PLAN turn output (consumed by EXECUTE turn)
#
# Active game set in studio_config.json → { "active_game": "aetheria" }
# =============================================================================

import asyncio
import aiohttp
import json
import os
import re
import subprocess
import sys
import signal
import logging
import datetime
import atexit
from ddgs import DDGS

# ── Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("GemmaSupervisor")

# ── Config
ROOT_DIR      = os.path.dirname(os.path.abspath(__file__))
FEEDBACK_PATH = os.path.join(ROOT_DIR, "human_feedback.md")
PID_FILE      = os.path.join(ROOT_DIR, "supervisor.pid")

# ── Active game — read from studio_config.json
_studio_config_path = os.path.join(ROOT_DIR, "studio_config.json")
_studio_config      = json.loads(open(_studio_config_path).read()) if os.path.exists(_studio_config_path) else {}
ACTIVE_GAME         = _studio_config.get("active_game", "aetheria")
GAMES_DIR           = os.path.join(ROOT_DIR, "games")
WORKSPACE_DIR       = os.path.join(GAMES_DIR, ACTIVE_GAME)
AGENT_DIR           = os.path.join(WORKSPACE_DIR, "agent")
logger.info(f"Active game: {ACTIVE_GAME} @ {WORKSPACE_DIR}")

OLLAMA_URL    = "http://localhost:11434/api/generate"
MODEL_NAME    = "gemma4:31b"
DROPLET_IP    = "165.227.27.71"
API_KEY       = "epiphany_secret_2026"

# Agent memory paths (scoped to active game)
BRIEF_PATH      = os.path.join(AGENT_DIR, "brief.md")
MANIFEST_PATH   = os.path.join(AGENT_DIR, "manifest.json")
TASK_QUEUE_PATH = os.path.join(AGENT_DIR, "task_queue.md")
JOURNAL_PATH    = os.path.join(AGENT_DIR, "journal.md")
PLAN_PATH           = os.path.join(AGENT_DIR, "plan.md")
REPAIR_RETRIES_PATH = os.path.join(AGENT_DIR, "repair_retries.json")
LAST_OUTPUT_PATH    = os.path.join(AGENT_DIR, "last_output.txt")

MAX_RETRIES = 5  # attempts per file before marking it stuck


# =============================================================================
# SAR Engine — pure functions, no class state needed
# =============================================================================

def parse_ts_errors(tsc_output: str) -> dict:
    """Parse `tsc --noEmit` output into {rel_path: [error_dict]} map."""
    errors = {}
    pattern = re.compile(r'^(.+?\.tsx?)\((\d+),(\d+)\): error (TS\d+): (.+)$')
    for line in tsc_output.splitlines():
        m = pattern.match(line.strip())
        if m:
            path, ln, col, code, msg = m.groups()
            rel = path
            for prefix in [WORKSPACE_DIR + '/', WORKSPACE_DIR + os.sep, './']:
                if rel.startswith(prefix):
                    rel = rel[len(prefix):]
                    break
            errors.setdefault(rel, []).append({
                'line': int(ln), 'col': int(col), 'code': code, 'message': msg.strip()
            })
    return errors


def resolve_imports(filepath: str) -> list:
    """Extract resolved .ts paths from import statements in a TypeScript file."""
    full = os.path.join(WORKSPACE_DIR, filepath)
    if not os.path.exists(full):
        return []
    try:
        content = open(full).read()
    except Exception:
        return []
    base_dir = os.path.dirname(filepath)
    results = []
    for m in re.finditer(r"""from\s+['"]([./][^'"]+)['"]""", content):
        raw = m.group(1)
        resolved = os.path.normpath(os.path.join(base_dir, raw)).replace('\\', '/')
        for candidate in [resolved + '.ts', resolved + '/index.ts']:
            if os.path.exists(os.path.join(WORKSPACE_DIR, candidate)):
                results.append(candidate)
                break
    return results


def build_repair_order(error_map: dict) -> list:
    """Return files sorted: imported-by-others first (unblock more), then error count desc."""
    files = set(error_map.keys())
    imported_by_errored = set()
    for f in files:
        for imp in resolve_imports(f):
            if imp in files:
                imported_by_errored.add(imp)
    return sorted(files, key=lambda f: (0 if f in imported_by_errored else 1, -len(error_map[f])))


TRIVIAL_CODES = {'TS2551', 'TS2552', 'TS2564', 'TS7006', 'TS2307', 'TS2724', 'TS2693'}

# PixiJS v7/v8: BLEND_MODES enum was replaced with string literals
PIXI_BLEND_MODE_MAP = {
    'BLEND_MODES.NORMAL': "'normal'",
    'BLEND_MODES.ADD': "'add'",
    'BLEND_MODES.MULTIPLY': "'multiply'",
    'BLEND_MODES.SCREEN': "'screen'",
    'BLEND_MODES.OVERLAY': "'overlay'",
    'BLEND_MODES.DARKEN': "'darken'",
    'BLEND_MODES.LIGHTEN': "'lighten'",
    'BLEND_MODES.COLOR_DODGE': "'color-dodge'",
    'BLEND_MODES.COLOR_BURN': "'color-burn'",
    'BLEND_MODES.HARD_LIGHT': "'hard-light'",
    'BLEND_MODES.SOFT_LIGHT': "'soft-light'",
    'BLEND_MODES.DIFFERENCE': "'difference'",
    'BLEND_MODES.EXCLUSION': "'exclusion'",
    'BLEND_MODES.HUE': "'hue'",
    'BLEND_MODES.SATURATION': "'saturation'",
    'BLEND_MODES.COLOR': "'color'",
    'BLEND_MODES.LUMINOSITY': "'luminosity'",
    'BLEND_MODES.NONE': "'none'",
    'BLEND_MODES.ERASE': "'erase'",
}


def _find_module_in_workspace(import_spec: str) -> str | None:
    """Given a relative import like '../types' or './WorldState', find the actual .ts file in WORKSPACE_DIR."""
    # Strip leading ./
    name = import_spec.lstrip('./').split('/')[-1]
    # Walk the workspace src tree looking for a matching filename
    for root, _, files in os.walk(os.path.join(WORKSPACE_DIR, "src")):
        for f in files:
            if f == name + '.ts' or f == name + '.d.ts':
                return os.path.join(root, f)
    return None


def attempt_trivial_fixes(filepath: str, errors: list) -> tuple:
    """Auto-fix trivial errors in place. Returns (changed: bool, new_content: str)."""
    full = os.path.join(WORKSPACE_DIR, filepath)
    try:
        lines = open(full).readlines()
    except Exception:
        return False, ""

    changed = False
    for err in errors:
        if err['code'] not in TRIVIAL_CODES:
            continue
        li = err['line'] - 1
        if li < 0 or li >= len(lines):
            continue
        line = lines[li]

        if err['code'] == 'TS2551':
            bad_m  = re.search(r"Property '(\w+)' does not exist", err['message'])
            good_m = re.search(r"Did you mean '(\w+)'\?", err['message'])
            if bad_m and good_m and bad_m.group(1) != good_m.group(1):
                new_line = line.replace(bad_m.group(1), good_m.group(1), 1)
                if new_line != line:
                    lines[li] = new_line
                    changed = True

        elif err['code'] == 'TS2564':
            prop_m = re.search(r"Property '(\w+)'", err['message'])
            if prop_m:
                pname = prop_m.group(1)
                new_line = re.sub(r'(\b' + re.escape(pname) + r'\b)(\s*[?]?\s*:)', r'\1!\2', line, count=1)
                if new_line != line:
                    lines[li] = new_line
                    changed = True

        elif err['code'] == 'TS7006':
            param_m = re.search(r"Parameter '(\w+)' implicitly", err['message'])
            if param_m:
                pname = param_m.group(1)
                new_line = re.sub(r'(\b' + re.escape(pname) + r'\b)(\s*[,){])', r'\1: any\2', line, count=1)
                if new_line != line:
                    lines[li] = new_line
                    changed = True

        elif err['code'] == 'TS2552':
            # "Cannot find name 'X'. Did you mean 'Y'?" — rename ALL occurrences in file
            bad_m  = re.search(r"Cannot find name '(\w+)'", err['message'])
            good_m = re.search(r"Did you mean '(\w+)'\?", err['message'])
            if bad_m and good_m and bad_m.group(1) != good_m.group(1):
                new_content = re.sub(r'\b' + re.escape(bad_m.group(1)) + r'\b', good_m.group(1), ''.join(lines))
                if new_content != ''.join(lines):
                    lines = list(new_content)  # will be rejoined at end
                    lines = [new_content]  # treat as single string for return
                    changed = True
                    break  # re.sub already handled all lines

        elif err['code'] == 'TS2724':
            # "'module' has no exported member named 'X'. Did you mean 'Y'?" — rename ALL occurrences
            bad_m  = re.search(r"has no exported member named '(\w+)'", err['message'])
            good_m = re.search(r"Did you mean '(\w+)'\?", err['message'])
            if bad_m and good_m and bad_m.group(1) != good_m.group(1):
                new_content = re.sub(r'\b' + re.escape(bad_m.group(1)) + r'\b', good_m.group(1), ''.join(lines))
                if new_content != ''.join(lines):
                    lines = [new_content]
                    changed = True
                    break

        elif err['code'] == 'TS2307':
            # "Cannot find module 'X' or its corresponding type declarations."
            mod_m = re.search(r"Cannot find module '([^']+)'", err['message'])
            if mod_m:
                bad_import = mod_m.group(1)
                # Only fix relative imports (starts with . or ..)
                if bad_import.startswith('.'):
                    # Try to find the actual file on disk
                    actual_path = _find_module_in_workspace(bad_import)
                    if actual_path:
                        file_dir = os.path.dirname(os.path.join(WORKSPACE_DIR, filepath))
                        rel = os.path.relpath(actual_path.replace('.ts', ''), file_dir).replace(os.sep, '/')
                        if not rel.startswith('.'):
                            rel = './' + rel
                        new_line = line.replace(f"'{bad_import}'", f"'{rel}'", 1)
                        new_line = new_line.replace(f'"{bad_import}"', f'"{rel}"', 1)
                        if new_line != line:
                            lines[li] = new_line
                            changed = True

        elif err['code'] == 'TS2693':
            # "'X' only refers to a type, but is being used as a value here"
            # Known case: BLEND_MODES used as runtime enum — replace with string literals
            type_m = re.search(r"'(\w+)' only refers to a type", err['message'])
            if type_m and type_m.group(1) == 'BLEND_MODES':
                content = ''.join(lines)
                new_content = content
                for old, new in PIXI_BLEND_MODE_MAP.items():
                    new_content = new_content.replace(old, new)
                # Remove the type-only BLEND_MODES import if no more usages remain
                new_content = re.sub(r',\s*BLEND_MODES', '', new_content)
                new_content = re.sub(r'BLEND_MODES\s*,\s*', '', new_content)
                new_content = re.sub(r"import\s*\{\s*\}\s*from\s*'pixi\.js';?\n?", '', new_content)
                if new_content != content:
                    lines = [new_content]
                    changed = True
                    break

    result = lines[0] if len(lines) == 1 and isinstance(lines[0], str) and '\n' in lines[0] else ''.join(lines)
    return changed, result


# Mapping of source files to their design spec documents
SPEC_MAP = {
    'src/client/core/CoreEngine.ts':                    'specs/CoreEngine.md',
    'src/client/core/systems/DialogueSystem.ts':        'specs/systems/DialogueSystem.md',
    'src/client/core/systems/LLMEngine.ts':             'specs/systems/LLMEngine.md',
    'src/client/core/systems/MovementSystem.ts':        'specs/systems/MovementSystem_SideScrolling.md',
    'src/client/core/systems/CameraSystem.ts':          'specs/systems/CameraSystem.md',
    'src/client/core/systems/InputSystem.ts':           'specs/systems/InputSystem.md',
    'src/client/core/systems/InteractionSystem.ts':     'specs/systems/InteractionSystem.md',
    'src/client/core/systems/QuestSystem.ts':           'specs/systems/QuestSystem.md',
    'src/client/core/systems/RenderSystem.ts':          'specs/systems/RenderSystem.md',
    'src/client/core/systems/WorldSystem.ts':           'specs/systems/WorldSystem.md',
    'src/client/ui/DialogueUI.ts':                      'specs/DialogueUI.spec.md',
    'src/client/state/GameState.ts':                    'specs/gamestate_spec.md',
    'src/server/GameServer.ts':                         'specs/server/GameServer.md',
}


def find_spec_for_file(source_file: str) -> str | None:
    """Return the spec markdown content for a given source file, if available."""
    spec_rel = SPEC_MAP.get(source_file)
    if not spec_rel:
        base = os.path.splitext(os.path.basename(source_file))[0].lower()
        for root, _, files in os.walk(os.path.join(WORKSPACE_DIR, 'specs')):
            for f in files:
                if f.lower().startswith(base) and f.endswith('.md'):
                    spec_rel = os.path.relpath(os.path.join(root, f), WORKSPACE_DIR)
                    break
            if spec_rel:
                break
    if not spec_rel:
        return None
    full = os.path.join(WORKSPACE_DIR, spec_rel)
    return open(full).read() if os.path.exists(full) else None


def assemble_nuke_prompt(target_file: str, errors: list, brief: str) -> str:
    """Build a 'rewrite from spec' prompt for files that have resisted incremental repair."""
    spec_content = find_spec_for_file(target_file)
    full_path = os.path.join(WORKSPACE_DIR, target_file)
    original = open(full_path).read() if os.path.exists(full_path) else ''
    orig_exports = re.findall(r'export\s+(?:class|interface|type|enum|function|const)\s+(\w+)', original)
    error_block = '\n'.join(f'  Line {e["line"]}: [{e["code"]}] {e["message"]}' for e in errors)
    exports_block = '\n'.join(f'  export {e}' for e in orig_exports) if orig_exports else '  (none detected — infer from spec)'
    return (
        'You are a TypeScript expert. Incremental patching has failed on this file.\n'
        'Rewrite it COMPLETELY FROM SCRATCH — discard the current implementation, start fresh.\n\n'
        f'FILE TO REWRITE: {target_file}\n\n'
        f'REQUIRED EXPORTS (must ALL be present in your output):\n{exports_block}\n\n'
        + (f'DESIGN SPEC (source of truth for behaviour):\n{spec_content[:3500]}\n\n' if spec_content else '')
        + f'COMPILE ERRORS IN CURRENT BROKEN VERSION (context only — do not patch, rewrite):\n{error_block}\n\n'
        + f'GAME CONTEXT: {brief[:300]}\n\n'
        + 'OUTPUT RULES:\n'
        + '- Respond with ONLY a ```typescript ... ``` code fence\n'
        + '- No explanation, no commentary, no text outside the fence\n'
        + '- Complete, compilable TypeScript — prioritise correctness over completeness\n'
        + '- Preserve all required exports listed above\n'
        + '- Only import from paths that exist in the codebase\n'
    )


def assemble_task_card(target_file: str, errors: list, context_files: dict, brief: str, retry: int = 0, previous_new_errors: dict = None, full_tsc_output: str = None) -> str:
    """Build the complete task card prompt for Gemma in REPAIR mode."""
    error_block = "\n".join(
        f"  Line {e['line']}: [{e['code']}] {e['message']}" for e in errors
    )
    context_block = "\n".join(
        "\n" + "=" * 60 + f"\nCONTEXT (read-only): {path}\n" + "=" * 60 + f"\n{content}"
        for path, content in context_files.items()
        if path != target_file
    )
    target_content = context_files.get(target_file, "(file not found)")

    if retry == 0:
        retry_note = ""
    elif previous_new_errors:
        # Tell Gemma exactly what new errors her last attempt introduced
        new_err_lines = []
        for f, errs in previous_new_errors.items():
            for e in errs:
                new_err_lines.append(f"  {f}({e['line']}): [{e['code']}] {e['message']}")
        retry_note = (
            f"\n⚠️ RETRY {retry}: Your previous rewrite INTRODUCED {sum(len(v) for v in previous_new_errors.values())} NEW ERRORS:\n"
            + "\n".join(new_err_lines[:20]) + "\n"
            + "Fix the original errors WITHOUT introducing any of the above new ones.\n"
            + "Pay close attention to import paths and type compatibility.\n"
        )
    else:
        retry_note = (
            f"\n⚠️ RETRY {retry}: Your previous response contained NO CODE BLOCK. "
            "BEGIN YOUR RESPONSE IMMEDIATELY WITH ```typescript and nothing else.\n"
        )

    # On retry, show the full compiler output so Gemma has the same view a developer would
    tsc_block = ""
    if retry >= 1 and full_tsc_output:
        tsc_block = (
            "\nFULL COMPILER OUTPUT (all files — same output as running `npx tsc --noEmit`):\n"
            + "```\n" + full_tsc_output[:3000] + ("\n...[TRUNCATED]" if len(full_tsc_output) > 3000 else "") + "\n```\n"
        )

    # Inject art direction palette as named constants if available
    palette_note = ""
    palette_path = os.path.join(WORKSPACE_DIR, "lore", "art_direction", "palette.md")
    if os.path.exists(palette_path):
        try:
            palette_content = open(palette_path).read()[:1200]
            palette_note = (
                "\nART DIRECTION PALETTE (use these exact hex values for any color constants):\n"
                + palette_content + "\n"
            )
        except Exception:
            pass

    sprite_note = ""
    sprite_path = os.path.join(WORKSPACE_DIR, "lore", "art_direction", "sprite_spec.md")
    if os.path.exists(sprite_path):
        try:
            sprite_note = (
                "\nSPRITE SPEC (use these exact dimensions for all sprite/tilemap loads):\n"
                + open(sprite_path).read()[:800] + "\n"
            )
        except Exception:
            pass

    # Detect framework from node_modules
    phaser_present = os.path.exists(os.path.join(WORKSPACE_DIR, "node_modules", "phaser"))
    framework_note = (
        "This is a Phaser 3 game. Use `import Phaser from 'phaser'`.\n"
        "Scenes extend Phaser.Scene. Never use PixiJS or CoreEngine/GameSystem patterns.\n"
        "Phaser API: this.physics, this.add, this.input, this.cameras, this.load, this.scene\n"
    ) if phaser_present else (
        "This is a TypeScript game using PixiJS. Use `import * as PIXI from 'pixi.js'`.\n"
    )

    return (
        "You are a TypeScript expert fixing compile errors.\n"
        + framework_note
        + retry_note
        + tsc_block
        + f"\nTASK: Rewrite `{target_file}` to fix all {len(errors)} errors listed below.\n"
        + f"\nERRORS TO FIX:\n{error_block}\n"
        + f"\nCONTEXT FILES (read-only):\n{context_block if context_block else '(none)'}\n"
        + f"\nFILE TO REWRITE:\n{'='*60}\n{target_file}\n{'='*60}\n{target_content}\n"
        + f"\nGAME CONTEXT: {brief[:400] if brief else 'A sci-fi side-scrolling RPG built with Phaser 3.'}\n"
        + palette_note
        + sprite_note
        + "\nOUTPUT RULES:\n"
        + "- Respond with ONLY a ```typescript ... ``` code fence\n"
        + "- No explanation, no commentary, no other text outside the fence\n"
        + "- Preserve all existing exports and functionality\n"
        + "- Do not add imports for modules not in the codebase\n"
    )


def lookup_conflicting_type_hints(target_file: str, errors: list) -> str:
    """For TS2416 errors (type not assignable), find all competing definitions
    of the same type name in the workspace and inject them so Gemma can resolve."""
    hints = []
    for e in errors:
        if e['code'] not in ('TS2416', 'TS2345', 'TS2322'):
            continue
        # Extract the type name from the error message
        type_m = re.search(r"type '([A-Z][\w]+)'", e['message'])
        if not type_m:
            continue
        type_name = type_m.group(1)
        # Find all .ts files that export this type
        definitions = []
        for root, _, files in os.walk(os.path.join(WORKSPACE_DIR, 'src')):
            for f in files:
                if not f.endswith('.ts'):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, WORKSPACE_DIR).replace(os.sep, '/')
                if rel == target_file:
                    continue
                try:
                    content = open(full).read()
                except Exception:
                    continue
                # Look for an export of this exact type name
                if re.search(r'export\s+(?:interface|class|type|enum)\s+' + re.escape(type_name) + r'\b', content):
                    # Extract the type definition block (up to 40 lines)
                    m = re.search(
                        r'export\s+(?:interface|class|type|enum)\s+' + re.escape(type_name) + r'\b[^{]*\{',
                        content)
                    if m:
                        block = '\n'.join(content[m.start():m.start()+3000].splitlines()[:40])
                        definitions.append((rel, block))
        if len(definitions) >= 2:
            hint_block = f"\n⚠️ TYPE CONFLICT: '{type_name}' is defined in multiple files:\n"
            for rel, block in definitions:
                hint_block += f"\n--- {rel} ---\n{block}\n...(truncated)\n"
            # Find which one GameSystem (or the base class) uses
            gamesystem_path = 'src/client/core/systems/GameSystem.ts'
            full_gs = os.path.join(WORKSPACE_DIR, gamesystem_path)
            if os.path.exists(full_gs):
                gs_content = open(full_gs).read()
                for rel, _ in definitions:
                    if rel in gs_content or rel.replace('/', "'") in gs_content:
                        hint_block += f"\n✅ GameSystem imports '{type_name}' from '{rel}' — use THIS import path.\n"
                        break
                else:
                    # Check which import path GameSystem actually uses
                    imp_m = re.search(r"from '([^']+(?:GameState|types)[^']*)'"  , gs_content)
                    if imp_m:
                        hint_block += f"\n✅ GameSystem imports from '{imp_m.group(1)}' — use THIS import path.\n"
            hints.append(hint_block)
    return '\n'.join(hints)


def lookup_pixi_type_hints(errors: list) -> str:
    """For TS2339 'property does not exist on type X' errors, look up the
    actual type definition (Phaser or PixiJS) and return it as a hint block."""
    type_names = set()
    for e in errors:
        if e['code'] == 'TS2339':
            m = re.search(r"does not exist on type '([^']+)'", e['message'])
            if m:
                type_names.add(re.sub(r'<.*>', '', m.group(1)).strip())

    if not type_names:
        return ""

    # Try Phaser 3 type definitions first
    phaser_dts = os.path.join(WORKSPACE_DIR, "node_modules", "phaser", "types", "phaser.d.ts")
    # Fallback to PixiJS
    pixi_dts   = os.path.join(WORKSPACE_DIR, "node_modules", "pixi.js", "dist", "pixi.js.d.ts")
    dts_path   = phaser_dts if os.path.exists(phaser_dts) else (pixi_dts if os.path.exists(pixi_dts) else None)
    framework  = "Phaser" if os.path.exists(phaser_dts) else "PixiJS"

    if not dts_path:
        return ""

    try:
        dts_content = open(dts_path).read()
    except Exception:
        return ""

    hints = []
    for type_name in sorted(type_names):
        m = re.search(
            r'(?:export\s+)?(?:declare\s+)?(?:class|interface)\s+' + re.escape(type_name) + r'\b[^{]*\{',
            dts_content
        )
        if not m:
            continue
        start = m.start()
        block_lines = dts_content[start:start + 4000].splitlines()[:60]
        block = '\n'.join(block_lines)
        hints.append(f"// {framework} type definition for {type_name}:\n{block}\n// ... (see full definition in node_modules)")

    return (f"\n{framework.upper()} TYPE REFERENCE (use these exact method/property names):\n"
            + "\n\n".join(hints) + "\n") if hints else ""


def extract_code_block(response: str) -> str:
    """Extract TypeScript content from a fenced code block."""
    # Try any language-tagged fence (typescript, ts, javascript, js, tsx, jsx, ...)
    m = re.search(r'```(?:[a-zA-Z]+)\n(.*?)```', response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try plain fence
    m = re.search(r'```\n(.*?)```', response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Opening fence but truncated before closing (output was cut off)
    m = re.search(r'```(?:[a-zA-Z]*)\n(.*)', response, re.DOTALL)
    if m:
        code = m.group(1).strip()
        if len(code) > 50:
            return code
    # Scan for TypeScript code anywhere in the response (Gemma wrote prose before code)
    ts_keywords = ('import ', 'export ', 'class ', 'const ', 'interface ', 'type ', '//', '/*')
    lines = response.strip().splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(ts_keywords):
            code = '\n'.join(lines[i:]).strip()
            # Strip trailing fence or prose after closing fence
            code = re.sub(r'\n```[\s\S]*$', '', code)
            if len(code) > 50:
                return code
    return ""


# =============================================================================
# GemmaSupervisor
# =============================================================================

class GemmaSupervisor:

    def __init__(self):
        self.iteration = 0
        self.ddgs = DDGS()
        self._shutdown = False
        self._paused = False

    # ── Signals ───────────────────────────────────────────────────────────────

    def setup_signals(self):
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info(f"Caught signal {signum}, shutting down cleanly.")
        self._shutdown = True

    # ── Core infrastructure ───────────────────────────────────────────────────

    async def execute_native(self, command: str, timeout: int = 60) -> tuple:
        process = await asyncio.create_subprocess_shell(
            command, cwd=WORKSPACE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=os.setsid
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            return process.returncode, stdout.decode(), stderr.decode()
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(process.pid), 9)
            except Exception:
                process.kill()
            return 1, "", f"Command timed out after {timeout}s."

    async def prompt_gemma(self, prompt: str, image_path: str = None, temperature: float = 0.7, json_mode: bool = False, max_tokens: int = 4096, num_ctx: int = 32768) -> str:
        import base64
        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_ctx": num_ctx,
                "num_predict": max_tokens,
                "temperature": temperature,
                "top_p": 0.9,
                "stop": ["Observation:"]
            }
        }
        if json_mode:
            payload["format"] = "json"
        if image_path and os.path.exists(image_path):
            try:
                with open(image_path, "rb") as f:
                    payload["images"] = [base64.b64encode(f.read()).decode()]
            except Exception as e:
                logger.warning(f"Could not encode image: {e}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    OLLAMA_URL, json=payload,
                    timeout=aiohttp.ClientTimeout(total=600)
                ) as response:
                    if response.status == 200:
                        return (await response.json()).get("response", "")
                    logger.error(f"Ollama error: {response.status}")
            except Exception as e:
                logger.error(f"Ollama connection error: {e}")
                if "timeout" in str(e).lower() or not str(e):
                    logger.warning("Possible memory lock — flushing Ollama...")
                    subprocess.run(["pkill", "-9", "ollama"], capture_output=True)
                    subprocess.Popen(["nohup", "ollama", "serve"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     preexec_fn=os.setpgrp)
        return ""

    def _flush_ollama_if_needed(self):
        if self.iteration > 0 and self.iteration % 50 == 0:
            logger.info("Scheduled Ollama memory flush...")
            subprocess.run(["pkill", "-9", "ollama"], capture_output=True)
            subprocess.Popen(["nohup", "ollama", "serve"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             preexec_fn=os.setpgrp)

    # ── File memory ───────────────────────────────────────────────────────────

    def read_agent_file(self, path: str, default: str = "") -> str:
        try:
            return open(path).read()
        except Exception:
            return default

    def write_agent_file(self, path: str, content: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)

    def append_journal(self, entry: str):
        os.makedirs(AGENT_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(JOURNAL_PATH, 'a') as f:
            f.write(f"\n---\n**{ts} — Iteration {self.iteration}**\n{entry}\n")

    def read_brief(self) -> str:
        return self.read_agent_file(BRIEF_PATH, "A game built with TypeScript and PixiJS.")

    def read_manifest(self) -> dict:
        try:
            return json.loads(open(MANIFEST_PATH).read())
        except Exception:
            return {}

    def write_manifest(self, data: dict):
        self.write_agent_file(MANIFEST_PATH, json.dumps(data, indent=2))

    def set_mode(self, mode: str):
        manifest = self.read_manifest()
        manifest["mode"] = mode
        self.write_manifest(manifest)

    # ── Git ───────────────────────────────────────────────────────────────────

    def git_commit(self, message: str):
        subprocess.run(["git", "add", "."], cwd=WORKSPACE_DIR, capture_output=True)
        result = subprocess.run(["git", "commit", "-m", message],
                                cwd=WORKSPACE_DIR, capture_output=True, text=True)
        if result.returncode == 0:
            logger.info(f"Git commit: {message}")

    def git_rollback_file(self, filepath: str):
        subprocess.run(["git", "checkout", "--", filepath],
                       cwd=WORKSPACE_DIR, capture_output=True)
        logger.warning(f"Rolled back {filepath} to HEAD.")

    # ── Dashboard API ─────────────────────────────────────────────────────────

    async def _api(self, method: str, endpoint: str, **kwargs) -> dict:
        headers = {"X-API-KEY": API_KEY}
        kwargs.setdefault("headers", {}).update(headers)
        try:
            async with aiohttp.ClientSession() as s:
                fn = getattr(s, method)
                async with fn(f"http://{DROPLET_IP}:8080{endpoint}",
                              timeout=aiohttp.ClientTimeout(total=5),
                              **kwargs) as r:
                    if r.status == 200:
                        return await r.json()
        except Exception:
            pass
        return {}

    async def log(self, text: str):
        logger.info(text)
        await self._api("post", "/api/logs", json={"log": text})

    async def push_state(self, key: str, value: str):
        await self._api("post", "/api/state", json={"key": key, "value": value})

    async def fetch_state(self) -> dict:
        return (await self._api("get", "/api/state")).get("state", {})

    async def set_last_output(self, text: str):
        """Write tool output to local file (reliable) and push to dashboard (display only)."""
        self.write_agent_file(LAST_OUTPUT_PATH, text)
        await self.push_state("last_command_output", text)  # dashboard display only

    def read_last_output(self) -> str:
        return self.read_agent_file(LAST_OUTPUT_PATH, "None")

    def read_repair_retries(self) -> dict:
        try:
            return json.loads(open(REPAIR_RETRIES_PATH).read())
        except Exception:
            return {}

    def write_repair_retries(self, counts: dict):
        self.write_agent_file(REPAIR_RETRIES_PATH, json.dumps(counts))

    def clear_repair_retries(self):
        if os.path.exists(REPAIR_RETRIES_PATH):
            os.remove(REPAIR_RETRIES_PATH)

    async def push_chat(self, message: str):
        await self._api("post", "/api/chat/response", json={"message": message})

    async def push_human_message(self, message: str):
        await self._api("post", "/api/chat/human", json={"message": message})

    async def fetch_pending_chat(self) -> list:
        return (await self._api("get", "/api/chat/pending")).get("messages", [])

    async def fetch_chat_history(self) -> str:
        history = (await self._api("get", "/api/chat/history")).get("history", [])
        if not history:
            return "No chat history."
        lines = [f"[{'HUMAN' if m['sender']=='human' else 'YOU'}]: {m['message']}" for m in history[-10:]]
        return "\n".join(lines)

    async def push_screenshot(self, filepath: str):
        if not os.path.exists(filepath):
            return
        try:
            with open(filepath, "rb") as f:
                data = f.read()
            headers = {
                "X-API-KEY": API_KEY,
                "Content-Type": "image/png",
                "X-Image-Name": os.path.basename(filepath)
            }
            async with aiohttp.ClientSession() as s:
                await s.post(f"http://{DROPLET_IP}:8080/api/screenshot",
                             data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=10))
        except Exception as e:
            logger.error(f"Failed to push screenshot: {e}")

    async def push_action(self, tool: str, summary: str, outcome: str = "ok"):
        await self._api("post", "/api/action_log",
                        json={"iteration": self.iteration, "tool": tool,
                              "summary": summary, "outcome": outcome})

    async def sync_intel(self):
        for name, path in [("manifesto", BRIEF_PATH), ("journal", JOURNAL_PATH)]:
            if os.path.exists(path):
                content = open(path).read()
                await self._api("post", f"/api/{name}", json={"content": content})

    async def fetch_reminders(self) -> str:
        reminders = (await self._api("get", "/api/reminders")).get("reminders", [])
        return "\n".join(f"- {r}" for r in reminders) if reminders else ""

    # ── Workspace init ────────────────────────────────────────────────────────

    async def initialize_workspace(self):
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        os.makedirs(AGENT_DIR, exist_ok=True)
        if not os.path.exists(FEEDBACK_PATH):
            open(FEEDBACK_PATH, 'w').write(
                "<!-- Write feedback here. Agent reads and clears this each loop. -->\n")
        subprocess.run(["git", "init"], cwd=WORKSPACE_DIR, capture_output=True)
        subprocess.run(["git", "config", "user.email", "gemma@local"],
                       cwd=WORKSPACE_DIR, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Gemma"],
                       cwd=WORKSPACE_DIR, capture_output=True)
        if not os.path.exists(MANIFEST_PATH):
            await self._migrate_existing_project()
        logger.info("Workspace initialized.")

    async def _migrate_existing_project(self):
        """Create agent/ files for an existing project (v1 migration or new install)."""
        src_dir  = os.path.join(WORKSPACE_DIR, "src")
        lore_dir = os.path.join(WORKSPACE_DIR, "lore")
        has_src  = os.path.exists(src_dir) and any(
            f.endswith('.ts') for _, _, files in os.walk(src_dir) for f in files)
        has_lore = os.path.exists(lore_dir) and bool(os.listdir(lore_dir))

        mode = "REPAIR" if has_src else ("ARCHITECT" if has_lore else "BOOTSTRAP")

        if not os.path.exists(BRIEF_PATH):
            manifesto_path = os.path.join(ROOT_DIR, "manifesto.md")
            brief = open(manifesto_path).read()[:6000] if os.path.exists(manifesto_path) \
                    else "# Khoros\nA sci-fi RPG built with TypeScript and PixiJS.\n"
            self.write_agent_file(BRIEF_PATH, brief)

        manifest = {
            "name":       "Khoros",
            "game_type":  "side_scroller",
            "art_style":  "pixel",
            "platform":   "browser",
            "scope":      "indie",
            "tech_stack": "typescript_pixijs",
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d"),
            "mode":       mode
        }
        self.write_manifest(manifest)
        logger.info(f"Migrated existing project -> mode={mode}")

    # ── Human feedback ────────────────────────────────────────────────────────

    def read_feedback(self) -> str:
        if not os.path.exists(FEEDBACK_PATH):
            return ""
        with open(FEEDBACK_PATH) as f:
            raw = f.read()
        lines = [l for l in raw.splitlines() if not l.strip().startswith("<!--")]
        content = "\n".join(lines).strip()
        if content:
            open(FEEDBACK_PATH, 'w').write(
                "<!-- Write feedback here. Agent reads and clears this each loop. -->\n")
        return content

    # ── Mode: BOOTSTRAP ───────────────────────────────────────────────────────

    async def run_bootstrap(self):
        """Interactive intake -> brief.md + manifest.json -> enters CREATIVE."""
        await self.log("=== BOOTSTRAP: New project intake ===")

        questions = [
            ("name",      "What is this game called? (working title fine)"),
            ("game_type", "Game type? (side-scroller / top-down / first-person / other)"),
            ("art_style", "Art style? (pixel / vector / high-res / 3D / hand-drawn)"),
            ("platform",  "Target platform? (browser / desktop / mobile)"),
            ("scope",     "Scope? (jam=one mechanic | indie=several systems | full=complete game)"),
            ("seed",      "Describe the game in 2-4 sentences. Genre, tone, feel, references."),
            ("mvp",       "What is the minimum that makes it feel like THIS game?"),
        ]

        answers = {}
        print("\n" + "=" * 60)
        print("  GEMMA GAME STUDIO — New Project")
        print("=" * 60)
        for key, question in questions:
            print(f"\n{question}")
            try:
                answers[key] = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                answers[key] = ""

        manifest = {
            "name":       answers.get("name", "Untitled"),
            "game_type":  answers.get("game_type", "").lower().replace(" ", "_"),
            "art_style":  answers.get("art_style", "").lower().replace(" ", "_"),
            "platform":   answers.get("platform", "browser"),
            "scope":      answers.get("scope", "indie"),
            "tech_stack": "typescript_pixijs",
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d"),
            "mode":       "CREATIVE"
        }
        self.write_manifest(manifest)

        await self.log("Synthesizing creative brief...")
        synthesis_prompt = (
            "You are a game designer and creative director.\n"
            "Write a creative brief for an AI game developer based on these intake answers.\n\n"
            f"ANSWERS:\n{json.dumps(answers, indent=2)}\n\n"
            "Write brief.md with sections: One-Line Pitch, Tone & Mood, Visual Language, "
            "Core Mechanic, World & Setting, Reference Points, MVP Definition.\n"
            "Be specific, evocative, and concrete. Output only the markdown."
        )

        response = await self.prompt_gemma(synthesis_prompt, temperature=0.7, num_ctx=4096)
        brief = response.strip() if response.strip() else \
                "\n".join(f"## {k.title()}\n{v}" for k, v in answers.items())
        self.write_agent_file(BRIEF_PATH, brief)

        await self.log("Bootstrap complete. Entering CREATIVE.")
        self.append_journal(
            f"Bootstrap complete.\n- Game: {manifest['name']}\n"
            f"- Type: {manifest['game_type']}\n- Art: {manifest['art_style']}")
        self.git_commit("init: bootstrap complete")

    # ── Mode: CREATIVE ────────────────────────────────────────────────────────

    async def run_creative_iteration(self, state: dict):
        """One iteration of the CREATIVE autonomous loop."""
        manifest = self.read_manifest()
        brief    = self.read_brief()

        phase_complete = os.path.join(WORKSPACE_DIR, "lore", "PHASE_COMPLETE.md")
        if os.path.exists(phase_complete):
            await self.log("PHASE_COMPLETE detected. Entering ART_DIRECTION.")
            self.set_mode("ART_DIRECTION")
            return

        human_feedback = self.read_feedback()
        if human_feedback:
            await self.push_human_message(human_feedback)
        pending = await self.fetch_pending_chat()
        if pending:
            human_feedback = (human_feedback + "\n" + "\n".join(pending)).strip()
        chat_history = await self.fetch_chat_history()
        reminders    = await self.fetch_reminders()

        lore_tree = subprocess.run(
            ["find", "lore", "-type", "f"],
            cwd=WORKSPACE_DIR, capture_output=True, text=True
        ).stdout.strip() or "(empty — start creating)"

        manifesto_path = os.path.join(ROOT_DIR, "manifesto.md")
        manifesto = open(manifesto_path).read() if os.path.exists(manifesto_path) else ""
        last_cmd  = self.read_last_output()[:2000]
        last_thought = state.get("last_thought", "")

        prompt = (
            manifesto + "\n\n"
            "[PHASE: CREATIVE — WORLD BUILDING, LORE & VISUAL DESIGN]\n"
            f"You are the author, world-builder, and art director for: **{manifest.get('name', 'the game')}**\n\n"
            f"GAME BRIEF:\n{brief[:1200]}\n\n"
            f"[LORE FILE TREE]\n{lore_tree}\n\n"
            f"[LAST COMMAND OUTPUT]\n{last_cmd}\n\n"
            f"[CHAT HISTORY]\n{chat_history}\n\n"
            + (f"[REMINDERS]\n{reminders}\n\n" if reminders else "")
            + (f"[MESSAGE FROM HUMAN]\n{human_feedback}\n\n" if human_feedback else "")
            + f"[LAST THOUGHT]\n{last_thought}\n\n"
            f"Iteration {self.iteration}. Think freely, then output ONE JSON action.\n\n"
            "Available tools: create_file, read_file, run_bash, chat_respond, update_state, "
            "add_reminder, generate_image, search_web, analyze_image.\n\n"
            "[CREATIVE PHASE — TWO PARTS]\n"
            "PART 1 — LORE: Write world-building documents until the world feels complete.\n"
            "PART 2 — VISUAL SCAFFOLD: Write minimal Phaser 3 TypeScript 'mood scenes' for each major zone.\n"
            "  A mood scene renders ONLY atmosphere: sky gradient, parallax layers, zone color palette, placeholder geometry.\n"
            "  Save to: src/scenes/mood/ZoneXxxMood.ts\n"
            "  After writing a mood scene, run it with run_bash: \"npx tsc --noEmit 2>&1\"\n"
            "  These become the visual skeleton BUILD tasks will flesh out.\n\n"
            "Declare creative phase complete (after lore AND at least 3 mood scenes): "
            '{"thought": "ready", "tool": "create_file", '
            '"filename": "lore/PHASE_COMPLETE.md", "content": "Creative phase complete."}\n'
            'Example: {"thought": "write the creation myth", "tool": "create_file", '
            '"filename": "lore/world/creation_myth.md", "content": "..."}\n'
        )
        if self.iteration % 20 == 0 and self.iteration > 0:
            prompt += "\n[SELF-CRITIQUE]: Re-read your most recent lore file and deepen what feels thin.\n"
        if self.iteration % 100 == 0 and self.iteration > 0:
            prompt += f"\n[PORTFOLIO UPDATE — ITER {self.iteration}]: Update lore/presentations/presentation_current.md.\n"

        await self.log("Querying Gemma (CREATIVE)...")
        response = await self.prompt_gemma(prompt, num_ctx=16384)
        await self.log(f"Gemma output:\n{response}")

        action = self._parse_json_action(response)
        if action:
            if action.get("thought"):
                await self.push_state("last_thought", action["thought"])
            await self._dispatch_creative_tool(action)
        else:
            await self.log("No valid JSON action found.")

    # ── Mode: ART_DIRECTION ───────────────────────────────────────────────────

    async def run_art_direction(self):
        """Multi-iteration phase: synthesise lore/visuals/ into a precise technical
        art spec (palette, sprite dimensions, asset manifest, concept images).
        Completes when Gemma writes lore/ART_DIRECTION_COMPLETE.md.
        """
        art_complete = os.path.join(WORKSPACE_DIR, "lore", "ART_DIRECTION_COMPLETE.md")
        if os.path.exists(art_complete):
            await self.log("ART_DIRECTION_COMPLETE detected. Entering ARCHITECT.")
            self.set_mode("ARCHITECT")
            return

        brief    = self.read_brief()
        manifest = self.read_manifest()

        lore_tree = subprocess.run(
            ["find", "lore", "-type", "f"],
            cwd=WORKSPACE_DIR, capture_output=True, text=True
        ).stdout.strip()

        # Ingest up to 6 key visual docs as context
        visuals_dir = os.path.join(WORKSPACE_DIR, "lore", "visuals")
        visual_docs = ""
        priority_docs = [
            os.path.join(visuals_dir, "biome_palettes.md"),
            os.path.join(visuals_dir, "STYLE_GUIDE_LUMINOUS_FORENSICISM.md"),
            os.path.join(visuals_dir, "STYLE_DECISION.md"),
            os.path.join(visuals_dir, "material_language.md"),
            os.path.join(visuals_dir, "silhouette_language.md"),
            os.path.join(visuals_dir, "architecture_silhouettes.md"),
        ]
        for p in priority_docs:
            if os.path.exists(p) and len(visual_docs) < 4000:
                try:
                    visual_docs += f"\n### {os.path.basename(p)}\n{open(p).read()[:800]}\n"
                except Exception:
                    pass

        # Check what art_direction files already exist
        ad_dir = os.path.join(WORKSPACE_DIR, "lore", "art_direction")
        ad_exists = os.listdir(ad_dir) if os.path.exists(ad_dir) else []

        last_cmd = self.read_last_output()[:1500]

        checklist = (
            "\n[ART DIRECTION DELIVERABLES CHECKLIST]\n"
            f"- lore/art_direction/palette.md         {'[DONE]' if 'palette.md' in ad_exists else '[TODO] — exact hex codes per zone + global UI palette'}\n"
            f"- lore/art_direction/sprite_spec.md     {'[DONE]' if 'sprite_spec.md' in ad_exists else '[TODO] — pixel dimensions (player, tiles, enemies, parallax), animation frame counts, naming convention'}\n"
            f"- lore/art_direction/asset_manifest.md  {'[DONE]' if 'asset_manifest.md' in ad_exists else '[TODO] — every asset filename BUILD tasks will reference (e.g. assets/sprites/player_walk.png)'}\n"
            f"- Concept images (3+)                   {'[DONE]' if len([f for f in ad_exists if f.endswith('.png')]) >= 3 else '[TODO] — generate 3-5 key zone/character images via generate_image'}\n"
            f"- lore/ART_DIRECTION_COMPLETE.md         [WRITE THIS LAST to advance to code phase]\n"
        )

        prompt = (
            "[PHASE: ART_DIRECTION — TECHNICAL ART SPECIFICATION]\n\n"
            f"You are the Art Director for **{manifest.get('name', 'Khoros')}**, "
            f"a {manifest.get('game_type', 'side-scroller')} built in Phaser 3.\n\n"
            "Your job is to translate the world's prose lore into a precise technical art "
            "specification that an engineer can implement directly as code constants and asset loads.\n\n"
            "## WHAT TO PRODUCE (in order):\n"
            "1. **`lore/art_direction/palette.md`** — Every color as a named constant with hex code. "
            "Format:\n   ```\n   RUST_ORANGE = #B7410E\n   VOID_SKY    = #A2CFFE\n   ```\n"
            "   Organised by zone (Grounded_Lowlands, High_Voltage_Wastes, Static_Mists, Suture_Cities) plus UI palette.\n\n"
            "2. **`lore/art_direction/sprite_spec.md`** — Technical sprite sheet specification:\n"
            "   - Player: WxH in pixels, walk/run/jump/idle frame counts, spritesheet layout\n"
            "   - Tiles: tile size in pixels (suggest 16x16 or 32x32), expected tileset dimensions\n"
            "   - Parallax layers: how many per zone, each layer size in pixels (suggest 1920x600)\n"
            "   - Named characters from the lore: size and animation frames for each\n"
            "   - Naming convention: snake_case, zone prefix (e.g. `lowlands_player_walk.png`)\n\n"
            "3. **`lore/art_direction/asset_manifest.md`** — Complete list of EVERY asset the game needs:\n"
            "   - One entry per line: `assets/sprites/lowlands_player_walk.png  (32x48, 8 frames)`\n"
            "   - Include: sprites, tilemaps, audio cues, UI elements, parallax layers\n\n"
            "4. **3-5 concept images** using `generate_image`, saved to `lore/art_direction/`.\n"
            "   Write highly specific ComfyUI prompts using the biome name, palette, lighting, silhouettes.\n\n"
            "5. **`lore/ART_DIRECTION_COMPLETE.md`** — write LAST, only when all above are done.\n\n"
            "## EXISTING VISUAL LORE (your raw material):\n"
            f"{visual_docs}\n\n"
            f"[LORE FILE TREE]\n{lore_tree}\n\n"
            f"[LAST COMMAND OUTPUT]\n{last_cmd}\n\n"
            f"[ART DIRECTION FILES SO FAR]: {ad_exists}\n"
            f"{checklist}\n"
            "Output ONE JSON action. Available tools: create_file, read_file, generate_image, run_bash, chat_respond.\n"
            'Example: {"thought": "write the palette spec", "tool": "create_file", '
            '"filename": "lore/art_direction/palette.md", "content": "..."}\n'
        )

        await self.log("Querying Gemma (ART_DIRECTION)...")
        response = await self.prompt_gemma(prompt, num_ctx=12288)
        await self.log(f"Gemma output:\n{response}")

        action = self._parse_json_action(response)
        if action:
            if action.get("thought"):
                await self.push_state("last_thought", action["thought"])
            await self._dispatch_creative_tool(action)
        else:
            await self.log("ART_DIRECTION: No valid JSON action found.")

    # ── Mode: ARCHITECT ───────────────────────────────────────────────────────

    async def run_architect(self):
        """One-shot: Gemma produces task_queue.md -> enters BUILD."""
        if os.path.exists(TASK_QUEUE_PATH):
            await self.log("task_queue.md exists. Entering BUILD.")
            self.set_mode("BUILD")
            return

        brief    = self.read_brief()
        manifest = self.read_manifest()

        src_tree = subprocess.run(
            ["find", "src", "-type", "f", "-name", "*.ts"],
            cwd=WORKSPACE_DIR, capture_output=True, text=True
        ).stdout.strip() or "(none — greenfield project)"

        specs_content = ""
        specs_dir = os.path.join(WORKSPACE_DIR, "specs")
        if os.path.exists(specs_dir):
            for root, _, files in os.walk(specs_dir):
                for f in files:
                    if f.endswith('.md') and len(specs_content) < 3000:
                        try:
                            specs_content += f"\n### {f}\n{open(os.path.join(root, f)).read()[:600]}\n"
                        except Exception:
                            pass

        # Read art direction specs — these make ARCHITECT tasks concrete
        art_direction_content = ""
        ad_dir = os.path.join(WORKSPACE_DIR, "lore", "art_direction")
        for ad_file in ["palette.md", "sprite_spec.md", "asset_manifest.md"]:
            ad_path = os.path.join(ad_dir, ad_file)
            if os.path.exists(ad_path):
                try:
                    art_direction_content += f"\n### lore/art_direction/{ad_file}\n{open(ad_path).read()[:1200]}\n"
                except Exception:
                    pass

        tech_stack = manifest.get('tech_stack', 'phaser3')
        is_phaser = 'phaser' in tech_stack.lower()
        phaser_guidance = """
## Phaser 3 Architecture (REQUIRED — follow exactly)
- Entry: `src/main.ts` — creates `new Phaser.Game(config)` with scene array
- Scenes live in `src/scenes/`. One file per scene. Each extends `Phaser.Scene`.
  - `BootScene.ts`    — config only, starts PreloadScene
  - `PreloadScene.ts` — all asset loads via this.load.*, starts GameScene
  - `GameScene.ts`    — hub/overworld or main zone
  - `ZoneXxxScene.ts` — one per world zone (e.g. ZoneRuinsScene.ts)
  - `UIScene.ts`      — parallel HUD scene (launched with mode: 'parallel')
  - `DialogueScene.ts` — NPC dialogue overlay
- Physics: arcade (gravity in main config). `this.physics.add.sprite/group/collider`
- Assets: `this.load.image/tilemapTiledJSON/audio` in PreloadScene preload()
- Camera: `this.cameras.main.setBounds().startFollow(player)`
- Input: `this.input.keyboard.createCursorKeys()` or `addKeys()`
- NPC souls: `data/souls/npc_name.json` — read via fetch() or Vite import
- NEVER import PixiJS. NEVER reference old CoreEngine/MovementSystem classes.
- Build check: `npx tsc --noEmit` must pass with zero errors.
- File size: max 400 lines per file. Split into helper modules if needed.
""" if is_phaser else ""

        await self.log("ARCHITECT: Gemma designing task_queue.md...")
        prompt = (
            "You are a senior game developer and architect.\n"
            "Design a complete ordered task queue for building this game with Phaser 3.\n\n"
            f"{phaser_guidance}\n"
            f"GAME BRIEF:\n{brief[:1500]}\n\n"
            f"TECH STACK: {tech_stack}\n"
            f"GAME TYPE:  {manifest.get('game_type')}\n"
            f"ART STYLE:  {manifest.get('art_style')}\n"
            f"SCOPE:      {manifest.get('scope', 'indie')}\n\n"
            f"EXISTING SOURCE FILES:\n{src_tree}\n\n"
            f"SPECS:\n{specs_content[:2000]}\n\n"
            + (f"ART DIRECTION (use these exact values in tasks — filenames, hex codes, dimensions):\n{art_direction_content}\n\n" if art_direction_content else "")
            + "Format each task EXACTLY like this:\n"
            "- [ ] **TASK-NNN**: Short title\n"
            "  - **Goal**: What this implements\n"
            "  - **Files**: Exact paths from project root (e.g. src/scenes/ZoneRuinsScene.ts)\n"
            "  - **Depends on**: TASK-NNN or none\n"
            "  - **Acceptance**: build passes / screenshot looks correct / NPC responds\n\n"
            "Task ordering: BootScene → PreloadScene → GameScene hub → first playable zone → "
            "player physics → UI/HUD → NPC dialogue → additional zones → polish.\n"
            f"Aim for 20-40 tasks for an {manifest.get('scope','epic')}-scope "
            f"{manifest.get('game_type','')} game.\n"
            "Name zone scenes after real places from the game's lore (from lore/ directory).\n"
            "Output only the task_queue.md markdown."
        )

        response = await self.prompt_gemma(prompt, temperature=0.4, num_ctx=8192)
        if response.strip():
            self.write_agent_file(TASK_QUEUE_PATH, response.strip())
            await self.log("task_queue.md written. Entering BUILD.")
            self.append_journal("ARCHITECT complete. Task queue generated.")
            self.set_mode("BUILD")
            self.git_commit("chore: architect phase complete — task_queue.md created")
        else:
            arch_retry_path = os.path.join(AGENT_DIR, "architect_retries.json")
            ar = json.loads(open(arch_retry_path).read()) if os.path.exists(arch_retry_path) else {"count": 0}
            ar["count"] = ar.get("count", 0) + 1
            self.write_agent_file(arch_retry_path, json.dumps(ar))
            await self.log(f"ARCHITECT: empty response (attempt {ar['count']}/3). Retrying.")
            if ar["count"] >= 3:
                # Auto-generate a minimal task queue so BUILD can start
                minimal_queue = (
                    "# Task Queue\n\n"
                    "- [ ] **TASK-001**: Bootstrap Phaser 3 entry point\n"
                    "  - **Goal**: src/main.ts creates Phaser.Game with BootScene, PreloadScene, GameScene\n"
                    "  - **Files**: `src/main.ts`, `src/scenes/BootScene.ts`, `src/scenes/PreloadScene.ts`\n"
                    "  - **Depends on**: none\n"
                    "  - **Acceptance**: build passes, blank canvas visible\n\n"
                    "- [ ] **TASK-002**: GameScene hub — player spawn and first zone\n"
                    "  - **Goal**: GameScene with arcade physics, placeholder player sprite, ground collider, camera follow\n"
                    "  - **Files**: `src/scenes/GameScene.ts`\n"
                    "  - **Depends on**: TASK-001\n"
                    "  - **Acceptance**: player falls onto ground, arrow keys move them\n"
                )
                self.write_agent_file(TASK_QUEUE_PATH, minimal_queue)
                if os.path.exists(arch_retry_path):
                    os.remove(arch_retry_path)
                await self.log("ARCHITECT: Auto-generated minimal task_queue.md after 3 failures. Entering BUILD.")
                self.append_journal("ARCHITECT: Auto-generated minimal task_queue after 3 empty responses.")
                self.set_mode("BUILD")

    # ── Mode: REPAIR ──────────────────────────────────────────────────────────

    async def run_repair_iteration(self, state: dict):
        """One SAR iteration: deterministic triage + targeted LLM rewrite per file."""
        brief = self.read_brief()

        await self.log("REPAIR: Running tsc --noEmit...")
        _, out, err = await self.execute_native("npx tsc --noEmit 2>&1", timeout=120)
        combined = (out + err).strip()

        if not combined or "error TS" not in combined:
            prior = state.get("pre_repair_mode", "BUILD")
            await self.log(f"REPAIR: Build clean! Returning to {prior}.")
            self.set_mode(prior)
            await self.push_state("mode", prior)
            self.clear_repair_retries()
            self.append_journal("REPAIR complete. Build is clean.")
            self.git_commit("fix: all TypeScript errors resolved")
            return

        error_map = parse_ts_errors(combined)
        total     = sum(len(v) for v in error_map.values())
        await self.log(f"REPAIR: {total} errors across {len(error_map)} files.")
        await self.push_state("last_build_result", f"{total} errors in {len(error_map)} files")

        ordered      = build_repair_order(error_map)
        retry_counts = self.read_repair_retries()

        # Stuck files stored locally (dashboard state is not reliable for persistence)
        stuck_path   = os.path.join(AGENT_DIR, "stuck_files.json")
        stuck_files  = set(json.loads(open(stuck_path).read()) if os.path.exists(stuck_path) else [])

        target_file  = next(
            (f for f in ordered if f not in stuck_files),
            None
        )

        if target_file is None:
            # All remaining errored files are stuck — move forward rather than spinning
            stuck_list = ", ".join(ordered)
            prior = state.get("pre_repair_mode", "BUILD")
            await self.log(f"REPAIR: All stuck ({stuck_list}). Giving up and returning to {prior}.")
            await self.push_chat(
                f"Exhausted all retries. Giving up on: {stuck_list}. "
                f"Moving on to {prior} with {total} errors remaining. Human review advised.")
            self.append_journal(f"REPAIR: Gave up on {stuck_list}. Entered {prior} with {total} errors.")
            self.set_mode(prior)
            # Do NOT clear stuck_files — BUILD needs it to skip these files
            self.clear_repair_retries()
            return

        errors = error_map[target_file]
        retry  = retry_counts.get(target_file, 0)
        await self.log(f"REPAIR: Targeting {target_file} ({len(errors)} errors, attempt {retry+1}/{MAX_RETRIES})")

        # Step 1: trivial auto-fixes
        changed, new_content = attempt_trivial_fixes(target_file, errors)
        if changed:
            open(os.path.join(WORKSPACE_DIR, target_file), 'w').write(new_content)
            await self.log(f"REPAIR: Applied trivial fixes to {target_file}")
            _, out2, err2 = await self.execute_native("npx tsc --noEmit 2>&1", timeout=120)
            remaining = parse_ts_errors((out2 + err2).strip()).get(target_file, [])
            if not remaining:
                await self.log(f"REPAIR: {target_file} fully resolved by trivial fixes.")
                self.append_journal(f"REPAIR: Auto-fixed {target_file} ({len(errors)} trivial errors).")
                retry_counts.pop(target_file, None)
                self.write_repair_retries(retry_counts)
                return
            errors = remaining

        # Step 2: assemble context (target + direct imports, capped at 5 context files)
        # On retry >= 2 with no progress, drop context files — Gemma gets overwhelmed
        import_paths  = resolve_imports(target_file)
        max_ctx_files = 0 if retry >= 2 else 3
        ctx_char_limit = 2000 if retry >= 1 else 4000
        context_files = {}
        for path in [target_file] + import_paths[:max_ctx_files]:
            full = os.path.join(WORKSPACE_DIR, path)
            if os.path.exists(full):
                content = open(full).read()
                context_files[path] = content[:ctx_char_limit] + "\n...[TRUNCATED]..." if len(content) > ctx_char_limit else content

        # Step 3: build task card and call Gemma
        # Check if this file is flagged for spec-rewrite (nuke) mode
        nuke_path  = os.path.join(AGENT_DIR, "nuke_files.json")
        nuke_files = set(json.loads(open(nuke_path).read()) if os.path.exists(nuke_path) else [])
        if target_file in nuke_files:
            await self.log(f"REPAIR: {target_file} is in NUKE mode — rewriting from spec.")
            task_card = assemble_nuke_prompt(target_file, errors, brief)
        else:
            pixi_hints      = lookup_pixi_type_hints(errors)
            conflict_hints  = lookup_conflicting_type_hints(target_file, errors)
            prev_err_path   = os.path.join(AGENT_DIR, f"prev_errors_{target_file.replace('/', '_')}.json")
            previous_new_errors = json.loads(open(prev_err_path).read()) if os.path.exists(prev_err_path) else None
            task_card = assemble_task_card(target_file, errors, context_files, brief, retry, previous_new_errors, full_tsc_output=combined) + pixi_hints + conflict_hints
            if pixi_hints:
                await self.log(f"REPAIR: Injected framework type hints for {target_file}.")
            if conflict_hints:
                await self.log(f"REPAIR: Injected type-conflict hints for {target_file}.")
        await self.log(f"REPAIR: Calling Gemma for {target_file} ({len(context_files)} ctx files, retry={retry}, nuke={'yes' if target_file in nuke_files else 'no'})...")
        response = await self.prompt_gemma(task_card, temperature=0.2, max_tokens=8192, num_ctx=16384)

        # Step 4: extract code block
        new_code = extract_code_block(response)
        await self.log(f"REPAIR: Response length={len(response)} chars, code extracted={len(new_code)} chars.")
        if not new_code:
            await self.log(f"REPAIR: No code block returned for {target_file}. Incrementing retry.")
            retry_counts[target_file] = retry + 1
            self.write_repair_retries(retry_counts)
            # On repeated no-code responses, try without context files next time
            if retry + 1 >= 2:
                await self.log(f"REPAIR: 2+ no-code responses for {target_file} — next attempt will strip context.")
            # Auto-stuck if we've hit MAX_RETRIES with no code at all
            if retry_counts[target_file] >= MAX_RETRIES:
                stuck_files.add(target_file)
                self.write_agent_file(stuck_path, json.dumps(list(stuck_files)))
                retry_counts.pop(target_file, None)
                self.write_repair_retries(retry_counts)
                await self.log(f"REPAIR: {target_file} auto-marked stuck after {MAX_RETRIES} no-code responses.")
                self.append_journal(f"REPAIR: Auto-stuck {target_file} — never produced a code block.")
            return

        # Step 5: sanity check — don't lose exports
        full_path    = os.path.join(WORKSPACE_DIR, target_file)
        original     = open(full_path).read() if os.path.exists(full_path) else ""
        orig_exports = set(re.findall(r'export (?:class|interface|type|enum|function|const) (\w+)', original))
        new_exports  = set(re.findall(r'export (?:class|interface|type|enum|function|const) (\w+)', new_code))
        missing      = orig_exports - new_exports
        if missing:
            await self.log(f"REPAIR: Sanity fail — missing exports {missing}. Incrementing retry.")
            retry_counts[target_file] = retry + 1
            self.write_repair_retries(retry_counts)
            if retry_counts[target_file] >= MAX_RETRIES:
                stuck_files.add(target_file)
                self.write_agent_file(stuck_path, json.dumps(list(stuck_files)))
                retry_counts.pop(target_file, None)
                self.write_repair_retries(retry_counts)
                await self.log(f"REPAIR: {target_file} auto-marked stuck after {MAX_RETRIES} sanity failures — skipping.")
                self.append_journal(f"REPAIR: Auto-stuck {target_file} after {MAX_RETRIES} sanity failures.")
                await self.push_chat(
                    f"⚠️ REPAIR: Auto-skipped `{target_file}` after {MAX_RETRIES} sanity failures. "
                    f"Added to stuck_files.json — continuing.")
            return

        # Step 5b: brace-balance check — reject truncated output
        open_braces  = new_code.count('{') - new_code.count('\\{')
        close_braces = new_code.count('}') - new_code.count('\\}')
        if open_braces != close_braces:
            await self.log(f"REPAIR: Brace mismatch ({open_braces} open vs {close_braces} close) — likely truncated. Incrementing retry.")
            retry_counts[target_file] = retry + 1
            self.write_repair_retries(retry_counts)
            if retry_counts[target_file] >= MAX_RETRIES:
                stuck_files.add(target_file)
                self.write_agent_file(stuck_path, json.dumps(list(stuck_files)))
                retry_counts.pop(target_file, None)
                self.write_repair_retries(retry_counts)
                await self.log(f"REPAIR: {target_file} auto-marked stuck after {MAX_RETRIES} brace mismatches — skipping.")
                self.append_journal(f"REPAIR: Auto-stuck {target_file} after {MAX_RETRIES} brace mismatches.")
                await self.push_chat(
                    f"⚠️ REPAIR: Auto-skipped `{target_file}` after {MAX_RETRIES} brace mismatches. "
                    f"Added to stuck_files.json — continuing.")
            return

        # Step 6: write new content
        open(full_path, 'w').write(new_code)
        await self.log(f"REPAIR: Wrote new {target_file}")

        # Step 7: verify — compare error count
        _, out3, err3 = await self.execute_native("npx tsc --noEmit 2>&1", timeout=120)
        new_map   = parse_ts_errors((out3 + err3).strip())
        new_total = sum(len(v) for v in new_map.values())

        if new_total < total:
            delta = total - new_total
            await self.log(f"REPAIR: {target_file} fixed. Errors: {total} -> {new_total} ({delta} resolved)")
            retry_counts.pop(target_file, None)
            # Un-stick any file that no longer has errors (unblocked by this fix)
            stuck_files = {f for f in stuck_files if f in new_map}
            self.write_agent_file(stuck_path, json.dumps(list(stuck_files)))
            # Clear nuke flag on success
            nuke_path  = os.path.join(AGENT_DIR, "nuke_files.json")
            nuke_files = set(json.loads(open(nuke_path).read()) if os.path.exists(nuke_path) else [])
            nuke_files.discard(target_file)
            self.write_agent_file(nuke_path, json.dumps(list(nuke_files)))
            self.write_repair_retries(retry_counts)
            await self.push_state("last_build_result", f"{new_total} errors remaining")
            self.append_journal(
                f"REPAIR: Fixed {target_file}.\n"
                f"- Errors: {total}->{new_total} ({delta} resolved)\n"
                f"- Files remaining with errors: {len(new_map)}")
            self.git_commit(f"fix: {target_file} — {delta} TS errors resolved ({new_total} remain)")
            await self.push_action("repair_fix", target_file, "ok")
        elif new_map.get(target_file) and len(new_map.get(target_file, [])) < len(errors):
            # Target file improved but other files got worse — accept the fix, let REPAIR handle spillover
            delta = total - new_total
            await self.log(f"REPAIR: {target_file} improved (target errors reduced) despite net {total}->{new_total}. Accepting.")
            retry_counts.pop(target_file, None)
            stuck_files = {f for f in stuck_files if f in new_map}
            self.write_agent_file(stuck_path, json.dumps(list(stuck_files)))
            nuke_path  = os.path.join(AGENT_DIR, "nuke_files.json")
            nuke_files = set(json.loads(open(nuke_path).read()) if os.path.exists(nuke_path) else [])
            nuke_files.discard(target_file)
            self.write_agent_file(nuke_path, json.dumps(list(nuke_files)))
            self.write_repair_retries(retry_counts)
            await self.push_state("last_build_result", f"{new_total} errors remaining")
            self.append_journal(f"REPAIR: Partial fix {target_file}. {total}->{new_total}.")
            self.git_commit(f"fix: {target_file} — partial improvement ({new_total} remain)")
            await self.push_action("repair_fix", target_file, "ok")
        else:
            await self.log(f"REPAIR: {target_file} unchanged ({total}->{new_total}). Rolling back.")
            # Persist the new errors Gemma introduced so next attempt can learn from them
            new_errors_in_other_files = {f: v for f, v in new_map.items() if f not in error_map or len(v) > len(error_map.get(f, []))}
            prev_err_path = os.path.join(AGENT_DIR, f"prev_errors_{target_file.replace('/', '_')}.json")
            self.write_agent_file(prev_err_path, json.dumps(new_errors_in_other_files))
            self.git_rollback_file(target_file)
            retry_counts[target_file] = retry + 1
            self.write_repair_retries(retry_counts)
            self.append_journal(
                f"REPAIR: Failed {target_file} attempt {retry+1}. {total}->{new_total}. Rolled back.")
            await self.push_action("repair_fix", target_file, "fail")
            # After MAX_RETRIES failures, auto-mark as stuck — never rely on human to unblock
            if retry_counts[target_file] >= MAX_RETRIES:
                stuck_files.add(target_file)
                self.write_agent_file(stuck_path, json.dumps(list(stuck_files)))
                retry_counts.pop(target_file, None)
                self.write_repair_retries(retry_counts)
                await self.log(f"REPAIR: {target_file} auto-marked stuck after {MAX_RETRIES} attempts — skipping.")
                self.append_journal(f"REPAIR: Auto-stuck {target_file} after {MAX_RETRIES} failed attempts.")
                await self.push_chat(
                    f"⚠️ REPAIR: Auto-skipped `{target_file}` after {MAX_RETRIES} failed attempts. "
                    f"Added to stuck_files.json — continuing."
                )

    # ── Mode: BUILD ───────────────────────────────────────────────────────────

    async def run_build_iteration(self, state: dict):
        """PLAN turn -> EXECUTE turn. Build-check triggers REPAIR if broken."""
        # Health check — skip REPAIR if every errored file is already stuck
        _, out, err = await self.execute_native("npx tsc --noEmit 2>&1", timeout=120)
        combined = (out + err).strip()
        if combined and "error TS" in combined:
            error_map  = parse_ts_errors(combined)
            total      = sum(len(v) for v in error_map.values())
            stuck_path = os.path.join(AGENT_DIR, "stuck_files.json")
            stuck_files = set(json.loads(open(stuck_path).read()) if os.path.exists(stuck_path) else [])
            unstuck_errors = {f for f in error_map if f not in stuck_files}
            if unstuck_errors:
                await self.log(f"BUILD: Build broken ({total} errors). Entering REPAIR.")
                await self.push_state("pre_repair_mode", "BUILD")
                self.set_mode("REPAIR")
                return
            else:
                # All remaining errors are in stuck files — unstick them and try again.
                # Round 1: normal incremental repair.
                # Round 2+: escalate to 'nuke and rebuild from spec' mode.
                unstick_path = os.path.join(AGENT_DIR, "unstick_rounds.json")
                unstick_data = json.loads(open(unstick_path).read()) if os.path.exists(unstick_path) else {}
                round_key = ",".join(sorted(stuck_files & set(error_map.keys())))
                round_count = unstick_data.get(round_key, 0)

                files_to_retry = stuck_files & set(error_map.keys())
                await self.log(f"BUILD: {total} errors in stuck files — unsticking {len(files_to_retry)} files (round {round_count + 1}).")

                # On round 2+, escalate: mark files for full rewrite from spec
                if round_count >= 1:
                    nuke_path = os.path.join(AGENT_DIR, "nuke_files.json")
                    nuke_files = set(json.loads(open(nuke_path).read()) if os.path.exists(nuke_path) else [])
                    nuke_files.update(files_to_retry)
                    self.write_agent_file(nuke_path, json.dumps(list(nuke_files)))
                    await self.log(f"BUILD: Round {round_count + 1} — escalating to spec-rewrite mode for: {', '.join(files_to_retry)}")

                for f in files_to_retry:
                    stuck_files.discard(f)
                self.write_agent_file(stuck_path, json.dumps(list(stuck_files)))
                retry_counts = self.read_repair_retries()
                for f in files_to_retry:
                    retry_counts.pop(f, None)
                self.write_repair_retries(retry_counts)
                unstick_data[round_key] = round_count + 1
                self.write_agent_file(unstick_path, json.dumps(unstick_data))
                await self.push_state("pre_repair_mode", "BUILD")
                self.set_mode("REPAIR")
                return

        # Ensure task queue exists
        task_queue = self.read_agent_file(TASK_QUEUE_PATH)
        if not task_queue:
            await self.log("BUILD: No task_queue.md. Entering ARCHITECT.")
            self.set_mode("ARCHITECT")
            return

        # Find next open task
        unchecked = re.findall(r'- \[ \] \*\*([^*]+)\*\*: (.+)', task_queue)
        if not unchecked:
            await self.log("BUILD: All tasks complete! Entering PLAYTEST.")
            self.set_mode("PLAYTEST")
            self.append_journal("BUILD complete. All tasks checked off.")
            self.git_commit("feat: all BUILD tasks complete")
            return

        task_id, task_title = unchecked[0][0].strip(), unchecked[0][1].strip()
        await self.log(f"BUILD: {task_id}: {task_title}")
        await self.push_state("current_task", f"{task_id}: {task_title}")

        # Parse expected files up front — used by both PLAN fallback and EXECUTE branch
        _tsect = re.search(
            r'- \[ \] \*\*' + re.escape(task_id) + r'\*\*.*?(?=\n- \[|\Z)',
            task_queue, re.DOTALL)
        expected_files: list = []
        if _tsect:
            _fm = re.search(r'\*\*Files\*\*:(.+?)$', _tsect.group(), re.MULTILINE)
            if _fm:
                expected_files = re.findall(r'`(src/[^`]+\.ts)`', _fm.group(1))

        brief          = self.read_brief()
        manifest       = self.read_manifest()
        journal_tail   = self.read_agent_file(JOURNAL_PATH, "")[-2000:]
        current_plan   = self.read_agent_file(PLAN_PATH, "")
        human_feedback = self.read_feedback()
        if human_feedback:
            await self.push_human_message(human_feedback)
        chat_history = await self.fetch_chat_history()

        src_tree = subprocess.run(
            ["find", "src", "-type", "f", "-name", "*.ts"],
            cwd=WORKSPACE_DIR, capture_output=True, text=True
        ).stdout.strip()

        if not current_plan:
            # ── PLAN TURN ─────────────────────────────────────────────────
            await self.log(f"BUILD: PLAN turn for {task_id}")
            prompt = (
                "You are a senior TypeScript/PixiJS game developer.\n"
                "Produce a precise implementation plan for the next task.\n\n"
                f"GAME: {manifest.get('name')} ({manifest.get('game_type')} / {manifest.get('art_style')})\n"
                f"BRIEF: {brief[:800]}\n\n"
                f"CURRENT TASK: **{task_id}**: {task_title}\n\n"
                f"TASK QUEUE (context):\n{task_queue[:2000]}\n\n"
                f"RECENT JOURNAL:\n{journal_tail}\n\n"
                f"SOURCE FILE TREE:\n{src_tree}\n\n"
                + (f"[HUMAN MESSAGE]\n{human_feedback}\n\n" if human_feedback else "")
                + "Write an implementation plan between ```plan ... ``` fences:\n"
                "- Exact files to create or modify (full src/ paths)\n"
                "- Per-file: what changes and why\n"
                "- New types/interfaces/exports needed\n"
                "- Order of changes\n"
                "- Acceptance criteria"
            )

            response = await self.prompt_gemma(prompt, temperature=0.4, num_ctx=8192)
            m = re.search(r'```plan\n(.*?)```', response, re.DOTALL)
            plan_content = m.group(1).strip() if m else response.strip()
            # Validate the plan actually mentions at least one source file
            has_files = bool(re.search(r'src/\S+\.ts', plan_content))
            if plan_content and has_files:
                self.write_agent_file(PLAN_PATH, f"# Plan: {task_id} — {task_title}\n\n{plan_content}")
                # Reset file-tracking for the new task's EXECUTE turns
                written_path = os.path.join(AGENT_DIR, "task_written_files.json")
                self.write_agent_file(written_path, "[]")
                await self.log(f"BUILD: Plan written for {task_id}")
                await self.push_action("plan", task_id)
            else:
                plan_retry_path = os.path.join(AGENT_DIR, "plan_retries.json")
                pr = json.loads(open(plan_retry_path).read()) if os.path.exists(plan_retry_path) else {}
                fail_count = pr.get(task_id, 0) + 1
                pr[task_id] = fail_count
                self.write_agent_file(plan_retry_path, json.dumps(pr))
                await self.log(f"BUILD: PLAN turn bad output (attempt {fail_count}/3) — retrying.")
                if fail_count >= 3:
                    file_list = "\n".join(f"- Create/update `{f}`" for f in expected_files) if expected_files \
                        else "- Implement this task from scratch."
                    fallback = (
                        f"## Task: {task_id} — {task_title}\n\n"
                        f"Files to implement:\n{file_list}\n\n"
                        f"Write correct TypeScript/PixiJS implementations for each file.\n"
                        f"Ensure the build passes with `npx tsc --noEmit`."
                    )
                    self.write_agent_file(PLAN_PATH, f"# Plan: {task_id} — {task_title}\n\n{fallback}")
                    written_path = os.path.join(AGENT_DIR, "task_written_files.json")
                    self.write_agent_file(written_path, "[]")
                    pr[task_id] = 0
                    self.write_agent_file(plan_retry_path, json.dumps(pr))
                    await self.log(f"BUILD: Auto-generated fallback plan for {task_id} after {fail_count} failed PLAN turns.")

        else:
            # ── EXECUTE TURN ──────────────────────────────────────────────
            # expected_files already parsed above (before PLAN/EXECUTE branch)
            written_path  = os.path.join(AGENT_DIR, "task_written_files.json")
            written_files: list = json.loads(open(written_path).read()) if os.path.exists(written_path) else []
            remaining     = [f for f in expected_files if f not in written_files]

            # If all expected files are written, check build and auto-complete
            if expected_files and not remaining:
                _, out, err = await self.execute_native("npx tsc --noEmit 2>&1", timeout=120)
                combined = (out + err).strip()
                stuck_path  = os.path.join(AGENT_DIR, "stuck_files.json")
                stuck_files = set(json.loads(open(stuck_path).read()) if os.path.exists(stuck_path) else [])
                def _build_clean_local(tsc_output):
                    if "error TS" not in tsc_output: return True
                    return all(f in stuck_files for f in parse_ts_errors(tsc_output))
                if _build_clean_local(combined):
                    await self.log(f"BUILD: All files written, build clean — completing {task_id}.")
                    return await self._complete_task(task_id, task_title, task_queue)
                else:
                    # Real errors in newly written files — enter REPAIR to fix them
                    await self.log(f"BUILD: Written files have errors — entering REPAIR.")
                    await self.push_state("pre_repair_mode", "BUILD")
                    self.set_mode("REPAIR")
                    return

            # Determine which file to write next
            target_file = remaining[0] if remaining else (
                # No expected_files in task_queue: ask Gemma to decide
                None
            )

            await self.log(f"BUILD: EXECUTE turn for {task_id}" + (f" → {target_file}" if target_file else ""))

            # Load current content of target file for context
            current_content = ""
            if target_file:
                full = os.path.join(WORKSPACE_DIR, target_file)
                if os.path.exists(full):
                    current_content = open(full).read()
                    if len(current_content) > 8000:
                        current_content = current_content[:8000] + "\n...[TRUNCATED]..."

            # Also load other files mentioned in the plan for context
            mentioned = list(dict.fromkeys(re.findall(r'src/[\w/.-]+\.ts', current_plan)))
            if target_file and target_file in mentioned:
                mentioned.remove(target_file)
            context_block = ""
            for f in mentioned[:4]:
                full = os.path.join(WORKSPACE_DIR, f)
                if os.path.exists(full):
                    content = open(full).read()
                    if len(content) > 3000:
                        content = content[:3000] + "\n...[TRUNCATED]..."
                    context_block += f"\n{'='*50}\n{f}\n{'='*50}\n{content}\n"

            last_cmd = self.read_last_output()[:1500]
            stuck_path  = os.path.join(AGENT_DIR, "stuck_files.json")
            stuck_files = json.loads(open(stuck_path).read()) if os.path.exists(stuck_path) else []
            stuck_note  = (
                "IMPORTANT: The following file(s) have known permanent errors — "
                "IGNORE any TS errors reported for them:\n"
                + "\n".join(f"  - {f}" for f in stuck_files)
            ) if stuck_files else ""

            if target_file:
                # Code-fence mode: ask Gemma to write one specific file
                # This avoids JSON truncation for large files entirely
                prompt = (
                    "You are a senior TypeScript/PixiJS game developer.\n"
                    f"Write the complete implementation of `{target_file}`.\n"
                    "Output ONLY a ```typescript code fence containing the full file.\n"
                    "No JSON. No explanation. No text before or after the fence.\n\n"
                    f"GAME: {manifest.get('name')} ({manifest.get('game_type')} / {manifest.get('art_style')})\n\n"
                    + (f"{stuck_note}\n\n" if stuck_note else "")
                    + f"IMPLEMENTATION PLAN:\n{current_plan}\n\n"
                    + (f"CURRENT CONTENT OF {target_file}:\n{current_content}\n\n" if current_content else
                       f"`{target_file}` does not exist yet — write from scratch.\n\n")
                    + (f"OTHER FILES FOR CONTEXT:\n{context_block}\n\n" if context_block else "")
                    + f"LAST BUILD OUTPUT:\n{last_cmd}\n"
                    + (f"\n[HUMAN MESSAGE]\n{human_feedback}\n" if human_feedback else "")
                )
                response = await self.prompt_gemma(prompt, temperature=0.3, json_mode=False, max_tokens=8192, num_ctx=32768)
                new_code = extract_code_block(response)
                if not new_code:
                    await self.log(f"BUILD: No code fence in response for {target_file}.")
                    await self.set_last_output(
                        "ERROR: Output must be ONLY a ```typescript ... ``` code fence "
                        f"containing the full implementation of {target_file}. No JSON, no prose.")
                    return
                # Brace-balance sanity check — after 3 mismatches write anyway and let REPAIR fix
                if new_code.count('{') != new_code.count('}'):
                    brace_fail_path = os.path.join(AGENT_DIR, "brace_failures.json")
                    bf = json.loads(open(brace_fail_path).read()) if os.path.exists(brace_fail_path) else {}
                    bf[target_file] = bf.get(target_file, 0) + 1
                    self.write_agent_file(brace_fail_path, json.dumps(bf))
                    if bf[target_file] >= 3:
                        await self.log(f"BUILD: {bf[target_file]} brace mismatches for {target_file} — writing anyway, REPAIR will fix.")
                        bf.pop(target_file, None)
                        self.write_agent_file(brace_fail_path, json.dumps(bf))
                        # fall through to write the file
                    else:
                        await self.log(f"BUILD: Brace mismatch in {target_file} (attempt {bf[target_file]}/3) — retrying.")
                        await self.set_last_output(
                            f"ERROR: Your output for {target_file} was truncated (brace mismatch). "
                            "The file must be complete. Write a shorter implementation if needed.")
                        return
                full = os.path.join(WORKSPACE_DIR, target_file)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                open(full, 'w').write(new_code)
                await self.log(f"BUILD: Wrote {target_file}")
                await self.push_action("create_file", target_file)
                if target_file not in written_files:
                    written_files.append(target_file)
                    self.write_agent_file(written_path, json.dumps(written_files))
                _, out, err = await self.execute_native("npx tsc --noEmit 2>&1", timeout=120)
                combined = (out + err).strip()
                new_errors = combined.count('error TS')
                await self.push_state("last_build_result", f"{new_errors} errors")
                stuck_path_check = os.path.join(AGENT_DIR, "stuck_files.json")
                stuck_set = set(json.loads(open(stuck_path_check).read()) if os.path.exists(stuck_path_check) else [])
                def _clean(tsc):
                    if "error TS" not in tsc: return True
                    return all(f in stuck_set for f in parse_ts_errors(tsc))
                remaining_now = [f for f in expected_files if f not in written_files]
                if _clean(combined) and not remaining_now:
                    await self.log(f"BUILD: All files written and build clean — auto-completing {task_id}.")
                    return await self._complete_task(task_id, task_title, task_queue)
                elif _clean(combined):
                    await self.set_last_output(f"Wrote {target_file}. Build clean. Next: {remaining_now[0]}")
                else:
                    await self.set_last_output(f"Wrote {target_file}. Build errors:\n{combined[:2000]}")
            else:
                # No expected_files specified in task_queue — Gemma decides via JSON (small payload)
                prompt = (
                    "You are a senior TypeScript/PixiJS game developer implementing a game task.\n"
                    "Respond with a SINGLE JSON object — no markdown, no prose.\n\n"
                    f"GAME: {manifest.get('name')} ({manifest.get('game_type')} / {manifest.get('art_style')})\n\n"
                    + (f"{stuck_note}\n\n" if stuck_note else "")
                    + f"IMPLEMENTATION PLAN:\n{current_plan}\n\n"
                    + (f"OTHER FILES:\n{context_block}\n\n" if context_block else "")
                    + f"LAST OUTPUT:\n{last_cmd}\n\n"
                    + (f"[HUMAN MESSAGE]\n{human_feedback}\n\n" if human_feedback else "")
                    + 'Output: {"tool": "create_file", "filename": "src/...", "content": "..."} '
                    'or {"tool": "run_build"} when done.'
                )
                response = await self.prompt_gemma(prompt, temperature=0.3, json_mode=True, num_ctx=8192)
                await self.log(f"Gemma output:\n{response[:300]}...")
                action = self._parse_json_action(response)
                if action:
                    if action.get("thought"):
                        await self.push_state("last_thought", action["thought"])
                    # Clear consecutive JSON failure counter on success
                    json_fail_path = os.path.join(AGENT_DIR, "execute_json_failures.json")
                    if os.path.exists(json_fail_path):
                        os.remove(json_fail_path)
                    done = await self._dispatch_build_tool(action, task_id, task_title, task_queue, expected_files)
                    if done:
                        return await self._complete_task(task_id, task_title, task_queue)
                else:
                    await self.log("BUILD: No valid action in EXECUTE response.")
                    json_fail_path = os.path.join(AGENT_DIR, "execute_json_failures.json")
                    jf = json.loads(open(json_fail_path).read()) if os.path.exists(json_fail_path) else {"count": 0, "task": ""}
                    if jf.get("task") != task_id:
                        jf = {"count": 0, "task": task_id}
                    jf["count"] += 1
                    self.write_agent_file(json_fail_path, json.dumps(jf))
                    if jf["count"] >= 3:
                        await self.log(f"BUILD: {jf['count']} consecutive JSON parse failures in EXECUTE — clearing plan to re-plan.")
                        if os.path.exists(PLAN_PATH):
                            os.remove(PLAN_PATH)
                        if os.path.exists(json_fail_path):
                            os.remove(json_fail_path)
                    else:
                        await self.set_last_output(
                            "ERROR: Respond with a single JSON object: "
                            '{"tool": "create_file", "filename": "src/...", "content": "..."}')

    async def _complete_task(self, task_id: str, task_title: str, task_queue: str):
        """Mark task complete, commit, and clean up plan/written_files."""
        if os.path.exists(PLAN_PATH):
            os.remove(PLAN_PATH)
        written_path = os.path.join(AGENT_DIR, "task_written_files.json")
        if os.path.exists(written_path):
            os.remove(written_path)
        new_queue = re.sub(
            r'- \[ \] \*\*' + re.escape(task_id) + r'\*\*',
            f'- [x] **{task_id}**', task_queue, count=1)
        self.write_agent_file(TASK_QUEUE_PATH, new_queue)
        await self.log(f"BUILD: {task_id} complete!")
        self.append_journal(f"BUILD: Completed {task_id} — {task_title}.")
        self.git_commit(f"feat({task_id}): {task_title}")
        await self.push_action("task_complete", task_id, "ok")

        # Take a screenshot every 5 completed tasks so the Observation Deck shows progress
        completed_count = len(re.findall(r'- \[x\]', self.read_agent_file(TASK_QUEUE_PATH) or ''))
        if completed_count % 5 == 0:
            shot_path = os.path.join(AGENT_DIR, "build_screenshot.png")
            rc, _, _ = await self.execute_native(f"node capture_screenshot.js {shot_path}", timeout=30)
            if os.path.exists(shot_path):
                await self.push_screenshot(shot_path)
                await self.log(f"BUILD: Screenshot captured at {completed_count} tasks complete.")

    # ── Mode: PLAYTEST ────────────────────────────────────────────────────────

    async def run_playtest_iteration(self, state: dict):
        """Capture screenshot -> Gemma evaluates -> appends new tasks -> returns to BUILD."""
        await self.log("PLAYTEST: Capturing screenshot...")
        screenshot_path = os.path.join(AGENT_DIR, "playtest_screenshot.png")
        console_path    = screenshot_path.replace('.png', '_console.json')
        rc, out, err = await self.execute_native(f"node capture_screenshot.js {screenshot_path}", timeout=30)
        has_screenshot = os.path.exists(screenshot_path)
        if has_screenshot:
            await self.push_screenshot(screenshot_path)

        # Read console output captured by the screenshot script
        console_note = ""
        if os.path.exists(console_path):
            try:
                console_data = json.loads(open(console_path).read())
                logs   = console_data.get('logs', [])
                errors = console_data.get('errors', [])
                if errors:
                    console_note += "\nBROWSER ERRORS (uncaught exceptions / navigation failures):\n"
                    console_note += "\n".join(f"  [{e['type']}] {e['text']}" for e in errors[:20])
                if logs:
                    # Only show warnings and errors from console, not routine logs
                    important = [l for l in logs if l['type'] in ('error', 'warning', 'warn')]
                    all_logs  = logs if not important else important
                    console_note += f"\nBROWSER CONSOLE ({len(logs)} total messages, showing {'warnings/errors' if important else 'all'}):\n"
                    console_note += "\n".join(f"  [{l['type']}] {l['text']}" for l in all_logs[:30])
                if not logs and not errors:
                    console_note = "\nBROWSER CONSOLE: No output recorded — the app may not be initialising at all."
            except Exception:
                pass

        brief    = self.read_brief()
        manifest = self.read_manifest()
        task_queue = self.read_agent_file(TASK_QUEUE_PATH, "")
        journal_tail = self.read_agent_file(JOURNAL_PATH, "")[-2000:]

        # Count existing tasks to number new ones sequentially
        existing_ids = re.findall(r'TASK-(\d+)', task_queue)
        next_num = max((int(n) for n in existing_ids), default=0) + 1

        screenshot_note = (
            "A screenshot of the current game state has been captured and is shown above.\n"
            if has_screenshot else
            "NOTE: Screenshot capture failed — evaluate based on build status and journal only.\n"
        )

        prompt = (
            "You are a game director doing a playtest review.\n"
            f"{screenshot_note}\n"
            + (f"{console_note}\n" if console_note else "")
            + f"GAME: {manifest.get('name')} ({manifest.get('game_type')} / {manifest.get('art_style')})\n"
            f"BRIEF:\n{brief[:600]}\n\n"
            f"RECENT JOURNAL:\n{journal_tail}\n\n"
            f"COMPLETED TASKS:\n{task_queue[:2000]}\n\n"
            "Your job: identify what is visually broken, missing, or needs polish based on the screenshot and build history.\n"
            "Generate 3-8 new actionable tasks to improve the game.\n\n"
            f"Format each task (starting from TASK-{next_num:03d}):\n"
            "- [ ] **TASK-NNN**: Short title\n"
            "  - **Goal**: What this implements or fixes\n"
            "  - **Files**: `src/...` full paths\n"
            "  - **Depends on**: none\n"
            "  - **Acceptance**: build passes / visual check\n\n"
            "Output ONLY the new task markdown entries — no preamble, no explanation."
        )

        await self.log("PLAYTEST: Asking Gemma to evaluate and generate new tasks...")
        response = await self.prompt_gemma(
            prompt,
            image_path=screenshot_path if has_screenshot else None,
            temperature=0.5,
            max_tokens=4096,
            num_ctx=16384
        )

        # Extract task entries from the response
        new_tasks = re.findall(r'- \[ \] \*\*TASK-\d+\*\*.*?(?=\n- \[ \]|\Z)', response, re.DOTALL)
        if new_tasks:
            appended = "\n" + "\n".join(t.strip() for t in new_tasks) + "\n"
            self.write_agent_file(TASK_QUEUE_PATH, task_queue.rstrip() + appended)
            await self.log(f"PLAYTEST: Appended {len(new_tasks)} new tasks to queue.")
            self.append_journal(f"PLAYTEST: Generated {len(new_tasks)} new tasks after reviewing build.")
            self.git_commit(f"chore: playtest review — {len(new_tasks)} new tasks added")
        else:
            # Gemma gave no tasks — log it but don't loop; append a generic polish task so BUILD has work
            await self.log("PLAYTEST: No tasks extracted from Gemma's response. Adding generic polish task.")
            fallback_num = f"{next_num:03d}"
            fallback = (
                f"\n- [ ] **TASK-{fallback_num}**: Polish and bug fixes\n"
                f"  - **Goal**: Review all systems for runtime errors and visual polish\n"
                f"  - **Files**: `src/client/core/CoreEngine.ts`\n"
                f"  - **Depends on**: none\n"
                f"  - **Acceptance**: game runs without console errors\n"
            )
            self.write_agent_file(TASK_QUEUE_PATH, task_queue.rstrip() + fallback)
            self.append_journal("PLAYTEST: No tasks from Gemma, added fallback polish task.")

        await self.log("PLAYTEST: Returning to BUILD.")
        self.set_mode("BUILD")

    # ── Tool dispatchers ──────────────────────────────────────────────────────

    def _parse_json_action(self, response: str) -> dict:
        """Extract last valid JSON object from Gemma's response."""
        arr_m = re.search(r'\[\s*\{', response)
        if arr_m:
            start = response.find('[', arr_m.start())
            end   = response.rfind(']') + 1
            try:
                arr = json.loads(response[start:end])
                if isinstance(arr, list) and arr:
                    return self._normalize_action(arr[-1])
            except json.JSONDecodeError:
                pass
        for m in reversed(list(re.finditer(r'\{', response))):
            start = m.start()
            end   = response.rfind("}", start) + 1
            try:
                return self._normalize_action(json.loads(response[start:end]))
            except json.JSONDecodeError:
                continue
        return {}

    # File-write tool names Gemma may use
    _FILE_WRITE_TOOLS = {
        "create_file", "write_file", "write_to_file", "edit_file",
        "update_file", "modify_file", "save_file", "overwrite_file",
        "create_and_write", "write_code", "create_or_update_file",
    }
    # Build/complete tool names Gemma may use
    _BUILD_TOOLS = {
        "run_build", "build", "compile", "tsc", "type_check",
        "complete_task", "task_complete", "finish_task", "done", "verify_build",
    }

    def _normalize_action(self, action: dict) -> dict:
        """Normalize common Gemma hallucination patterns."""
        if "tool" not in action and "action" in action:
            action["tool"] = action["action"]
        tool = action.get("tool", "")

        # Normalize file-write variants → create_file
        if tool in self._FILE_WRITE_TOOLS:
            action["tool"] = "create_file"
        # Normalize bash variants → run_bash
        elif tool in ("bash", "execute_bash", "run_command", "terminal", "execute"):
            action["tool"] = "run_bash"
        # Normalize build variants → run_build
        elif tool in self._BUILD_TOOLS:
            action["tool"] = "run_build"

        # Normalize path field: always ensure 'filename' exists for file tools
        if action.get("tool") in ("create_file", "read_file"):
            if not action.get("filename"):
                action["filename"] = (
                    action.get("path") or action.get("file") or
                    action.get("filepath") or action.get("file_path") or
                    action.get("dest") or action.get("target") or ""
                )

        # Unpack nested args/params/action_input
        nested = action.get("args") or action.get("params") or action.get("parameters") or action.get("action_input") or {}
        if isinstance(nested, dict) and nested:
            t = action.get("tool")
            if t == "run_bash" and not action.get("command"):
                action["command"] = nested.get("command", "")
            if t in ("read_file", "create_file") and not action.get("filename"):
                action["filename"] = nested.get("path", nested.get("filename", ""))
            if t == "create_file" and not action.get("content"):
                action["content"] = (
                    nested.get("content") or nested.get("file_content") or
                    nested.get("text") or nested.get("code") or nested.get("source") or ""
                )
        return action

    async def _dispatch_creative_tool(self, action: dict):
        tool = action.get("tool", "")

        if tool == "create_file":
            fn, content = action.get("filename", ""), action.get("content", "")
            if fn and content:
                full = os.path.join(WORKSPACE_DIR, fn)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                open(full, 'w').write(content)
                await self.log(f"Created: {fn}")
                await self.push_action("create_file", fn)
                await self.set_last_output(f"Created {fn}")

        elif tool == "read_file":
            fn = action.get("filename", "")
            if fn:
                full = os.path.join(WORKSPACE_DIR, fn)
                try:
                    content = open(full).read()[:40000]
                    await self.set_last_output(f"Contents of {fn}:\n{content}")
                    await self.push_action("read_file", fn)
                except Exception as e:
                    await self.set_last_output(f"Error reading {fn}: {e}")

        elif tool == "run_bash":
            cmd = action.get("command", "")
            if cmd:
                _, out, err = await self.execute_native(cmd)
                await self.set_last_output((out + err).strip()[:8000])
                await self.push_action("run_bash", cmd[:80])

        elif tool == "chat_respond":
            msg = action.get("message", action.get("content", action.get("text", "")))
            if msg:
                await self.push_chat(msg)
                await self.push_action("chat_respond", msg[:80])

        elif tool == "generate_image":
            await self._handle_generate_image(action)

        elif tool == "search_web":
            query = action.get("query", "")
            if query:
                try:
                    results = list(self.ddgs.text(query, max_results=3))
                    await self.set_last_output(json.dumps(results)[:4000])
                except Exception as e:
                    await self.set_last_output(f"Search error: {e}")
                await self.push_action("search_web", query[:80])

        elif tool == "update_state":
            for k in ("current_task", "overarching_goal"):
                if k in action:
                    await self.push_state(k, action[k])

        elif tool == "add_reminder":
            note = action.get("note", "")
            if note:
                await self._api("post", "/api/reminders", json={"note": note})

        elif tool == "analyze_image":
            fn = action.get("filename", "")
            q  = action.get("question", "What do you see?")
            full = os.path.join(WORKSPACE_DIR, fn)
            if os.path.exists(full):
                resp = await self.prompt_gemma(q, image_path=full, num_ctx=4096)
                await self.set_last_output(f"Analysis of {fn}:\n{resp}")
            else:
                await self.set_last_output(f"Image not found: {fn}")

    async def _dispatch_build_tool(self, action: dict, task_id: str, task_title: str, task_queue: str, expected_files: list = None) -> bool:
        """Dispatch BUILD tools. Returns True when the current task is complete (build clean)."""
        tool = action.get("tool", "")
        stuck_path  = os.path.join(AGENT_DIR, "stuck_files.json")
        stuck_files = set(json.loads(open(stuck_path).read()) if os.path.exists(stuck_path) else [])

        def _build_clean(tsc_output: str) -> bool:
            """Build is 'clean enough' if all remaining errors are in stuck files."""
            if "error TS" not in tsc_output:
                return True
            err_map = parse_ts_errors(tsc_output)
            return all(f in stuck_files for f in err_map)

        if tool == "create_file":  # all write variants normalized to this by _normalize_action
            fn = action.get("filename") or action.get("path") or action.get("file") or ""
            content = (
                action.get("content") or action.get("file_content") or
                action.get("text") or action.get("code") or ""
            )
            if not fn or not content:
                # Tell Gemma exactly what was missing so she can correct her output
                missing = []
                if not fn:      missing.append("'filename' (or 'path')")
                if not content: missing.append("'content'")
                await self.set_last_output(
                    f"ERROR: create_file requires {' and '.join(missing)}. "
                    f"Got keys: {list(action.keys())}. "
                    f"Output: {{\"tool\": \"create_file\", \"filename\": \"src/...\", \"content\": \"...\"}}"
                )
                await self.log(f"BUILD: create_file missing {missing} — sent feedback to Gemma.")
                return False
            full = os.path.join(WORKSPACE_DIR, fn)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, 'w').write(content)
            await self.log(f"BUILD: Created {fn}")
            await self.push_action("create_file", fn)
            # Verify after every file write, give Gemma the error output as context
            _, out, err = await self.execute_native("npx tsc --noEmit 2>&1", timeout=120)
            combined = (out + err).strip()
            new_errors = combined.count('error TS')
            await self.push_state("last_build_result", f"{new_errors} errors")
            # Track this file as written for the current task
            written_path = os.path.join(AGENT_DIR, "task_written_files.json")
            written = json.loads(open(written_path).read()) if os.path.exists(written_path) else []
            if fn not in written:
                written.append(fn)
                self.write_agent_file(written_path, json.dumps(written))
            if _build_clean(combined):
                # Auto-complete if all expected files are now written
                if expected_files:
                    written_now = json.loads(open(written_path).read()) if os.path.exists(written_path) else []
                    if all(f in written_now for f in expected_files):
                        await self.log(f"BUILD: All expected files written and build clean — auto-completing {task_id}.")
                        return True
                    next_f = next((f for f in expected_files if f not in written_now), None)
                    await self.set_last_output(f"Wrote {fn}. Build clean. Next: {next_f}")
                else:
                    # No explicit file list and build is clean — task is done
                    await self.log(f"BUILD: Build clean after {fn} (no expected_files) — auto-completing {task_id}.")
                    return True
            else:
                await self.set_last_output(f"Created {fn}. Build errors:\n{combined[:2000]}")
            return False

        elif tool == "run_build":  # all build variants normalized by _normalize_action
            _, out, err = await self.execute_native("npx tsc --noEmit 2>&1", timeout=120)
            combined = (out + err).strip()
            count = combined.count("error TS")
            await self.set_last_output(combined[:3000])
            await self.push_state("last_build_result", f"{count} errors")
            await self.push_action("run_build", f"{count} errors")
            if _build_clean(combined):
                return True

        elif tool in ("run_tests", "test"):
            _, out, err = await self.execute_native("CI=true npx vitest run 2>&1", timeout=120)
            await self.set_last_output((out + err).strip()[:3000])
            await self.push_action("run_tests", "vitest run")

        elif tool == "chat_respond":
            msg = action.get("message", action.get("content", ""))
            if msg:
                await self.push_chat(msg)

        else:
            # Unknown tool — give Gemma explicit feedback so she doesn't silently loop
            await self.set_last_output(
                f"ERROR: Unknown tool '{tool}'. Use: create_file (with 'filename' and 'content'), "
                f"run_build, run_tests, chat_respond."
            )
            await self.log(f"BUILD: Unknown tool '{tool}' — gave feedback to Gemma.")

        return False

    # ── ComfyUI image generation ──────────────────────────────────────────────

    async def _handle_generate_image(self, action: dict):
        prompt_text = action.get("prompt", "")
        filename    = action.get("filename", f"output_{self.iteration}.png")
        model       = action.get("model", "illustriousXL_v01.safetensors")
        width       = int(action.get("width", 1024))
        height      = int(action.get("height", 512))
        steps       = int(action.get("steps", 28))
        cfg         = float(action.get("cfg", 7.5))
        negative    = action.get("negative", "photorealistic, 3d render, blurry, text, watermark")

        ckpt_dir = "/Users/max/ComfyUI/models/checkpoints/"
        if not os.path.exists(os.path.join(ckpt_dir, model)):
            model = ("illustriousXL_v01.safetensors"
                     if os.path.exists(os.path.join(ckpt_dir, "illustriousXL_v01.safetensors"))
                     else "sd_xl_base_1.0.safetensors")

        out_dir  = os.path.join(WORKSPACE_DIR, "lore", "visuals", "generated")
        os.makedirs(out_dir, exist_ok=True)
        out_path  = os.path.join(out_dir, filename)
        comfy_url = "http://127.0.0.1:8188"

        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt_text}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": negative}},
            "4": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": width, "height": height, "batch_size": 1}},
            "5": {"class_type": "KSampler",
                  "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                             "latent_image": ["4", 0], "seed": self.iteration,
                             "steps": steps, "cfg": cfg,
                             "sampler_name": "euler", "scheduler": "karras", "denoise": 1.0}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage",
                  "inputs": {"images": ["6", 0], "filename_prefix": os.path.splitext(filename)[0]}},
        }

        import uuid as _uuid
        client_id = str(_uuid.uuid4())
        await self.log(f"Generating image: {filename} (model={model})")
        try:
            async with aiohttp.ClientSession() as sess:
                r = await sess.post(f"{comfy_url}/prompt",
                                    json={"prompt": workflow, "client_id": client_id},
                                    timeout=aiohttp.ClientTimeout(total=10))
                prompt_id = (await r.json()).get("prompt_id")

            for _ in range(300):
                await asyncio.sleep(1)
                async with aiohttp.ClientSession() as sess:
                    hist_r = await sess.get(f"{comfy_url}/history/{prompt_id}",
                                            timeout=aiohttp.ClientTimeout(total=5))
                    hist = await hist_r.json()
                if prompt_id in hist:
                    for node_out in hist[prompt_id].get("outputs", {}).values():
                        for img in node_out.get("images", []):
                            async with aiohttp.ClientSession() as sess:
                                ir = await sess.get(
                                    f"{comfy_url}/view",
                                    params={"filename": img["filename"],
                                            "subfolder": img.get("subfolder", ""),
                                            "type": "output"},
                                    timeout=aiohttp.ClientTimeout(total=15))
                                open(out_path, "wb").write(await ir.read())
                            await self.push_screenshot(out_path)
                            await self.log(f"Image saved: {filename}")
                            await self.set_last_output(f"[IMAGE GENERATED]: lore/visuals/generated/{filename}")
                            return
                    break
            await self.log("ComfyUI timed out.")
        except Exception as e:
            await self.log(f"ComfyUI error: {e}")
            await self.set_last_output(f"[GENERATE ERROR]: {e}")

    # ── Studio control (pause / resume / switch) ────────────────────────────

    async def _check_studio_commands(self):
        """Poll pending_actions for pause/resume/switch commands from the dashboard."""
        result = await self._api("get", "/api/studio/pending")
        for action in result.get("actions", []):
            atype   = action.get("type", "")
            payload = action.get("payload", {})
            if atype == "pause":
                await self._enter_pause()
                return  # _enter_pause owns its own command loop
            elif atype == "switch_game":
                slug = payload.get("slug", "")
                if slug and slug != ACTIVE_GAME:
                    await self._switch_game(slug)
                    return  # os.execv — never reached

    async def _enter_pause(self):
        """Enter a slow-poll wait loop until resume or switch_game arrives."""
        await self.log(f"[{ACTIVE_GAME}] PAUSED — waiting for resume or switch command.")
        await self.push_state("mode", "PAUSED")
        await self.push_state("supervisor_status", "paused")
        self.git_commit("chore: pausing — checkpoint")
        while not self._shutdown:
            await asyncio.sleep(10)
            result = await self._api("get", "/api/studio/pending")
            for action in result.get("actions", []):
                atype   = action.get("type", "")
                payload = action.get("payload", {})
                if atype == "resume":
                    await self.log(f"[{ACTIVE_GAME}] RESUMED.")
                    await self.push_state("supervisor_status", "running")
                    return
                elif atype == "switch_game":
                    slug = payload.get("slug", "")
                    if slug:
                        await self._switch_game(slug)
                        return  # os.execv — never reached

    async def _switch_game(self, slug: str):
        """Commit current work, update active game in studio_config, then restart."""
        await self.log(f"Switching active game: {ACTIVE_GAME} → {slug}")
        self.git_commit(f"chore: checkpoint before switching to {slug}")
        try:
            config = json.loads(open(_studio_config_path).read())
        except Exception:
            config = {}
        config["active_game"] = slug
        with open(_studio_config_path, "w") as f:
            f.write(json.dumps(config, indent=2))
        await self.log(f"studio_config.json updated. Restarting supervisor for '{slug}'...")
        await asyncio.sleep(1)  # allow log to flush
        # Remove PID lock so the restarted process can acquire it
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run_loop(self):
        self.setup_signals()
        await self.initialize_workspace()
        await self.push_state("supervisor_status", "running")

        state = await self.fetch_state()
        if self.iteration == 0:
            self.iteration = int(state.get("iteration_count", 0))

        await self.log(f"Supervisor v2 started. Iteration {self.iteration}.")

        while not self._shutdown:
            try:
                state    = await self.fetch_state()
                manifest = self.read_manifest()
                mode     = manifest.get("mode", "BOOTSTRAP")

                await self.push_state("mode", mode)
                await self.push_state("iteration_count", str(self.iteration))
                logger.info(f"--- Iteration {self.iteration} | Mode: {mode} ---")
                await self._api("post", "/api/logs",
                                json={"log": f"--- Iteration {self.iteration} | Mode: {mode} ---"})

                if self.iteration % 5 == 0:
                    await self.sync_intel()
                self._flush_ollama_if_needed()

                if   mode == "BOOTSTRAP":     await self.run_bootstrap()
                elif mode == "CREATIVE":      await self.run_creative_iteration(state)
                elif mode == "ART_DIRECTION": await self.run_art_direction()
                elif mode == "ARCHITECT":     await self.run_architect()
                elif mode == "BUILD":         await self.run_build_iteration(state)
                elif mode == "REPAIR":        await self.run_repair_iteration(state)
                elif mode == "PLAYTEST":      await self.run_playtest_iteration(state)
                else:
                    await self.log(f"Unknown mode '{mode}'. Defaulting to BUILD.")
                    self.set_mode("BUILD")

                if not self._shutdown:
                    await self._check_studio_commands()

                self.iteration += 1
                if not self._shutdown:
                    await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Iteration {self.iteration} error: {e}", exc_info=True)
                await self.log(f"Error: {e}")
                await asyncio.sleep(5)

        logger.info("Supervisor shutting down.")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)


# =============================================================================
# Entry point
# =============================================================================

def _acquire_pid_lock():
    if os.path.exists(PID_FILE):
        try:
            existing = int(open(PID_FILE).read().strip())
            os.kill(existing, 0)
            print(f"[ABORT] Supervisor already running (PID {existing}). Exiting.")
            sys.exit(1)
        except (OSError, ValueError):
            pass
    open(PID_FILE, 'w').write(str(os.getpid()))


def _release_pid_lock():
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    _acquire_pid_lock()
    atexit.register(_release_pid_lock)
    supervisor = GemmaSupervisor()
    try:
        asyncio.run(supervisor.run_loop())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt.")
    finally:
        _release_pid_lock()
