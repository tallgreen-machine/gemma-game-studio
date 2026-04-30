import { Application } from 'pixi.js';
import { GameState } from '../state/GameState';
import { GameSystem } from './systems/GameSystem';

export class CoreEngine {
  private app: Application | null = null;
  private gameState: GameState;
  private systems: Map<string, GameSystem> = new Map();
  public isInitialized: boolean = false;
  public isRunning: boolean = false;

  constructor(gameState: GameState) {
    this.gameState = gameState;
  }

  async init(): Promise<void> {
    this.app = new Application();
    // The app view will be appended to the DOM in main.ts
    this.isInitialized = true;
  }

  addSystem(system: GameSystem): void {
    this.systems.set(system.name, system);
    if (this.isInitialized && system.init) {
      system.init();
    }
  }

  removeSystem(name: string): void {
    const system = this.systems.get(name);
    if (system && system.destroy) {
      system.destroy();
    }
    this.systems.delete(name);
  }

  start(): void {
    if (!this.isInitialized) {
      throw new Error('Engine must be initialized before starting');
    }
    if (this.app) {
      this.app.ticker.add((delta) => this.update(delta));
      this.app.ticker.start();
    }
    this.isRunning = true;
  }

  stop(): void {
    if (this.app) {
      this.app.ticker.stop();
    }
    this.isRunning = false;
  }

  update(delta: number): void {
    if (!this.isRunning) return;
    
    for (const system of this.systems.values()) {
      if (system.update) {
        system.update(delta);
      }
    }
  }

  destroy(): void {
    for (const system of this.systems.values()) {
      if (system.destroy) system.destroy();
    }
    if (this.app) {
      this.app.destroy(true, { children: true, texture: true });
    }
    this.isInitialized = false;
    this.isRunning = false;
  }

  getApp(): Application | null {
    return this.app;
  }

  getGameState(): GameState {
    return this.gameState;
  }
}
