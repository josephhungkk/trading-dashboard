// frontend/src/services/algo/types.ts

export type AlgoStrategy =
  | 'ADAPTIVE'
  | 'TWAP'
  | 'VWAP'
  | 'ARRIVAL_PRICE'
  | 'ICEBERG'
  | 'RESERVE'
  | 'DARK_ICE';

export const DISPLAY_ALGOS: ReadonlySet<AlgoStrategy> = new Set([
  'ICEBERG',
  'RESERVE',
  'DARK_ICE',
]);

export interface AlgoParamSchema {
  name: string;
  type: 'enum' | 'time' | 'decimal' | 'boolean';
  values?: string[];
  required: boolean;
}

export interface AlgoCapabilityEntry {
  strategy: AlgoStrategy;
  params: AlgoParamSchema[];
}

export interface AlgoCapabilitiesResponse {
  strategies: AlgoCapabilityEntry[];
}

export interface AlgoSchemasResponse {
  schemas: Record<AlgoStrategy, AlgoParamSchema[]>;
}

export interface AlgoOrderFields {
  algo_strategy: AlgoStrategy;
  algo_params: Record<string, string>;
}
