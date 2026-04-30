import { GameSystem } from '../GameSystem';
import { GameState, GameMode } from '../../state/GameState';
import { WorldSystem, NPC }
from './WorldSystem';

export class InteractionSystem implements GameSystem {
  public name = 'InteractionSystem';
  private gameState: GameState;
  private worldSystem: WorldSystem;
  private interactionRadius = 100; // pixels

  constructor(gameState: GameState, worldSystem: WorldSystem) {
    this.gameState = gameState;
    this.worldSystem = worldSystem;
  }

  init(): void {
    window.addEventListener('keydown', (e) => this.handleKeyDown(e));
  }

  private handleKeyDown(e: KeyboardEvent): void {
    if (e.key.toLowerCase() === 'e') {
      this.attemptInteraction();
    }
  }

  private attemptInteraction(): void {
    const player = this.gameState.getState().player;
    const chunkCoords = this.worldSystem.getPlayerChunk();
    
    let closestNpc: NPC | null = null;
    let minDistance = this.interactionRadius;

    // Check current and surrounding chunks for NPCs
    for (let x = chunkCoords.x - 1; x <= chunkCoords.x + 1; x++) {
      for (let y = chunkCoords.y - 1; y <= chunkCoords.y + 1; y++) {
        const chunk = this.worldSystem.getChunkAt(x, y);
        
        for (const npc of chunk.npcs) {
          const dist = this.calculateDistance(player.x, player.y, npc.x, npc.y);
          if (dist < minDistance) {
            minDistance = dist;
            closestNpc = npc;
          }
        }
      }
    }

    if (closestNpc) {
      this.gameState.setInteractionTarget(closestNpc);
      this.gameState.setGameMode(GameMode.DIALOGUE);
      console.log(`Started dialogue with ${closestNpc.name}`);
    } else {
      console.log('No interactable NPC nearby');
    }
  }

  private calculateDistance(x1: number, y1: number, x2: number, y2: number): number {
    return Math.sqrt(Math.pow(x2 - x1, 2) + Math.pow(y2 - y1, 2));
  }

  update(delta: number): void {
    // Proximity logic is currently event-driven via keydown
  }

  destroy(): void {
    // Cleanup would go here
  }
}
