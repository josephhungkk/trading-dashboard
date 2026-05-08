import * as React from 'react';
import { mintModifyNonce, submitModify } from './services/orders';

export interface ConfirmDialogProps {
  open: boolean;
  legId: string;
  type: 'sl' | 'tp';
  currentPrice: number;
  newPrice: number;
  tickSize: number;
  onCancel: () => void;
  onConfirmed: (nonce: string) => void;
  onError: (reason: string) => void;
}

export function ConfirmDialog(props: ConfirmDialogProps): React.JSX.Element | null {
  const {
    open,
    legId,
    type,
    currentPrice,
    newPrice,
    tickSize,
    onCancel,
    onConfirmed,
    onError,
  } = props;
  const [nonceEntry, setNonceEntry] = React.useState<{ legId: string; nonce: string } | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const nonce = nonceEntry?.legId === legId ? nonceEntry.nonce : null;
  // minting derived: open with no nonce yet means a mint is in-flight.
  const minting = open && nonce === null;

  // HIGH-3: AbortController aborts the in-flight mint fetch on unmount or when the
  // dialog closes, preventing orphaned Redis nonces from accumulating.
  // All setState calls are inside async callbacks (not in the synchronous effect body)
  // to satisfy react-hooks/set-state-in-effect.
  React.useEffect(() => {
    if (!open) return undefined;

    const controller = new AbortController();
    mintModifyNonce(legId, controller.signal)
      .then((r) => {
        setNonceEntry({ legId, nonce: r.nonce });
      })
      .catch((err: unknown) => {
        const error = err as { name?: string };
        if (error.name === 'AbortError') return; // expected on unmount/close
        setNonceEntry(null);
        onError('could not start modify');
      });

    return () => {
      controller.abort();
    };
  }, [open, legId, onError]);

  const handleConfirm = async (): Promise<void> => {
    if (!nonce) return;
    setSubmitting(true);
    try {
      const result = await submitModify({
        orderId: legId,
        stopPrice: newPrice,
        nonce,
      });
      if (result.accepted) {
        onConfirmed(nonce);
      } else {
        onError(result.reason);
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  const tickFmt = tickSize.toFixed(2);
  const newFmt = newPrice.toFixed(2);
  const message = `Move ${type.toUpperCase()} from $${currentPrice.toFixed(2)} to $${newFmt} (rounded to $${tickFmt} tick)?`;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
    >
      <div className="w-full max-w-md rounded border border-border bg-background p-4 shadow-md">
        <h2 className="text-lg font-semibold">Confirm Modify</h2>
        <p className="my-2">{message}</p>
        {minting && <p className="text-muted-foreground">Preparing…</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onCancel} disabled={submitting}>
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              void handleConfirm();
            }}
            disabled={!nonce || minting || submitting}
            aria-busy={submitting}
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  );
}
