import { describe, it, expect, beforeEach, vi } from 'vitest';
import { InputSystem } from '../../../src/client/core/systems/InputSystem';
import { GameState } from '../../../src/client/state/GameState';

describe('InputSystem', () => {
  let inputSystem: InputSystem;
  let gameState: GameState;

  beforeEach(() => {
    gameState = new GameState();
    inputSystem = new InputSystem(gameState);
    inputSystem.init();
    
    // Clean up window listeners between tests
    window.onkeydown = null;
    window.onkeyup = null;
  });

  it('should track key presses on keydown event', () => {
    const event = new KeyboardEvent('keydown', { key: 'w' });
    window.dispatchEvent(event);
    
    const initialY = gameState.getState().player.y;
    inputSystem.update(1);
    expect(gameState.getState().player.y).not.toBe(initialY);
  });

  it('should stop tracking keys on keyup event', () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'w' }));
    window.dispatchEvent(new KeyboardEvent('keyup', { key: 'w' }));
    
    const initialY = gameState.getState().player.y;
    inputSystem.update(1);
    expect(gameState.getState().player.y).toBe(initialY);
  });

  it('should move the player in the correct directions', () => {
    const initialPos = { ...gameState.getState().player };
    
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'w' }));
    inputSystem.update(1);
    const yAfterW = gameState.getState().player.y;
    
    window.dispatchEvent(new KeyboardEvent('keyup', { key: 'w' }));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 's' }));
    inputSystem.update(1);
    const yAfterS = gameState.getState().player.y;
    
    expect(yAfterW).toBeLessThan(initialPos.y);
    expect(yAfterS).toBeGreaterThan(yAfterW);
  });

  it('should move the player horizontally', () => {
    const initialX = gameState.getState().player.x;
    
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'd' }));
    inputSystem.update(1);
    expect(gameState.getState().player.x).toBeGreaterThan(initialX);
  });
});
