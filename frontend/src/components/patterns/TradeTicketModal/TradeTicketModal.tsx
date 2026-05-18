import * as React from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Local types (mirrors services/types — patterns layer cannot import services)
// ---------------------------------------------------------------------------

type DecimalString = string & { __brand: 'DecimalString' };

type Side = 'BUY' | 'SELL';
type OrderType = 'MARKET' | 'LIMIT' | 'STOP';
type Tif = 'DAY' | 'GTC';

interface PreviewResponse {
  nonce: string;
  cap_status: 'ok' | 'near' | 'exceeded';
  warnings: string[];
  [key: string]: unknown;
}

export type TradeTicketMode = 'place' | 'modify' | 'bracket';

export interface InitialOrder {
  conid: string;
  side: Side;
  order_type: OrderType;
  qty: number;
  limit_price?: number;
}

export interface TradeTicketModalProps {
  /** Controls which endpoint and field layout to use. Defaults to "place". */
  mode?: TradeTicketMode;
  /** Account UUID to attach orders to. Required. */
  accountId: string;
  /** Pre-filled contract identifier. */
  conid?: string;
  /** Pre-filled symbol hint (display only). */
  symbol?: string;
  /** Required when mode="modify". */
  orderId?: string;
  /** Pre-fill for mode="modify". */
  initialOrder?: InitialOrder;
  /** Initial bracket stop price (mode="bracket"). */
  stopPrice?: number;
  /** Initial bracket target price (mode="bracket"). */
  targetPrice?: number;
  /** Fired when the modal should close (user cancels or order succeeds). */
  onClose: () => void;
  /** Called after a successful submit with the returned order id. */
  onSuccess?: (orderId: string) => void;
  /** Optional slot for multi-leg combo builder (injected by features layer). */
  comboBuilderSlot?: React.ReactNode;
}

interface FormState {
  side: Side;
  conid: string;
  orderType: OrderType;
  qty: string;
  limitPrice: string;
  stopPrice: string;
  bracketStopPrice: string;
  bracketTargetPrice: string;
  tif: Tif;
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

function positiveInteger(value: string): boolean {
  return /^[1-9]\d*$/.test(value.trim());
}

function nonEmpty(value: string): boolean {
  return value.trim() !== '';
}

function buildInitialForm(
  conid: string | undefined,
  initialOrder: InitialOrder | undefined,
  stopPrice: number | undefined,
  targetPrice: number | undefined,
): FormState {
  if (initialOrder !== undefined) {
    return {
      side: initialOrder.side,
      conid: initialOrder.conid,
      orderType: initialOrder.order_type,
      qty: String(initialOrder.qty),
      limitPrice: initialOrder.limit_price !== undefined ? String(initialOrder.limit_price) : '',
      stopPrice: '',
      bracketStopPrice: stopPrice !== undefined ? String(stopPrice) : '',
      bracketTargetPrice: targetPrice !== undefined ? String(targetPrice) : '',
      tif: 'DAY',
    };
  }
  return {
    side: 'BUY',
    conid: conid ?? '',
    orderType: 'MARKET',
    qty: '',
    limitPrice: '',
    stopPrice: '',
    bracketStopPrice: stopPrice !== undefined ? String(stopPrice) : '',
    bracketTargetPrice: targetPrice !== undefined ? String(targetPrice) : '',
    tif: 'DAY',
  };
}

function submitLabel(mode: TradeTicketMode): string {
  if (mode === 'modify') return 'Modify Order';
  if (mode === 'bracket') return 'Place Bracket';
  return 'Place Order';
}

function submitUrl(mode: TradeTicketMode, orderId: string | undefined): string {
  if (mode === 'modify') return `/api/orders/${encodeURIComponent(orderId ?? '')}`;
  if (mode === 'bracket') return '/api/orders/bracket';
  return '/api/orders';
}

function submitMethod(mode: TradeTicketMode): string {
  return mode === 'modify' ? 'PUT' : 'POST';
}

/**
 * Validate bracket legs.
 * Returns null on success, or an error key string on failure.
 */
function validateBracket(
  side: Side,
  entryPriceStr: string,
  bracketStopStr: string,
  bracketTargetStr: string,
): string | null {
  const hasStop = nonEmpty(bracketStopStr);
  const hasTarget = nonEmpty(bracketTargetStr);
  if (!hasStop && !hasTarget) return 'bracket_invalid_legs';

  const entry = Number(entryPriceStr);

  if (hasStop) {
    const stop = Number(bracketStopStr);
    if (side === 'BUY' && stop >= entry) return 'bracket_invalid_prices';
    if (side === 'SELL' && stop <= entry) return 'bracket_invalid_prices';
  }
  if (hasTarget) {
    const target = Number(bracketTargetStr);
    if (side === 'BUY' && target <= entry) return 'bracket_invalid_prices';
    if (side === 'SELL' && target >= entry) return 'bracket_invalid_prices';
  }
  return null;
}

// ---------------------------------------------------------------------------
// Preview debounce hook
// ---------------------------------------------------------------------------

const PREVIEW_DEBOUNCE_MS = 300;

function useDebounceCallback(fn: () => void, delay: number): () => void {
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const fnRef = React.useRef(fn);

  // Keep fnRef current without causing the useCallback to re-create
  React.useEffect(() => {
    fnRef.current = fn;
  });

  const trigger = React.useCallback(() => {
    if (timerRef.current !== undefined) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      fnRef.current();
    }, delay);
  }, [delay]);

  React.useEffect(() => {
    return () => {
      if (timerRef.current !== undefined) clearTimeout(timerRef.current);
    };
  }, []);

  return trigger;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
].join(',');

function getFocusable(root: HTMLElement | null): HTMLElement[] {
  if (root === null) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => !el.hasAttribute('disabled') && el.tabIndex !== -1,
  );
}

export function TradeTicketModal({
  mode = 'place',
  accountId,
  conid,
  orderId,
  initialOrder,
  stopPrice,
  targetPrice,
  onClose,
  onSuccess,
  comboBuilderSlot,
}: TradeTicketModalProps): React.JSX.Element {
  const [form, setForm] = React.useState<FormState>(() =>
    buildInitialForm(conid, initialOrder, stopPrice, targetPrice),
  );
  const [preview, setPreview] = React.useState<PreviewResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [tradeMode, setTradeMode] = React.useState<'single' | 'combo'>('single');
  const dialogRef = React.useRef<HTMLDivElement>(null);

  const isModify = mode === 'modify';
  const isBracket = mode === 'bracket';
  const needsLimitPrice = form.orderType === 'LIMIT';
  const needsStopPrice = form.orderType === 'STOP';

  const canPreview =
    nonEmpty(form.conid) &&
    positiveInteger(form.qty) &&
    (!needsLimitPrice || nonEmpty(form.limitPrice)) &&
    (!needsStopPrice || nonEmpty(form.stopPrice));

  // ------------------------------------------------------------------
  // Preview (debounced) — fires on every form change
  // ------------------------------------------------------------------

  const fetchPreview = React.useCallback(async (): Promise<PreviewResponse | null> => {
    if (!canPreview) return null;
    const body = {
      account_id: accountId,
      conid: form.conid.trim(),
      side: form.side,
      order_type: form.orderType,
      tif: form.tif,
      qty: form.qty.trim() as DecimalString,
      limit_price: needsLimitPrice ? (form.limitPrice.trim() as DecimalString) : null,
      stop_price: needsStopPrice ? (form.stopPrice.trim() as DecimalString) : null,
    };
    try {
      const response = await fetch('/api/orders/preview', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) return null;
      const data = (await response.json()) as PreviewResponse;
      setPreview(data);
      return data;
    } catch {
      // Preview errors are non-blocking
      return null;
    }
  }, [accountId, canPreview, form, needsLimitPrice, needsStopPrice]);

  const debouncedPreview = useDebounceCallback(() => {
    void fetchPreview();
  }, PREVIEW_DEBOUNCE_MS);

  React.useEffect(() => {
    debouncedPreview();
  }, [form, debouncedPreview]);

  // ------------------------------------------------------------------
  // Submit
  // ------------------------------------------------------------------

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);

    if (isBracket) {
      const entryPrice = needsLimitPrice ? form.limitPrice : '0';
      const bracketError = validateBracket(
        form.side,
        entryPrice,
        form.bracketStopPrice,
        form.bracketTargetPrice,
      );
      if (bracketError !== null) {
        setError(bracketError);
        return;
      }
    }

    setSubmitting(true);
    try {
      // 5c v0.5.5: force a fresh preview synchronously so the nonce hashes the
      // EXACT body we're about to submit. Without this, a fast user can submit
      // before the 300ms debounce settles and the stale nonce mismatches the
      // current form values → backend 422 payload_mismatch.
      const fresh = await fetchPreview();
      const body: Record<string, unknown> = {
        account_id: accountId,
        conid: form.conid.trim(),
        side: form.side,
        order_type: form.orderType,
        tif: form.tif,
        qty: form.qty.trim() as DecimalString,
        limit_price: needsLimitPrice ? (form.limitPrice.trim() as DecimalString) : null,
        stop_price: needsStopPrice ? (form.stopPrice.trim() as DecimalString) : null,
        nonce: fresh?.nonce ?? preview?.nonce ?? null,
      };

      if (isBracket) {
        if (nonEmpty(form.bracketStopPrice)) {
          body.bracket_stop_price = form.bracketStopPrice.trim() as DecimalString;
        }
        if (nonEmpty(form.bracketTargetPrice)) {
          body.bracket_target_price = form.bracketTargetPrice.trim() as DecimalString;
        }
      }

      const url = submitUrl(mode, orderId);
      const method = submitMethod(mode);

      const response = await fetch(url, {
        method,
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!response.ok) {
        const text = await response.text().catch(() => '');
        setError(`submit_failed: ${response.status}${nonEmpty(text) ? ` — ${text}` : ''}`);
        return;
      }

      const result = (await response.json()) as { id?: string };
      onSuccess?.(result.id ?? '');
      onClose();
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : 'unknown_error';
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  // ------------------------------------------------------------------
  // Keyboard trap + initial focus
  // ------------------------------------------------------------------

  React.useEffect(() => {
    const focusable = getFocusable(dialogRef.current);
    focusable[0]?.focus();
  }, []);

  React.useEffect(() => {
    function onKeyDown(e: KeyboardEvent): void {
      if (dialogRef.current === null) return;
      if (!dialogRef.current.contains(document.activeElement)) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== 'Tab') return;
      const focusable = getFocusable(dialogRef.current);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last?.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first?.focus();
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  // ------------------------------------------------------------------
  // IDs for a11y
  // ------------------------------------------------------------------

  const conidId = React.useId();
  const orderTypeId = React.useId();
  const qtyId = React.useId();
  const limitPriceId = React.useId();
  const stopPriceId = React.useId();
  const tifId = React.useId();
  const bracketStopId = React.useId();
  const bracketTargetId = React.useId();

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  return (
    <div className="fixed inset-0 z-50 bg-bg/80">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="pattern-trade-ticket-title"
        tabIndex={-1}
        className={cn(
          'fixed inset-0 flex flex-col border-border bg-panel p-4 text-fg shadow-lg',
          'md:inset-auto md:left-1/2 md:top-1/2 md:max-h-[calc(100vh-4rem)] md:w-full md:max-w-[34rem]',
          'md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-lg md:border md:p-5',
        )}
      >
        <header className="mb-4 flex items-center justify-between gap-3">
          <h2 id="pattern-trade-ticket-title" className="text-lg font-semibold">
            {mode === 'modify' ? 'Modify Order' : mode === 'bracket' ? 'Bracket Order' : 'Trade Ticket'}
          </h2>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
            aria-label="Close trade ticket"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </header>

        <div className="mb-3 flex gap-2 border-b border-border pb-2">
          <button
            type="button"
            onClick={() => setTradeMode('single')}
            className={`rounded px-3 py-1 text-sm ${tradeMode === 'single' ? 'bg-panel-active font-semibold text-fg' : 'text-fg-muted'}`}
          >
            Single
          </button>
          <button
            type="button"
            onClick={() => setTradeMode('combo')}
            className={`rounded px-3 py-1 text-sm ${tradeMode === 'combo' ? 'bg-panel-active font-semibold text-fg' : 'text-fg-muted'}`}
          >
            Multi-Leg
          </button>
        </div>
        {tradeMode === 'combo' ? comboBuilderSlot ?? null : null}

        {tradeMode === 'single' && error !== null ? (
          <div
            role="alert"
            data-testid="trade-ticket-error"
            className="mb-3 rounded-md border border-destructive/60 bg-destructive/10 p-3 text-sm text-destructive"
          >
            {error}
          </div>
        ) : null}

        {tradeMode === 'single' && <form
          className="flex flex-1 flex-col gap-4 overflow-auto"
          onSubmit={(e) => {
            void handleSubmit(e);
          }}
        >
          {/* Side */}
          <fieldset className="grid grid-cols-2 gap-2" aria-label="Side">
            {(['BUY', 'SELL'] as const).map((s) => (
              <Button
                key={s}
                type="button"
                variant={form.side === s ? 'default' : 'outline'}
                disabled={isModify}
                onClick={() => {
                  setForm((prev) => ({ ...prev, side: s }));
                }}
              >
                {s}
              </Button>
            ))}
          </fieldset>

          {/* Contract / conid */}
          <label htmlFor={conidId} className="flex flex-col gap-1 text-sm font-medium">
            Contract
            <Input
              id={conidId}
              aria-label="Contract"
              data-testid="trade-ticket-conid"
              value={form.conid}
              disabled={isModify}
              onChange={(e) => {
                const val = e.currentTarget.value;
                setForm((prev) => ({ ...prev, conid: val }));
              }}
            />
          </label>

          {/* Order type */}
          <label htmlFor={orderTypeId} className="flex flex-col gap-1 text-sm font-medium">
            Order type
            <select
              id={orderTypeId}
              aria-label="Order type"
              data-testid="trade-ticket-order-type"
              className="h-10 rounded-md border border-border bg-panel px-3 text-sm text-fg disabled:opacity-50"
              value={form.orderType}
              disabled={isModify}
              onChange={(e) => {
                const orderType = e.currentTarget.value as OrderType;
                setForm((prev) => ({ ...prev, orderType }));
              }}
            >
              <option value="MARKET">MARKET</option>
              <option value="LIMIT">LIMIT</option>
              <option value="STOP">STOP</option>
            </select>
          </label>

          {/* Qty */}
          <label htmlFor={qtyId} className="flex flex-col gap-1 text-sm font-medium">
            Qty
            <Input
              id={qtyId}
              aria-label="Qty"
              inputMode="decimal"
              variant="numeric"
              value={form.qty}
              onChange={(e) => {
                const qty = e.currentTarget.value;
                setForm((prev) => ({ ...prev, qty }));
              }}
            />
          </label>

          {/* Limit price */}
          {needsLimitPrice ? (
            <label htmlFor={limitPriceId} className="flex flex-col gap-1 text-sm font-medium">
              Limit price
              <Input
                id={limitPriceId}
                aria-label="Limit price"
                inputMode="decimal"
                variant="numeric"
                value={form.limitPrice}
                onChange={(e) => {
                  const limitPrice = e.currentTarget.value;
                  setForm((prev) => ({ ...prev, limitPrice }));
                }}
              />
            </label>
          ) : null}

          {/* Stop price (for STOP order type) */}
          {needsStopPrice ? (
            <label htmlFor={stopPriceId} className="flex flex-col gap-1 text-sm font-medium">
              Stop price
              <Input
                id={stopPriceId}
                aria-label="Stop price"
                inputMode="decimal"
                variant="numeric"
                value={form.stopPrice}
                onChange={(e) => {
                  const sp = e.currentTarget.value;
                  setForm((prev) => ({ ...prev, stopPrice: sp }));
                }}
              />
            </label>
          ) : null}

          {/* TIF */}
          <label htmlFor={tifId} className="flex flex-col gap-1 text-sm font-medium">
            TIF
            <select
              id={tifId}
              aria-label="TIF"
              className="h-10 rounded-md border border-border bg-panel px-3 text-sm text-fg"
              value={form.tif}
              onChange={(e) => {
                const tif = e.currentTarget.value as Tif;
                setForm((prev) => ({ ...prev, tif }));
              }}
            >
              <option value="DAY">DAY</option>
              <option value="GTC">GTC</option>
            </select>
          </label>

          {/* Bracket legs (mode="bracket" only) */}
          {isBracket ? (
            <>
              <label htmlFor={bracketStopId} className="flex flex-col gap-1 text-sm font-medium">
                Bracket stop price
                <Input
                  id={bracketStopId}
                  aria-label="Bracket stop price"
                  data-testid="trade-ticket-bracket-stop"
                  inputMode="decimal"
                  variant="numeric"
                  value={form.bracketStopPrice}
                  onChange={(e) => {
                    const val = e.currentTarget.value;
                    setForm((prev) => ({ ...prev, bracketStopPrice: val }));
                  }}
                />
              </label>
              <label htmlFor={bracketTargetId} className="flex flex-col gap-1 text-sm font-medium">
                Bracket target price
                <Input
                  id={bracketTargetId}
                  aria-label="Bracket target price"
                  data-testid="trade-ticket-bracket-target"
                  inputMode="decimal"
                  variant="numeric"
                  value={form.bracketTargetPrice}
                  onChange={(e) => {
                    const val = e.currentTarget.value;
                    setForm((prev) => ({ ...prev, bracketTargetPrice: val }));
                  }}
                />
              </label>
            </>
          ) : null}

          <div className="mt-2 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={submitting || (!isBracket && !canPreview)}
              data-testid="trade-ticket-submit"
            >
              {submitting ? 'Submitting…' : submitLabel(mode)}
            </Button>
          </div>
        </form>}
      </div>
    </div>
  );
}
