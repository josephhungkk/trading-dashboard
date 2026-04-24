import * as React from 'react';

export function useMediaQuery(query: string): boolean {
  const subscribe = React.useCallback(
    (cb: () => void) => {
      const mql = window.matchMedia(query);
      mql.addEventListener('change', cb);
      return () => {
        mql.removeEventListener('change', cb);
      };
    },
    [query],
  );
  const get = React.useCallback(() => window.matchMedia(query).matches, [query]);
  return React.useSyncExternalStore(subscribe, get, () => false);
}
