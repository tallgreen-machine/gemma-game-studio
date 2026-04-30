import { describe, it, expect, beforeEach } from 'vitest';
import { WorldSystem } from '../../../src/client/core/systems/WorldSystem';
import { GameState } from '../../../src/client/state/GameState';

describe('WorldSystem', () => {
  let worldSystem: WorldSystem;
  let gameState: GameState;
  const SEED = 'aetheria-seed-123';

  beforeEach(() => {
    gameState = new GameState();
    worldSystem = new WorldSystem(gameState, SEED);
  });

  it('should be deterministic based on seed', () => {
    const world1 = new WorldSystem(new GameState(), SEED);
    const world2 = new WorldSystem(new GameState(), SEED);
    
    const chunk1 = world1.getChunkAt(0, 0);
    const chunk2 = world2.getChunkAt(0, 0);
    
    expect(chunk1).toEqual(chunk2);
  });

  it('should produce different results for different seeds', () => {
    const world1 = new WorldSystem(new GameState(), 'seed-a');
    const world2 = new WorldSystem(new GameState(), 'seed-b');
    
    const chunk1 = world1.getChunkAt(0, 0);
    const chunk2 = world2.getChunkAt(0, 0);
    
    expect(chunk1).not.toEqual(chunk2);
  });

  it('should correctly identify the player current chunk', () => {
    // Set player to a specific coordinate
    gameState.updatePlayerPosition(1500, 2500, 0);
    
    const currentChunk = worldSystem.getPlayerChunk();
    // Assuming chunk size is 1000, (1500, 2500) should be chunk (1, 2)
    expect(currentChunk.x).toBe(1);
    expect(currentChunk.y).toBe(2);
  });

  it('should generate chunks on demand (lazy loading)', () => {
    const chunkCoord = '5,5';
    expect(worldSystem.isChunkLoaded(5, 5)).toBe(false);
    
    worldSystem.getChunkAt(5, 5);
    expect(worldSystem.isChunkLoaded(5, 5)).toBe(true);
  });
});
