import { Application } from 'pixi.js';
import { GameState } from './state/GameState';
import { CoreEngine } from './core/CoreEngine';
import { InputSystem } from './core/systems/InputSystem';
import { RenderSystem } from './core/systems/RenderSystem';
import { WorldSystem } from './core/systems/WorldSystem';
import { InteractionSystem } from './core/systems/InteractionSystem';
import { DialogueSystem } from './core/systems/DialogueSystem';

async function bootstrap() {
    console.log('Initializing Aetheria...');

    // 1. Initialize State
    const gameState = new GameState();

    // 2. Initialize Engine
    const engine = new CoreEngine(gameState);
    await engine.init();

    // 3. Setup PixiJS Application
    const app = engine.getApp();
    if (!app) {
        throw new Error('Failed to initialize PixiJS Application');
    }

    // Configure the application view
    app.view.width = window.innerWidth;
    app.view.height = window.innerHeight;
    document.body.appendChild(app.view);

    // 4. Initialize World System
    const worldSeed = 'aetheria-prime-seed';
    const worldSystem = new WorldSystem(gameState, worldSeed);

    // 5. Initialize and Add Systems
    const inputSystem = new InputSystem(gameState);
    const renderSystem = new RenderSystem(gameState, app, worldSystem);
    const interactionSystem = new InteractionSystem(gameState, worldSystem);
    const dialogueSystem = new DialogueSystem(gameState, app, worldSystem);

    // Initialize DialogueSystem (async LLM loading)
    try {
        await dialogueSystem.init();
    } catch (err) {
        console.warn('DialogueSystem failed to init (WebGPU?), continuing without LLM:', err);
    }

    engine.addSystem(worldSystem);
    engine.addSystem(inputSystem);
    engine.addSystem(renderSystem);
    engine.addSystem(interactionSystem);
    engine.addSystem(dialogueSystem);

    // 6. Start the Engine
    engine.start();

    console.log('Aetheria Engine Started Successfully with Local LLM NPCs.');
    
    // Handle window resizing
    window.addEventListener('resize', () => {
        app.renderer.resize(window.innerWidth, window.innerHeight);
    });
}

bootstrap().catch((err) => {
    console.error('Critical Engine Failure:', err);
});
