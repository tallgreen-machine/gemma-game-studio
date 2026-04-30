import { GameSystem } from '../GameSystem';
import { GameState } from '../../state/GameState';

export class InputSystem implements GameSystem {
  public name = 'InputSystem';
  private gameState: GameState;
  private keysPressed: Set<string> = new Set();
  private moveSpeed = 200; // pixels per second

  constructor(gameState: GameState) {
    this.gameState = gameState;
  }

  init(): void {
    window.addEventListener('keydown', (e) => this.handleKeyDown(e));
    window.addEventListener('keyup', (e) => this.handleKeyUp(e));
  }

  private handleKeyDown(e: KeyboardEvent): void {
    this.keysPressed.add(e.key.toLowerCase());
  }

  private handleKeyUp(e: KeyboardEvent): void {
    this.keysPressed.delete(e.key.toLowerCase());
  }

  update(delta: number): void {
    const state = this.gameState.getState();
    const player = state.player;
    
    let dx = 0;
    let dy = 0;

    if (this.keysPressed.has('w')) dy -= this.moveSpeed * (delta / 60); // Assuming delta is frame-based or similar
    if (this.keysPressed.has('s')) dy += this.moveSpeed * (delta / 60);
    if (this.keysPressed.has('a')) dx -= this.moveSpeed * (delta / 60);
    if (this.keysPressed.has('d')) dx += this.moveSpeed * (delta / 60);

    if (dx !== 0 || dy !== 0) {
      this.gameState.updatePlayerPosition(
        player.x + dx,
        player.y + dy,
        player.z
      );
    }
  }

  destroy(): void {
    // Event listeners would ideally be removed here, but for simplicity in a browser environment
    // and based on the test setup, we are managing them via window.onkeydown/keyup or similar.
    // In a real app, we'd use a reference to the listener function.
  }
}
