import { describe, it, expect, beforeEach } from 'vitest';
import { GameState } from '../../../src/client/state/GameState';

describe('GameState', () => {
  let gameState: GameState;

  beforeEach(() => {
    gameState = new GameState();
  });

  it('should initialize with default values', () => {
    const state = gameState.getState();
    expect(state.player.x).toBe(0);
    expect(state.player.y).toBe(0);
    expect(state.player.z).toBe(0);
    expect(state.inventory.size).toBe(0);
  });

  it('should update player position correctly', () => {
    gameState.updatePlayerPosition(10, 20, 30);
    const state = gameState.getState();
    expect(state.player.x).toBe(10);
    expect(state.player.y).toBe(20);
    expect(state.player.z).toBe(30);
  });

  it('should add items to the inventory', () => {
    const item = { id: 'item1', name: 'Rusty Sword', type: 'weapon', properties: { damage: 5 } };
    gameState.addItem(item);
    const state = gameState.getState();
    expect(state.inventory.has('item1')).toBe(true);
    expect(state.inventory.get('item1')).toEqual(item);
  });

  it('should remove items from the inventory', () => {
    const item = { id: 'item1', name: 'Rusty Sword', type: 'weapon', properties: { damage: 5 } };
    gameState.addItem(item);
    gameState.removeItem('item1');
    const state = gameState.getState();
    expect(state.inventory.has('item1')).toBe(false);
  });

  it('should merge state updates via setState', () => {
    const updates = {
      player: { x: 100, y: 200, z: 300, hp: 80, maxHp: 100 }
    };
    gameState.setState(updates);
    const state = gameState.getState();
    expect(state.player.x).toBe(100);
    expect(state.player.hp).toBe(80);
  });
});
