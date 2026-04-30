import { describe, it, expect, beforeEach, vi } from 'vitest';
import { DialogueSystem } from '../../../src/client/core/systems/DialogueSystem';
import { GameState, GameMode } from '../../../src/client/state/GameState';
import { WorldSystem, NPC } from '../../../src/client/core/systems/WorldSystem';

// Mock @mlc-ai/web-llm
vi.mock('@mlc-ai/web-llm', () => {
  return {
    CreateMLCEngine: vi.fn().mockReturnValue({
      chat: vi.fn().mockResolvedValue('Hello from the local LLM!'),
      unload: vi.fn().mockResolvedValue(undefined),
      reload: vi.fn().mockResolvedValue(undefined),
    }),
  };
});

describe('DialogueSystem', () => {
  let dialogueSystem: DialogueSystem;
  let gameState: GameState;
  let mockWorldSystem: any;
  let mockApp: any;

  beforeEach(() => {
    vi.clearAllMocks();
    gameState = new GameState();
    mockWorldSystem = { 
      getPlayerChunk: vi.fn(),
      getChunkAt: vi.fn()
    };
    mockApp = { 
      stage: { addChild: vi.fn(), removeChild: vi.fn() },
      screen: { width: 800, height: 600 }
    };
    dialogueSystem = new DialogueSystem(gameState, mockApp, mockWorldSystem);
  });

  it('should initialize the LLM engine', async () => {
    await dialogueSystem.init();
    expect(dialogueSystem.isReady).toBe(true);
  });

  it('should generate a prompt based on NPC metadata', async () => {
    await dialogueSystem.init();
    const mockNpc: NPC = {
      id: 'npc-1',
      type: 'npc',
      name: 'Kaelen',
      x: 10, y: 10, z: 0,
      dialogueSeed: 123,
      metadata: { trait: 'grumpy' }
    };

    gameState.setInteractionTarget(mockNpc);
    gameState.setGameMode(GameMode.DIALOGUE);

    const response = await dialogueSystem.generateResponse('Hello there!');
    expect(response).toContain('Hello from the local LLM!');
  });

  it('should transition game mode back to EXPLORATION when dialogue ends', async () => {
    await dialogueSystem.init();
    gameState.setGameMode(GameMode.DIALOGUE);
    
    dialogueSystem.endDialogue();
    
    expect(gameState.getMode()).toBe(GameMode.EXPLORATION);
    expect(gameState.getState().interactionTarget).toBeNull();
  });

  it('should handle LLM loading errors gracefully', async () => {
    const { CreateMLCEngine } = await import('@mlc-ai/web-llm');
    (CreateMLCEngine as any).mockRejectedValueOnce(new Error('WebGPU not supported'));
    
    await expect(dialogueSystem.init()).rejects.toThrow('WebGPU not supported');
    expect(dialogueSystem.isReady).toBe(false);
  });
});
