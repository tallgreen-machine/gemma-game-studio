# Specification: Interaction System

## 1. Purpose
The `InteractionSystem` enables the player to interact with NPCs and other interactive world objects, triggering events such as dialogue or item acquisition.

## 2. Responsibilities
- **Proximity Detection**: Continuously check if the player is within a specific interaction radius of an NPC or interactive object.
- **Input Handling**: Listen for the interaction key (e.g., 'E') when a valid target is in proximity.
- **State Management**: Transition the `GameState` from `EXPLORATION` to `DIALOGUE` mode when an interaction begins.
- **Target Identification**: Identify exactly which NPC or object is being targeted (handling cases where multiple targets are close).
- **Interface for Dialogue**: Pass the target's metadata (e.g., `dialogueSeed`) to the Dialogue Manager/LLM system.

## 3. Technical Details
- **Radius Check**: Calculate the Euclidean distance between the player and all NPCs in the current and neighboring chunks provided by the `WorldSystem`.
- **Interaction Window**: Use a small distance threshold (e.g., 50-100 pixels) for valid interactions.
- **GameState Integration**: Update a `currentInteractionTarget` field in the `GameState` to track who the player is talking to.
- **Non-Blocking Logic**: Ensure proximity checks are efficient and do not lag the main loop.

## 4. Dialogue Flow
1. Player approaches NPC -> `InteractionSystem` marks NPC as 'Interactable'.
2. Player presses 'E' -> `InteractionSystem` triggers `startDialogue(npcId)`.
3. `GameState` enters `DIALOGUE` mode -> `InputSystem` disables movement.
4. `DialogueSystem` (to be implemented) invokes the Local LLM for a greeting.
