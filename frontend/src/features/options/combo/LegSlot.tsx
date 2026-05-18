interface LegSlotProps {
  legIdx: number
  side: 'buy' | 'sell'
  label: string
  bid: string
  ask: string
}

export function LegSlot(props: LegSlotProps) {
  const { side, label, bid, ask } = props;
  const badge = side === 'buy' ? 'BTO' : 'STO'
  const badgeColor = side === 'buy' ? 'bg-green-600' : 'bg-red-600'
  return (
    <div className="flex items-center gap-2 border border-slate-600 rounded p-2">
      <span className={`text-xs font-bold text-white px-1 rounded ${badgeColor}`}>{badge}</span>
      <span className="flex-1 font-mono text-sm">{label}</span>
      <span className="text-xs text-slate-400">{bid}/{ask}</span>
    </div>
  )
}
