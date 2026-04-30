# Specification: LLMEngine

## Overview
The `LLMEngine` is a client-side service responsible for managing the lifecycle and execution of a quantized micro-LLM (e.g., Gemma 2B) using WebGPU. It ensures that all NPC dialogue is generated locally on the player's machine to eliminate server costs and latency.

## Requirements
1. **Local Execution**: Must use `WebLLM` or `Transformers.js` for browser-based inference.
2. **Asynchronous Loading**: Model loading must be asynchronous and provide progress updates (0-100%).
3. **Persona-Driven**: The engine must accept a 'persona' object (name, traits, goals, knowledge) to steer the LLM's output.
4. **Context Management**: Must maintain a short-term conversation history to allow for coherent multi-turn dialogue.
5. **Fallback Mechanism**: Must provide a fallback response if the LLM fails to initialize or crashes.

## Interface

### `LLMEngine` Class
- `async initialize(): Promise<void>`: Loads the model into WebGPU memory.
- `async generateResponse(npcPersona: NPCPersona, userMessage: string): Promise<string>`: Generates a response based on the persona and the current conversation state.
- `onLoadingProgress(callback: (progress: number) => void)`: Registers a callback for loading updates.
- `resetConversation()`: Clears the current dialogue context.

### `NPCPersona` Interface
- `name: string`
- `role: string`
- `traits: string[]`
- `backstory: string`
- `currentGoal: string`
