export type {
  PutCall,
  OptionStyle,
  OptionChainRow,
  OptionChainData,
  ExerciseCandidate,
  ExerciseElection,
} from '@/services/options/types';

export interface GreeksSnapshot {
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  iv: number | null;
}

export interface WsChainFrame {
  type: 'chain' | 'stale' | 'heartbeat' | 'canonicalized' | 'subscription_capped';
  calls?: import('@/services/options/types').OptionChainRow[];
  puts?: import('@/services/options/types').OptionChainRow[];
  source?: string;
  fetched_at_ms?: number;
  conid?: string;
  canonical_id?: string;
}
