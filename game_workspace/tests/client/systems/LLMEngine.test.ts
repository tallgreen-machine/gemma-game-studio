import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LLMEngine, NPCPersona } from '../../../src/client/core/systems/LLMEngine';

// Mock WebLLM to match the implementation's use of CreateMLCEngine
vi.mock('webllm', () => ({
  CreateMLCEngine: vi.fn().mockResolvedValue({
    chat: {
      completions: {
        create: vi.fn().mockResolvedValue({
          choices: [{
            message: { content: 'Mocked LLM Response' }
          }]
        }),
      },
    },
  }),
}));

describe('LLMEngine', () => {
  let engine: LLMEngine;
  const mockPersona: NPCPersona = {
    name: 'Eldrin',
    role: 'Ancient Librarian',
    traits: ['mysterious', 'knowledgeable'],
    backstory: 'Guardian of the Aetheria archives for 500 years.',
    currentGoal: 'Guide the player to the Forbidden Wing.',
  };

  beforeEach(() => {
    engine = new LLMEngine();
    vi.clearAllMocks();
  });

  it('should initialize the model and track progress', async () => {
    let progress = 0;
    engine.onLoadingProgress((p) => {
      progress = p;
    });

    await engine.initialize();
    expect(progress).toBe(100);
  });

  it('should generate a response based on a persona', async () => {
    await engine.initialize();
    const response = await engine.generateResponse(mockPersona, 'Who are you?');
    
    expect(response).toBeDefined();
    expect(typeof response).toBe('string');
    expect(response).toBe('Mocked LLM Response');
  });

  it('should maintain conversation history for multi-turn dialogue', async () => {
    await engine.initialize();
    await engine.generateResponse(mockPersona, 'Hello!');
    const response2 = await engine.generateResponse(mockPersona, 'What did I just say?');
    
    expect(response2).toBeDefined();
  });

  it('should clear history when resetConversation is called', async () => {
    await engine.initialize();
    await engine.generateResponse(mockPersona, 'First message');
    engine.resetConversation();
    
    const response = await engine.generateResponse(mockPersona, 'Second message');
    expect(response).toBeDefined();
  });

  it('should return a fallback response if not initialized', async () => {
    const response = await engine.generateResponse(mockPersona, 'Hello?');
    expect(response).toBe('...The NPC stares at you blankly...');
  });
});
