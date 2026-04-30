import * as webllm from 'webllm';

export interface NPCPersona {
  name: string;
  role: string;
  traits: string[];
  backstory: string;
  currentGoal: string;
}

export class LLMEngine {
  private engine: webllm.MLCEngine | null = null;
  private isInitialized: boolean = false;
  private conversationHistory: { role: 'user' | 'assistant', content: string }[] = [];
  private progressCallback: ((progress: number) => void) | null = null;

  /**
   * Initializes the WebLLM engine and loads the model into WebGPU memory.
   */
  async initialize(): Promise<void> {
    try {
      const engine = await webllm.CreateMLCEngine({
        initProgressCallback: (report) => {
          if (this.progressCallback && report.progress !== undefined) {
            this.progressCallback(report.progress * 100);
          }
        },
      });

      this.engine = engine;
      this.isInitialized = true;
      
      // Manually trigger 100% progress if callback exists
      if (this.progressCallback) {
        this.progressCallback(100);
      }
    } catch (error) {
      console.error('Failed to initialize LLMEngine:', error);
      this.isInitialized = false;
      throw error;
    }
  }

  /**
   * Generates a response based on the NPC persona and the conversation history.
   */
  async generateResponse(npcPersona: NPCPersona, userMessage: string): Promise<string> {
    if (!this.isInitialized || !this.engine) {
      return '...The NPC stares at you blankly...';
    }

    const systemPrompt = this.constructSystemPrompt(npcPersona);
    const messages = [
      { role: 'system', content: systemPrompt },
      ...this.conversationHistory,
      { role: 'user', content: userMessage },
    ] as any[];

    try {
      const response = await this.engine.chat.completions.create({
        messages,
        temperature: 0.7,
      });

      const content = response.choices[0].message.content || '...';
      
      // Update history
      this.conversationHistory.push({ role: 'user', content: userMessage });
      this.conversationHistory.push({ role: 'assistant', content: content });

      return content;
    } catch (error) {
      console.error('LLM Generation Error:', error);
      return '...The NPC seems confused and cannot speak...';
    }
  }

  /**
   * Constructs a detailed system prompt to steer the LLM's personality.
   */
  private constructSystemPrompt(persona: NPCPersona): string {
    return `You are ${persona.name}, a ${persona.role}. 
` +
           `Your traits are: ${persona.traits.join(', ')}. 
` +
           `Backstory: ${persona.backstory} 
` +
           `Your current goal is: ${persona.currentGoal}. 
` +
           `Respond in a manner consistent with this persona. Keep responses concise and cinematic.`;
  }

  /**
   * Registers a callback to track model loading progress.
   */
  onLoadingProgress(callback: (progress: number) => void): void {
    this.progressCallback = callback;
  }

  /**
   * Clears the conversation history.
   */
  resetConversation(): void {
    this.conversationHistory = [];
  }
}
