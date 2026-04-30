import { GameSystem } from '../GameSystem';
import { GameState } from '../../state/GameState';
import { Application, Sprite, Graphics, Container, Texture, Text } from 'pixi.js';
import { WorldSystem, WorldObject, NPC } from './WorldSystem';

export class RenderSystem implements GameSystem {
  public name = 'RenderSystem';
  private gameState: GameState;
  private app: Application;
  private worldSystem?: WorldSystem;
  public playerSprite: Sprite | null = null;
  public isInitialized = false;

  // Cinematic elements
  private viewport: Container;
  private letterbox: Graphics;
  private worldContainer: Container;
  private objectSprites: Map<string, Sprite> = new Map();
  private npcSprites: Map<string, { sprite: Sprite, label: Text }> = new Map();

  constructor(gameState: GameState, app: Application, worldSystem?: WorldSystem) {
    this.gameState = gameState;
    this.app = app;
    this.worldSystem = worldSystem;
    this.viewport = new Container();
    this.worldContainer = new Container();
    this.letterbox = new Graphics();
  }

  async init(): Promise<void> {
    this.viewport.addChild(this.worldContainer);
    this.app.stage.addChild(this.viewport);
    this.app.stage.addChild(this.letterbox);

    this.playerSprite = new Sprite(this.createPlaceholderTexture('player'));
    this.playerSprite.anchor.set(0.5);
    this.playerSprite.tint = 0x00FF00; // Green for player
    this.worldContainer.addChild(this.playerSprite);

    this.setupCinematicFraming();
    this.isInitialized = true;
  }

  private setupCinematicFraming(): void {
    const width = this.app.screen.width;
    const height = this.app.screen.height;
    const barHeight = height * 0.1;

    this.letterbox.clear();
    this.letterbox.beginFill(0x000000);
    this.letterbox.drawRect(0, 0, width, barHeight);
    this.letterbox.drawRect(0, height - barHeight, width, barHeight);
    this.letterbox.endFill();
  }

  private createPlaceholderTexture(type: string): any {
    return Texture.from('https://pixijs.com/assets/bunny.png');
  }

  update(delta: number): void {
    if (!this.isInitialized || !this.playerSprite) return;

    const state = this.gameState.getState();
    const player = state.player;

    this.playerSprite.x = player.x;
    this.playerSprite.y = player.y;

    if (this.worldSystem) {
      this.updateWorldRendering(player);
    }

    this.viewport.x = this.app.screen.width / 2 - player.x;
    this.viewport.y = this.app.screen.height / 2 - player.y;
  }

  private updateWorldRendering(player: { x: number, y: number, z: number }): void {
    const chunkCoords = this.worldSystem.getPlayerChunk();
    
    for (let x = chunkCoords.x - 1; x <= chunkCoords.x + 1; x++) {
      for (let y = chunkCoords.y - 1; y <= chunkCoords.y + 1; y++) {
        const chunk = this.worldSystem.getChunkAt(x, y);
        this.renderChunk(chunk);
      }
    }

    this.cleanupRemoteSprites(chunkCoords);
  }

  private renderChunk(chunk: any): void {
    // Render Static Objects
    chunk.objects.forEach((obj: WorldObject) => {
      if (!this.objectSprites.has(obj.id)) {
        const sprite = new Sprite(this.createPlaceholderTexture(obj.type));
        sprite.anchor.set(0.5);
        sprite.x = obj.x;
        sprite.y = obj.y;
        sprite.tint = obj.type === 'rock' ? 0x888888 : 0x228B22;
        this.worldContainer.addChild(sprite);
        this.objectSprites.set(obj.id, sprite);
      }
    });

    // Render NPCs
    chunk.npcs.forEach((npc: NPC) => {
      if (!this.npcSprites.has(npc.id)) {
        const sprite = new Sprite(this.createPlaceholderTexture('npc'));
        sprite.anchor.set(0.5);
        sprite.x = npc.x;
        sprite.y = npc.y;
        sprite.tint = 0x0000FF; // Blue for NPCs
        sprite.scale.set(1.2); // NPCs are slightly larger

        const label = new Text(npc.name, { 
          fontSize: 14, 
          fill: 0xffffff, 
          align: 'center' 
        });
        label.anchor.set(0.5);
        label.y = -20; // Position above head

        const npcContainer = new Container();
        npcContainer.addChild(sprite);
        npcContainer.addChild(label);
        npcContainer.x = npc.x;
        npcContainer.y = npc.y;

        this.worldContainer.addChild(npcContainer);
        this.npcSprites.set(npc.id, { sprite: npcContainer as any, label });
      }
    });
  }

  private cleanupRemoteSprites(chunkCoords: { x: number, y: number }): void {
    // Cleanup static objects
    for (const [id, sprite] of this.objectSprites.entries()) {
      if (!this.isSpriteInViewRange(sprite, chunkCoords)) {
        this.worldContainer.removeChild(sprite);
        this.objectSprites.delete(id);
      }
    }

    // Cleanup NPCs
    for (const [id, data] of this.npcSprites.entries()) {
      if (!this.isSpriteInViewRange(data.sprite, chunkCoords)) {
        this.worldContainer.removeChild(data.sprite);
        this.npcSprites.delete(id);
      }
    }
  }

  private isSpriteInViewRange(sprite: any, chunkCoords: { x: number, y: number }): boolean {
    const objX = sprite.x;
    const objY = sprite.y;
    const cx = Math.floor(objX / 1000);
    const cy = Math.floor(objY / 1000);
    return Math.abs(cx - chunkCoords.x) <= 1 && Math.abs(cy - chunkCoords.y) <= 1;
  }

  destroy(): void {
    this.worldContainer.destroy({ children: true });
    this.app.stage.removeChild(this.viewport);
    this.app.stage.removeChild(this.letterbox);
    this.isInitialized = false;
  }
}
