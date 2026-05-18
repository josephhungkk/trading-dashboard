import { useState, useEffect } from 'react'
import { StrategyPicker } from './StrategyPicker'
import { LegSlot } from './LegSlot'
import { ComboPayoffChart } from './ComboPayoffChart'
import { ComboSummary } from './ComboSummary'
import { previewCombo, confirmCombo, listCombos } from '@/services/combos/api'
import type { ComboEnvelope, PreviewResponse } from '@/services/combos/types'

interface Props {
  accountId: string
  onClose: () => void
}

export function ComboBuilder({ accountId, onClose }: Props) {
  const [strategy, setStrategy] = useState('VERTICAL')
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [envelope, setEnvelope] = useState<ComboEnvelope | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    listCombos(accountId, 'pending_submit').then(data => {
      if (data?.items?.length > 0) {
        const item = data.items[0];
        if (!item) return;
        setEnvelope({
          net_debit_credit: item.net_debit_credit,
          kind: item.net_debit_credit_kind as 'DEBIT' | 'CREDIT',
          max_loss: item.max_loss,
          max_profit: item.max_profit,
          break_even: item.break_even,
        })
      }
    }).catch(
      // ignore pending-combo load errors on mount
      (reason: unknown) => void reason
    )
  }, [accountId])

  async function handlePreview() {
    setLoading(true)
    setError(null)
    try {
      const result = await previewCombo({
        strategy_type: strategy,
        underlying_symbol: 'AAPL',
        underlying_canonical_id: 'AAPL',
        tif: 'DAY',
        legs: [],
      })
      setPreview(result)
      setEnvelope(result.envelope)
    } catch (e: unknown) {
      const err = e as { detail?: { reason?: string } }
      setError(err?.detail?.reason ?? 'Preview failed')
    } finally {
      setLoading(false)
    }
  }

  async function handleConfirm() {
    if (!preview || !envelope) return
    setLoading(true)
    setError(null)
    try {
      await confirmCombo(preview.csrf_nonce, {
        client_combo_id: preview.client_combo_id,
        legs: [],
        underlying_canonical_id: 'AAPL',
        strategy_type: preview.strategy_type,
        underlying_symbol: 'AAPL',
        tif: 'DAY',
        net_debit_credit: envelope.net_debit_credit,
        net_debit_credit_kind: envelope.kind,
      })
      onClose()
    } catch (e: unknown) {
      const err = e as { detail?: { error_code?: string } }
      setError(err?.detail?.error_code ?? 'Confirm failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <StrategyPicker value={strategy} onChange={setStrategy} />
      <LegSlot legIdx={0} side="buy" label="Leg 1 — select from chain" bid="—" ask="—" />
      <LegSlot legIdx={1} side="sell" label="Leg 2 — select from chain" bid="—" ask="—" />
      {envelope !== null && <ComboPayoffChart envelope={envelope} legs={[]} />}
      {envelope !== null && <ComboSummary envelope={envelope} />}
      {error !== null && <p className="text-red-400 text-xs">{error}</p>}
      <div className="flex gap-2 mt-1">
        <button onClick={onClose} className="flex-1 border border-slate-600 rounded py-1 text-sm">
          Cancel
        </button>
        {preview === null ? (
          <button
            onClick={handlePreview}
            disabled={loading}
            className="flex-1 bg-sky-600 rounded py-1 text-sm text-white disabled:opacity-50"
          >
            {loading ? 'Loading…' : 'Preview →'}
          </button>
        ) : (
          <button
            onClick={handleConfirm}
            disabled={loading}
            className="flex-1 bg-sky-600 rounded py-1 text-sm text-white disabled:opacity-50"
          >
            {loading ? 'Loading…' : 'Confirm'}
          </button>
        )}
      </div>
    </div>
  )
}
