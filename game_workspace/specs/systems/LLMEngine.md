# Specification: LLM Engine

## 1. Purpose
The `LLMEngine` is the low-level interface between the game's `DialogueSystem` and the client-side LLM runtime (`web-llm`). Its primary goal is to provide an asynchronous interface for generating text based on prompts while managing the lifecycle of the model in the browser's WebGPU memory.

## 2. Technical Requirements
- **Runtime**: `@mlc-ai/web-llm`
- **Model**: Quantized Gemma 2B (or compatible small-footprint model)
- **Execution**: Client-side WebGPU
- **Scalability**: Zero server-side calls for dialogue generation.

## 3. Interface Definition

### `LLMEngine` Class

#### Methods
- `async initialize(): Promise<void>`
  - Loads the model into the browser's GPU memory.
  - Handles initialization progress updates.
  - Throws an error if WebGPU is not supported or the model fails to load.

- `async generateResponse(prompt: string, options?: GenerationOptions): Promise<string>`
  - Sends a prompt to the local LLM.
  - Returns the generated text response.
  - Implements a timeout to prevent game freezes.

- `getStatus(): LLMStatus`
  - Returns the current state: `IDLE`, `LOADING`, `READY`, or `ERROR`.

- `getProgress(): number`
  - Returns the loading progress as a percentage (0-100).

#### Types
- `GenerationOptions`: Configuration for temperature, max tokens, etc.
- `LLMStatus`: Enum of `IDLE | LOADING | READY | ERROR`

## 4. Constraints
- **Memory Management**: The engine must allow for unloading the model if the user exits the game or enters a low-memory state.
- **Async Nature**: All LLM interactions must be non-blocking to the main game loop.
- **Error Handling**: If the local LLM fails, the engine should provide a fallback mechanism (e.g., returning a generic '...' response) to prevent the game from crashing.
