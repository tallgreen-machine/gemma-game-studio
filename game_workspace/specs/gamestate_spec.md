# GameState Technical Specification

## 1. Purpose
The `GameState` class is the single source of truth for the client-side state of 'Aetheria'. It manages the player's current status, world interaction data, and serves as the bridge between the rendering engine and the networking layer.

## 2. Core Responsibilities
- **Player State**: Track position (x, y, z), orientation, and health.
- **Inventory**: Manage a list of items and their metadata.
- **World State**: Store local copies of nearby entities and environmental flags.
- **Synchronization**: Provide methods to serialize state for WebSocket transmission and deserialize updates from the server.

## 3. API Definition
- `constructor()`: Initializes state with default values.
- `updatePlayerPosition(x: number, y: number, z: number)`: Updates the player coordinates.
- `addItem(item: Item)`: Adds an item to the inventory.
- `removeItem(itemId: string)`: Removes an item by ID.
- `getState()`: Returns a read-only snapshot of the current state.
- `setState(data: Partial<GameState>)`: Updates state based on server synchronization.

## 4. Data Structures
- **Player**: `{ x: number, y: number, z: number, hp: number, maxHp: number }`
- **Item**: `{ id: string, name: string, type: string, properties: Record<string, any> }`
- **Inventory**: `Map<string, Item>`

## 5. Test Requirements
- Ensure player position updates correctly.
- Verify items can be added and removed from the inventory.
- Validate that `getState()` returns a correct snapshot.
- Confirm `setState()` properly merges server updates.
