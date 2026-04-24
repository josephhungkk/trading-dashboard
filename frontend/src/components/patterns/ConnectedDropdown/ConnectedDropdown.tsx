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
// eslint-disable-next-line boundaries/element-types -- broker label metadata
import { BROKERS } from '@/services/fixtures';
// eslint-disable-next-line boundaries/element-types -- type-only import from services/types for ConnectedStatus shape
import type { ConnectedStatus, BrokerId, Mode } from '@/services/types';

type Tone = 'green' | 'yellow' | 'red';
const TONE_VARIANT: Record<Tone, 'up' | 'warn' | 'down'> = { green: 'up', yellow: 'warn', red: 'down' };
const TONE_RANK: Record<Tone, number> = { green: 0, yellow: 1, red: 2 };

function rowTone(s: ConnectedStatus): Tone {
  if (s.backendOk && s.gatewayOk) return 'green';
  if (s.backendOk || s.gatewayOk) return 'yellow';
  return 'red';
}

function worstOf(rows: ConnectedStatus[]): Tone {
  return rows.reduce<Tone>((acc, r) => {
    const t = rowTone(r);
    return TONE_RANK[t] > TONE_RANK[acc] ? t : acc;
  }, 'green');
}

interface Group {
  broker: BrokerId;
  brokerName: string;
  mode?: Mode;
  label: string;
  rows: ConnectedStatus[];
  tone: Tone;
}

function groupStatuses(statuses: ConnectedStatus[]): Group[] {
  const out: Group[] = [];
  for (const b of BROKERS) {
    const mine = statuses.filter(s => s.broker === b.id);
    if (mine.length === 0) continue;
    const modes = Array.from(new Set(mine.map(s => s.mode).filter((m): m is Mode => m != null)));
    if (modes.length > 0) {
      for (const m of modes) {
        const rows = mine.filter(s => s.mode === m);
        out.push({
          broker: b.id,
          brokerName: b.name,
          mode: m,
          label: `${b.name} ${m === 'live' ? 'Live' : 'Paper'}`,
          rows,
          tone: worstOf(rows),
        });
      }
    } else {
      out.push({
        broker: b.id,
        brokerName: b.name,
        label: b.name,
        rows: mine,
        tone: worstOf(mine),
      });
    }
  }
  return out;
}

export function ConnectedDropdown(): React.JSX.Element {
  const statuses = useConnectedStore(s => s.statuses);
  const groups = groupStatuses(statuses);
  const worst = groups.reduce<Tone>((acc, g) => (TONE_RANK[g.tone] > TONE_RANK[acc] ? g.tone : acc), 'green');

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" aria-label="connection health">
          <Badge variant={TONE_VARIANT[worst]}>Connected</Badge>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        {groups.map(g => (
          <DropdownMenuItem
            key={`${g.broker}-${g.mode ?? 'default'}`}
            className="flex items-center justify-between gap-3"
          >
            <span className="flex-1 truncate">{g.label}</span>
            <span className="text-xs text-fg-muted tabular-nums">
              {g.rows.length} gw · {g.rows.filter(r => r.backendOk).length}/{g.rows.length} be · {g.rows.filter(r => r.gatewayOk).length}/{g.rows.length} gw
            </span>
            <Badge variant={TONE_VARIANT[g.tone]}>{g.tone}</Badge>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
