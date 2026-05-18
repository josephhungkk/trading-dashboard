import Decimal from 'decimal.js'
import type { ComboEnvelope } from '@/services/combos/types'

interface Props {
  envelope: ComboEnvelope
  legs: { strike: string; put_call: string }[]
}

export function ComboPayoffChart({ envelope, legs }: Props) {
  const strikes = legs.length > 0
    ? legs.map(l => new Decimal(l.strike))
    : [new Decimal('100'), new Decimal('110')]
  const minStrike = Decimal.min(...strikes)
  const maxStrike = Decimal.max(...strikes)
  const pad = maxStrike.minus(minStrike).times('0.3').plus('5')
  const xMin = minStrike.minus(pad)
  const xMax = maxStrike.plus(pad)
  const range = xMax.minus(xMin)

  const toX = (price: Decimal) =>
    price.minus(xMin).dividedBy(range).times(200).toNumber()

  const be = envelope.break_even[0] !== undefined ? new Decimal(envelope.break_even[0]) : null

  return (
    <div className="bg-slate-900 rounded p-2 h-16 relative">
      <svg viewBox="0 0 200 50" style={{ width: '100%', height: '100%' }}>
        <line x1="0" y1="35" x2="200" y2="35" stroke="#475569" strokeWidth="0.5" strokeDasharray="2,2" />
        {be !== null && (
          <line
            x1={toX(be)} y1="0" x2={toX(be)} y2="50"
            stroke="#94a3b8" strokeWidth="0.5" strokeDasharray="2,2"
          />
        )}
        <text x="5" y="48" fill="#94a3b8" fontSize="6">{xMin.toFixed(0)}</text>
        <text x="175" y="48" fill="#94a3b8" fontSize="6">{xMax.toFixed(0)}</text>
      </svg>
      <div className="absolute top-1 right-2 text-xs text-slate-500">Payoff at expiry</div>
    </div>
  )
}
