import * as React from 'react';
import type { RollPreviewResponse } from '@/services/futures/types';
import { confirmRoll } from '@/services/futures/api';
import { mintCsrfNonce } from '@/services/admin/api';

interface Props {
  preview: RollPreviewResponse;
  onClose: () => void;
  onConfirmed: () => void;
}

function formatDate(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export function RollConfirmDialog({ preview, onClose, onConfirmed }: Props): React.JSX.Element {
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const handleConfirm = async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const csrfNonce = await mintCsrfNonce();
      await confirmRoll(preview.nonce, csrfNonce);
      onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Roll confirm failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Confirm contract roll"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
      tabIndex={-1}
    >
      <div
        className="bg-background rounded-lg border border-border p-6 w-full max-w-sm shadow-lg space-y-4"
      >
        <h2 className="text-base font-semibold">Confirm Contract Roll</h2>

        <div className="text-sm space-y-1">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Close</span>
            <span className="font-mono">{preview.close_symbol}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Open</span>
            <span className="font-mono">{preview.open_symbol}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Next expiry</span>
            <span className="font-mono">{formatDate(preview.expiry)}</span>
          </div>
        </div>

        {error != null && (
          <p className="text-sm text-red-600" role="alert">{error}</p>
        )}

        <div className="flex gap-2 justify-end pt-2">
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void handleConfirm()}
            disabled={loading}
            className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {loading ? 'Confirming…' : 'Confirm Roll'}
          </button>
        </div>
      </div>
    </div>
  );
}
