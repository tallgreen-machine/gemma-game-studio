# Specification: Core Engine

## 1. Purpose
The `CoreEngine` is the central orchestrator of the client-side application. Its primary responsibility is to initialize the rendering context, maintain the game loop, and provide a centralized point for system-level updates.

## 2. Requirements
- **PixiJS Integration**: Must initialize a `PIXI.Application` with a canvas that fills the browser window.
- **Game Loop**: Must implement a high-precision ticker (using `PIXI.Ticker`) to execute `update` and `render` cycles.
- **Lifecycle Management**: 
  - `init()`: Asynchronous setup of rendering and initial system modules.
  - `start()`: Begins the game loop.
  - `stop()`: Gracefully shuts down the loop and cleans up resources.
- **Input Handling**: Provide a mechanism to register and dispatch input events (Keyboard/Mouse) to the active game state.
- **State Coordination**: Must hold a reference to the current `GameState` and call its update method every frame.
- **Resolution Management**: Automatically handle window resizing to maintain the aspect ratio and cinematic framing.

## 3. Technical Interface

### `CoreEngine` Class
- `public async init(): Promise<void>`: Sets up the PixiJS app and loads base configurations.
- `public start(): void`: Starts the `PIXI.Ticker`.
- `public stop(): void`: Stops the `PIXI.Ticker`.
- `private update(delta: number): void`: The internal loop that calculates timing and invokes `GameState.update()`.

## 4. TDD Success Criteria
- A Vitest test must verify that the `CoreEngine` can be instantiated.
- A test must verify that `init()` correctly creates the PixiJS application instance.
- A test must verify that the update loop is triggered upon calling `start()`.
