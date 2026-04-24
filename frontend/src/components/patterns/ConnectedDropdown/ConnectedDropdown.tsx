import * as React from 'react';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/primitives/DropdownMenu';
import { Button } from '@/components/primitives/Button';
import { Badge } from '@/components/primitives/Badge';
// eslint-disable-next-line boundaries/element-types -- live connection-health store is a global ambient source
import { useConnectedStore } from '@/stores/global/connected';

const VARIANT_MAP = { live: 'up', delayed: 'warn', down: 'down' } as const;

export function ConnectedDropdown(): React.JSX.Element {
  const statuses = useConnectedStore(s => s.statuses);
  const worst: keyof typeof VARIANT_MAP =
    statuses.some(s => s.state === 'down')    ? 'down'    :
    statuses.some(s => s.state === 'delayed') ? 'delayed' : 'live';

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" aria-label="connection health">
          <Badge variant={VARIANT_MAP[worst]}>Connected</Badge>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        {statuses.map(s => (
          <DropdownMenuItem
            key={`${s.assetClass}-${s.source}`}
            className="flex items-center justify-between gap-3"
          >
            <span className="flex-1 capitalize">{s.assetClass}</span>
            <span className="text-xs text-fg-muted">{s.source}</span>
            <Badge variant={VARIANT_MAP[s.state]}>{s.state}</Badge>
            <span className="w-16 text-right text-xs tabular-nums">
              {s.latencyMs == null ? '—' : `${s.latencyMs.toFixed(0)} ms`}
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
