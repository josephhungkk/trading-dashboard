import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { createEarningsHook } from '../../services/earnings/api'
import type { EarningsHookCreate } from '../../services/earnings/types'

interface EarningsHookDrawerProps {
  instrumentId: number
  accountId: string
  onClose: () => void
}

export function EarningsHookDrawer({
  instrumentId,
  accountId,
  onClose,
}: EarningsHookDrawerProps) {
  const queryClient = useQueryClient()
  const [hookType, setHookType] = useState<EarningsHookCreate['hook_type']>('auto_flat')
  const [minutesBefore, setMinutesBefore] = useState(30)
  const mutation = useMutation({
    mutationFn: createEarningsHook,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['earnings', 'hooks'] })
      onClose()
    },
  })

  return (
    <aside
      role="dialog"
      aria-modal="true"
      aria-label="Configure earnings hook"
      className="fixed inset-y-0 right-0 z-50 w-full max-w-sm border-l bg-background p-5 shadow-lg"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">Earnings hook</h2>
        <button type="button" className="rounded border px-2 py-1 text-sm" onClick={onClose}>
          Close
        </button>
      </div>
      <form
        className="mt-5 space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          mutation.mutate({
            instrument_id: instrumentId,
            account_id: accountId,
            hook_type: hookType,
            minutes_before: minutesBefore,
          })
        }}
      >
        <label className="block text-sm">
          <span className="mb-1 block font-medium">Hook type</span>
          <select
            className="w-full rounded border bg-background px-2 py-2"
            value={hookType}
            onChange={(event) =>
              setHookType(event.target.value as EarningsHookCreate['hook_type'])
            }
          >
            <option value="auto_flat">Auto-flat</option>
            <option value="auto_pause_bot">Pause bot</option>
          </select>
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium">Minutes before: {minutesBefore}</span>
          <input
            className="w-full"
            type="range"
            min={10}
            max={120}
            step={5}
            value={minutesBefore}
            onChange={(event) => setMinutesBefore(parseInt(event.target.value, 10))}
          />
        </label>
        {mutation.isError ? (
          <p className="text-sm text-red-600">Could not create the hook.</p>
        ) : null}
        <button
          type="submit"
          disabled={mutation.isPending}
          className="w-full rounded bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
        >
          {mutation.isPending ? 'Saving...' : 'Save hook'}
        </button>
      </form>
    </aside>
  )
}
