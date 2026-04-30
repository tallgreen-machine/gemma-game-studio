# Specification: Dialogue System

## 1. Purpose
The `DialogueSystem` manages the communication between the player and NPCs, utilizing a client-side LLM to generate dynamic, context-aware responses without relying on a backend server.

## 2. Responsibilities
- **LLM Lifecycle Management**: Initialize and load the quantized micro-LLM (e.g., Gemma 2B) into the browser's WebGPU memory.
- **Prompt Engineering**: Construct systemic prompts that include the NPC's identity, the current world state, and the player's history to ensure consistent characterization.
- **Dialogue UI Orchestration**: Interface with the rendering layer to display a cinematic dialogue overlay (text boxes, typing effects).
- **State Synchronization**: Manage the flow of the conversation and trigger `GameState` transitions when a dialogue ends.
- **Asynchronous Handling**: Manage the loading and generation states to prevent the main game loop from freezing during inference.

## 3. Technical Details
- **Inference Engine**: Utilize `@mlc-ai/web-llm` for WebGPU-accelerated inference.
- **Prompt Template**: 
  `"You are [NPC_NAME], a [NPC_TYPE] in the world of Aetheria. Your personality is [TRAIT]. Current context: [WORLD_STATE]. The player says: [PLAYER_INPUT]. Respond in a concise, cinematic style similar to retro adventure games."`
- **UI Implementation**: A dedicated PixiJS Container for the dialogue box, appearing as an overlay on top of the `viewport` and `letterbox`.
- **Memory Management**: Implement a mechanism to unload the LLM or clear cache if the browser's memory limits are reached.

## 4. Cinematic Alignment
- Use a 'typewriter' effect for text delivery to evoke the feel of 90s cinematic adventures.
- Ensure the dialogue box maintains the cinematic aspect ratio and doesn't obscure critical visual elements.
