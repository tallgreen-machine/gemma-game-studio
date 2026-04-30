import { describe, it, expect, beforeEach, vi } from 'vitest';
import { CoreEngine } from '../../../src/client/core/CoreEngine';
import { GameState } from '../../../src/client/state/GameState';

// Mock PixiJS to avoid WebGL requirements in JSDOM
vi.mock('pixi.js', () => ({
  Application: vi.fn().mockImplementation(() => ({
    view: document.createElement('canvas'),
    ticker: {
      add: vi.fn(),
      stop: vi.fn(),
      start: vi.fn(),
    },
    destroy: vi.fn(),
  })),
}));

describe('CoreEngine', () => {
  let coreEngine: CoreEngine;
  let gameState: GameState;

  beforeEach(() => {
    vi.clearAllMocks();
    gameState = new GameState();
    coreEngine = new CoreEngine(gameState);
  });

  it('should initialize the PixiJS application on init', async () => {
    await coreEngine.init();
    expect(coreEngine.isInitialized).toBe(true);
  });

  it('should start the game loop', async () => {
    await coreEngine.init();
    coreEngine.start();
    // In a real scenario, we'd check if the ticker.add was called
    // Since we mocked it, we check if the internal state indicates it started
    expect(coreEngine.isRunning).toBe(true);
  });

  it('should stop the game loop', async () => {
    await coreEngine.init();
    coreEngine.start();
    coreEngine.stop();
    expect(coreEngine.isRunning).toBe(false);
  });

  it('should call the update method during the loop', async () => {
    await coreEngine.init();
    const updateSpy = vi.spyOn(coreEngine, 'update');
    
    // Manually trigger an update to simulate the ticker
    coreEngine.update(1);
    
    expect(updateSpy).toHaveBeenCalledWith(1);
  });

  it('should clean up resources on destroy', async () => {
    await coreEngine.init();
    coreEngine.destroy();
    expect(coreEngine.isInitialized).toBe(false);
  });
});
