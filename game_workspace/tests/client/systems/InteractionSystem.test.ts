import { describe, it, expect, beforeEach, vi } from 'vitest';
import { InteractionSystem } from '../../../src/client/core/systems/InteractionSystem';
import { GameState, GameMode } from '../../../src/client/state/GameState';
import { WorldSystem, NPC } from '../../../src/client/core/systems/WorldSystem';

vi.mock('../../../src/client/core/systems/WorldSystem');

describe('InteractionSystem', () => {
  let interactionSystem: InteractionSystem;
  let gameState: GameState;
  let mockWorldSystem: any;

  beforeEach(() => {
    gameState = new GameState();
    mockWorldSystem = new WorldSystem(gameState, 'test-seed');
    interactionSystem = new InteractionSystem(gameState, mockWorldSystem);
    interactionSystem.init();
  });

  it('should not trigger interaction if no NPC is nearby', () => {
    // Player at 0,0; NPC at 500,500 (too far)
    mockWorldSystem.getPlayerChunk = vi.fn().mockReturnValue({ x: 0, y: 0 });
    mockWorldSystem.getChunkAt = vi.fn().mockReturnValue({
      objects: [],
      npcs: [{
        id: 'npc-1',
        type: 'npc',
        name: 'Test NPC',
        x: 500,
        y: 500,
        dialogueSeed: 123,
        metadata: {}
      }]
    });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'e' }));
    
    expect(gameState.getMode()).toBe(GameMode.EXPLORATION);
    expect(gameState.getState().interactionTarget).toBeNull();
  });

  it('should transition to DIALOGUE mode when interacting with a nearby NPC', () => {
    // Player at 0,0; NPC at 10,10 (very close)
    gameState.updatePlayerPosition(0, 0, 0);
    mockWorldSystem.getPlayerChunk = vi.fn().mockReturnValue({ x: 0, y: 0 });
    mockWorldSystem.getChunkAt = vi.fn().mockReturnValue({
      objects: [],
      npcs: [{
        id: 'npc-1',
        type: 'npc',
        name: 'Test NPC',
        x: 10,
        y: 10,
        dialogueSeed: 123,
        metadata: {}
      }]
    });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'e' }));
    
    expect(gameState.getMode()).toBe(GameMode.DIALOGUE);
    expect(gameState.getState().interactionTarget?.id).toBe('npc-1');
  });

  it('should select the closest NPC if multiple are nearby', () => {
    gameState.updatePlayerPosition(0, 0, 0);
    mockWorldSystem.getPlayerChunk = vi.fn().mockReturnValue({ x: 0, y: 0 });
    mockWorldSystem.getChunkAt = vi.fn().mockReturnValue({
      objects: [],
      npcs: [
        {
          id: 'npc-far',
          type: 'npc',
          name: 'Far NPC',
          x: 40,
          y: 40,
          dialogueSeed: 1,
          metadata: {}
        },
        {
          id: 'npc-close',
          type: 'npc',
          name: 'Close NPC',
          x: 10,
          y: 10,
          dialogueSeed: 2,
          metadata: {}
        }
      ]
    });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'e' }));
    
    expect(gameState.getState().interactionTarget?.id).toBe('npc-close');
  });
});
