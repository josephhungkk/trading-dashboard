import Decimal from 'decimal.js'
import type { ComboEnvelope } from '@/services/combos/types'

interface Props { envelope: ComboEnvelope }

export function ComboSummary({ envelope }: Props) {
  const nd = new Decimal(envelope.net_debit_credit)
  const label = envelope.kind === 'DEBIT' ? 'Net Debit' : 'Net Credit'
  return (
    <div className="flex justify-between text-sm font-mono mt-2">
      <span>{label} <strong className="text-orange-400">${nd.toFixed(2)}</strong></span>
      {envelope.max_loss !== null && (
        <span>Max loss <strong>${new Decimal(envelope.max_loss).dividedBy(100).toFixed(2)}</strong></span>
      )}
      {envelope.max_profit !== null && (
        <span>Max profit <strong>${new Decimal(envelope.max_profit).dividedBy(100).toFixed(2)}</strong></span>
      )}
      {envelope.break_even[0] !== undefined && (
        <span>BE <strong>${new Decimal(envelope.break_even[0]).toFixed(2)}</strong></span>
      )}
    </div>
  )
}
