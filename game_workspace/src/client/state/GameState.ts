import { NPC } from '../core/systems/WorldSystem';

export enum GameMode {
  EXPLORATION = 'EXPLORATION',
  DIALOGUE = 'DIALOGUE',
  MENU = 'MENU'
}

export interface PlayerState {
  x: number;
  y: number;
  z: number;
  hp: number;
  maxHp: number;
}

export interface Item {
  id: string;
  name: string;
  type: string;
  properties: any;
}

export interface State {
  player: PlayerState;
  mode: GameMode;
  interactionTarget: NPC | null;
  inventory: Map<string, Item>;
}

export class GameState {
  private state: State = {
    player: { x: 0, y: 0, z: 0, hp: 100, maxHp: 100 },
    mode: GameMode.EXPLORATION,
    interactionTarget: null,
    inventory: new Map<string, Item>()
  };

  getState(): State {
    return { ...this.state };
  }

  updatePlayerPosition(x: number, y: number, z: number): void {
    this.state.player = { ...this.state.player, x, y, z };
  }

  setGameMode(mode: GameMode): void {
    this.state.mode = mode;
    if (mode === GameMode.EXPLORATION) {
      this.state.interactionTarget = null;
    }
  }

  setInteractionTarget(npc: NPC | null): void {
    this.state.interactionTarget = npc;
  }

  getMode(): GameMode {
    return this.state.mode;
  }

  // Inventory Management
  addItem(item: Item): void {
    this.state.inventory.set(item.id, item);
  }

  removeItem(itemId: string): void {
    this.state.inventory.delete(itemId);
  }

  // Generic State Updates
  setState(updates: Partial<State>): void {
    // Deep merge for player state if provided
    if (updates.player) {
      this.state.player = { ...this.state.player, ...updates.player };
    }
    
    // Merge other top-level properties
    Object.assign(this.state, updates);
  }
}
