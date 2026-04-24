import type { Mode } from '@/services/types';
declare const brand: unique symbol;
export type Scoped<M extends Mode, T> = T & { readonly [brand]: M };
