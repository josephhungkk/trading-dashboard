import { useQuery } from '@tanstack/react-query';

import { fetchExerciseElections } from '@/services/options/api';

export function useExerciseElections() {
  return useQuery({
    queryKey: ['exercise-elections'],
    queryFn: fetchExerciseElections,
    staleTime: 30_000,
  });
}
