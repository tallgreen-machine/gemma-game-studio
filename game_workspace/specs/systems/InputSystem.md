# Specification: Input System

## 1. Purpose
The `InputSystem` captures user input from the keyboard and mouse and translates it into state changes within the `GameState`.

## 2. Responsibilities
- Listen for keyboard events (WASD/Arrows for movement).
- Update the player's position in `GameState` based on input.
- Handle interaction keys (e.g., 'E' to interact with NPCs).

## 3. Technical Details
- Implements `GameSystem` interface.
- Uses a map to track currently pressed keys.
- Calculates movement vectors based on a configurable speed constant.
