import { GameSystem } from '../GameSystem';
import { GameState } from '../../state/GameState';

export interface WorldObject {
  id: string;
  type: string;
  x: number;
  y: number;
  metadata: any;
}

export interface NPC extends WorldObject {
  type: 'npc';
  name: string;
  dialogueSeed: number;
}

export interface Chunk {
  x: number;
  y: number;
  objects: WorldObject[];
  npcs: NPC[];
}

export class WorldSystem implements GameSystem {
  public name = 'WorldSystem';
  private gameState: GameState;
  private seed: string;
  private chunks: Map<string, Chunk> = new Map();
  private chunkSize = 1000;

  constructor(gameState: GameState, seed: string) {
    this.gameState = gameState;
    this.seed = seed;
  }

  init(): Promise<void> {
    return Promise.resolve();
  }

  update(delta: number): void {
    // Handle NPC migrations or world events here
  }

  public getChunkAt(cx: number, cy: number): Chunk {
    const key = `${cx},${cy}`;
    if (this.chunks.has(key)) {
      return this.chunks.get(key)!;
    }

    const chunk = this.generateChunk(cx, cy);
    this.chunks.set(key, chunk);
    return chunk;
  }

  public getPlayerChunk(): { x: number, y: number } {
    const player = this.gameState.getState().player;
    return {
      x: Math.floor(player.x / this.chunkSize),
      y: Math.floor(player.y / this.chunkSize)
    };
  }

  public isChunkLoaded(cx: number, cy: number): boolean {
    return this.chunks.has(`${cx},${cy}`);
  }

  private generateChunk(cx: number, cy: number): Chunk {
    const objects: WorldObject[] = [];
    const npcs: NPC[] = [];
    
    const seedValue = this.hashString(`${this.seed}-${cx}-${cy}`);
    
    // Generate Static Objects (Rocks/Trees)
    const objectCount = (seedValue % 5) + 2;
    for (let i = 0; i < objectCount; i++) {
      const objSeed = this.hashString(`${this.seed}-${cx}-${cy}-obj-${i}`);
      objects.push({
        id: `obj-${cx}-${cy}-${i}`,
        type: objSeed % 2 === 0 ? 'rock' : 'tree',
        x: (objSeed * 1103515245) % this.chunkSize + (cx * this.chunkSize),
        y: (objSeed * 12345) % this.chunkSize + (cy * this.chunkSize),
        metadata: { seed: objSeed }
      });
    }

    // Generate NPCs (Rare spawn: 1 per chunk max, 30% chance)
    if ((seedValue % 100) < 30) {
      const npcSeed = this.hashString(`${this.seed}-${cx}-${cy}-npc`);
      npcs.push({
        id: `npc-${cx}-${cy}`,
        type: 'npc',
        name: this.generateNPCName(npcSeed),
        x: (npcSeed * 1234567) % this.chunkSize + (cx * this.chunkSize),
        y: (npcSeed * 7654321) % this.chunkSize + (cy * this.chunkSize),
        dialogueSeed: npcSeed,
        metadata: { seed: npcSeed }
      });
    }

    return { x: cx, y: cy, objects, npcs };
  }

  private generateNPCName(seed: number): string {
    const names = ['Kaelen', 'Lyra', 'Theron', 'Syla', 'Marek', 'Elara', 'Vael', 'Zora'];
    return names[seed % names.length];
  }

  private hashString(str: string): number {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      const char = str.charCodeAt(i);
      hash = ((hash << 5) - hash) + char;
      hash |= 0;
    }
    return Math.abs(hash);
  }

  destroy(): void {
    this.chunks.clear();
  }
}
