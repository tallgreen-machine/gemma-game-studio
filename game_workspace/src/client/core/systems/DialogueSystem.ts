import { GameSystem } from '../GameSystem';
import { GameState, GameMode } from '../../state/GameState';
import { Application, Container, Graphics, Text } from 'pixi.js';
import { WorldSystem, NPC } from './WorldSystem';
import * as webllm from '@mlc-ai/web-llm';

export class DialogueSystem implements GameSystem {
  public name = 'DialogueSystem';
  private gameState: GameState;
  private app: Application;
  private worldSystem: WorldSystem;
  private engine: webllm.MLCEngine | null = null;
  public isReady = false;

  // UI Elements
  private dialogueContainer: Container;
  private dialogueBox: Graphics;
  private dialogueText: Text;
  private isTyping = false;

  constructor(gameState: GameState, app: Application, worldSystem: WorldSystem) {
    this.gameState = gameState;
    this.app = app;
    this.worldSystem = worldSystem;
    
    this.dialogueContainer = new Container();
    this.dialogueBox = new Graphics();
    this.dialogueText = new Text('', { 
      fontSize: 18, 
      fill: 0xffffff, 
      wordWrap: true, 
      wordWrapWidth: 600 
    });
    
    this.setupUI();
  }

  private setupUI(): void {
    this.dialogueBox.beginFill(0x000000, 0.8);
    this.dialogueBox.drawRect(0, 0, 700, 150);
    this.dialogueBox.endFill();

    this.dialogueText.x = 20;
    this.dialogueText.y = 20;
    
    this.dialogueContainer.addChild(this.dialogueBox);
    this.dialogueContainer.addChild(this.dialogueText);
    
    // Center the dialogue box at the bottom
    this.dialogueContainer.x = (this.app.screen.width - 700) / 2;
    this.dialogueContainer.y = this.app.screen.height - 200;
    this.dialogueContainer.visible = false;
  }

  async init(): Promise<void> {
    try {
      // Initialize the local LLM engine
      // Note: In a production environment, we'd let the user choose the model
      this.engine = await webllm.CreateMLCEngine({
        modelId: 'Gemma-2b-it-q4f16_1-MLC',
      });
      
      this.app.stage.addChild(this.dialogueContainer);
      this.isReady = true;
      console.log('DialogueSystem: Local LLM Engine initialized successfully.');
    } catch (e) {
      console.error('DialogueSystem: Failed to initialize LLM engine:', e);
      throw e;
    }
  }

  update(delta: number): void {
    const mode = this.gameState.getMode();
    this.dialogueContainer.visible = (mode === GameMode.DIALOGUE);
  }

  async generateResponse(playerInput: string): Promise<string> {
    if (!this.engine || !this.isReady) return "... (LLM not ready) ...";

    const target = this.gameState.getState().interactionTarget;
    if (!target) return "Who are you talking to?";

    const prompt = this.constructPrompt(target, playerInput);
    
    try {
      const response = await this.engine.chat.completions.create({
        messages: [{ role: 'user', content: prompt }],
      });
      
      const text = response.choices[0].message.content || "...";
      await this.typewriterEffect(text);
      return text;
    } catch (e) {
      console.error('LLM Inference Error:', e);
      return "(The NPC glares at you blankly)";
    }
  }

  private constructPrompt(npc: NPC, input: string): string {
    const trait = npc.metadata?.trait || 'mysterious';
    return `You are ${npc.name}, a citizen of Aetheria. You are ${trait}. 
` +
           `The player is talking to you. Be concise and cinematic. 
` +
           `Player says: "${input}"`;
  }

  private async typewriterEffect(text: string): Promise<void> {
    this.isTyping = true;
    this.dialogueText.text = '';
    for (let i = 0; i < text.length; i++) {
      this.dialogueText.text += text[i];
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    this.isTyping = false;
  }

  endDialogue(): void {
    this.dialogueContainer.visible = false;
    this.dialogueText.text = '';
    this.gameState.setGameMode(GameMode.EXPLORATION);
    this.gameState.setInteractionTarget(null);
  }

  destroy(): void {
    if (this.engine) {
      this.engine.unload();
    }
    this.app.stage.removeChild(this.dialogueContainer);
  }
}
