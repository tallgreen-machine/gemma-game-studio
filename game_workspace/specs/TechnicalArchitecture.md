# Technical Architecture: Aetheria

## 1. High-Level Stack
- **Frontend**: TypeScript, Vite, PixiJS (Rendering), WebGPU (LLM Inference).
- **Backend**: Node.js, Fastify or Express, Socket.io/ws (State Sync).
- **Client-Side AI**: `WebLLM` or `Transformers.js` utilizing the browser's WebGPU API to run a quantized Gemma-2B model.
- **Testing**: Vitest for unit/logic tests, Playwright for E2E and visual regression.

## 2. Frontend Architecture
- **Rendering Engine**: PixiJS will be used for its high-performance 2D/2.5D capabilities. We will implement a custom shader pipeline to achieve the 'Another World' retro-polygon aesthetic (flat shading, limited palette).
- **Game Loop**: A centralized `Engine` class managing the update/draw cycles, input handling, and state synchronization.
- **Asset Pipeline**: Use Vite's asset handling for sprites, shaders, and audio. Assets will be optimized for fast loading.

## 3. Backend Architecture (State Sync)
- **Authoritative State**: The server maintains the 'Source of Truth' for player positions, inventory, and global world state.
- **WebSocket Layer**: Real-time bi-directional communication for player movements and environmental changes.
- **Persistence**: A simple database (e.g., MongoDB or PostgreSQL) to store user accounts and persistent world changes.
- **Scalability**: Use a lightweight state-diffing algorithm to send only changed data to clients to minimize bandwidth.

## 4. Local LLM Implementation
- **Inference**: The client will download a quantized model weight file (ONNX/WebGPU format) on first boot and cache it in IndexedDB.
- **Context Management**: A `DialogueManager` will maintain a local window of conversation history and inject 'World Knowledge' (retrieved from the server or local data) into the system prompt.
- **Async Pipeline**: LLM inference will run in a Web Worker to prevent blocking the main rendering thread, ensuring the game stays at 60fps during dialogue generation.

## 5. Data Flow
1. **Player Input** $\rightarrow$ **Client State** $\rightarrow$ **Server Validation** $\rightarrow$ **Broadcast to other Clients**.
2. **Dialogue Input** $\rightarrow$ **Local LLM Inference** $\rightarrow$ **Update Local UI** $\rightarrow$ **(Optional) Sync outcome to Server**.

## 6. Directory Structure
- `/specs`: Design and Technical documents.
- `/src/client`: All frontend code (PixiJS, AI logic, UI).
- `/src/server`: All backend code (WebSocket server, DB logic).
- `/assets`: Textures, Shaders, Audio.
- `/tests`: Unit and E2E tests.
