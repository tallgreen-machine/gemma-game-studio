export interface GameSystem {
  name: string;
  init?(): Promise<void>;
  update?(delta: number): void;
  destroy?(): void;
}
