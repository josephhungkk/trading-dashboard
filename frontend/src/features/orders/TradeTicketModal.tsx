import * as React from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';
import { cn } from '@/lib/utils';
import { useToast } from '@/hooks/use-toast';
import { previewOrder, placeOrder } from '@/services/orders';
import type { DecimalString, PreviewRequest, PreviewResponse } from '@/services/types';
import { useOrdersStore, type OrderResponse as StoredOrderResponse } from '@/stores/global/orders';
import { useActiveStores } from '@/stores/registry';
import { ContractSearchInput, type ContractSearchInputValue } from './ContractSearchInput';
import { tradeTicketStore, useTradeTicketStore } from './use-trade-ticket';

type Side = PreviewRequest['side'];
type OrderType = PreviewRequest['order_type'];
type Tif = PreviewRequest['tif'];
type TradeTicketContract = ContractSearchInputValue & {
  asset_class?: string;
};

interface MaintenanceBanner {
  kind: 'maintenance';
  seconds: number;
}

interface KillSwitchBanner {
  kind: 'kill-switch';
}

type BlockingBanner = MaintenanceBanner | KillSwitchBanner;

interface FormState {
  side: Side;
  contract: ContractSearchInputValue;
  orderType: OrderType;
  qty: string;
  limitPrice: string;
  stopPrice: string;
  tif: Tif;
}

const initialForm: FormState = {
  side: 'BUY',
  contract: { conid: '', symbol: '' },
  orderType: 'MARKET',
  qty: '',
  limitPrice: '',
  stopPrice: '',
  tif: 'DAY',
};

const focusableSelector = [
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function TradeTicketModal({
  storyBanner = null,
}: {
  storyBanner?: BlockingBanner | null;
}): React.JSX.Element | null {
  const isOpen = useTradeTicketStore((s) => s.isOpen);
  const clientOrderId = useTradeTicketStore((s) => s.clientOrderId);

  if (!isOpen) return null;
  return <TradeTicketModalContent key={clientOrderId} storyBanner={storyBanner} />;
}

function TradeTicketModalContent({
  storyBanner,
}: {
  storyBanner: BlockingBanner | null;
}): React.JSX.Element {
  const accountId = useTradeTicketStore((s) => s.accountId);
  const accountsStore = useActiveStores().useAccounts;
  const activeBroker = accountsStore(
    (s) => s.accounts.find((a) => a.id === accountId)?.broker,
  );
  const defaultConid = useTradeTicketStore((s) => s.defaultConid);
  const defaultSymbol = useTradeTicketStore((s) => s.defaultSymbol);
  const clientOrderId = useTradeTicketStore((s) => s.clientOrderId);
  const preview = useTradeTicketStore((s) => s.preview);
  const inFlight = useTradeTicketStore((s) => s.inFlight);
  const { toast } = useToast();
  const addOrder = useOrdersStore((s) => s.addOrder);
  const dialogRef = React.useRef<HTMLDivElement>(null);
  const previousFocusRef = React.useRef<HTMLElement | null>(null);
  const placingRef = React.useRef(false);
  const [form, setForm] = React.useState<FormState>(() => ({
    ...initialForm,
    contract: { conid: defaultConid ?? '', symbol: defaultSymbol ?? '' },
  }));
  const [attestedExtreme, setAttestedExtreme] = React.useState(false);
  const [banner, setBanner] = React.useState<BlockingBanner | null>(storyBanner);
  const [previewError, setPreviewError] = React.useState<string | null>(null);

  React.useEffect(() => {
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    window.setTimeout(() => {
      const first = getFocusable(dialogRef.current)[0];
      first?.focus();
    }, 0);
  }, []);

  React.useEffect(() => {
    if (banner?.kind !== 'maintenance' || banner.seconds <= 0) return;
    const id = window.setTimeout(() => {
      setBanner((current) => current?.kind === 'maintenance'
        ? { kind: 'maintenance', seconds: Math.max(0, current.seconds - 1) }
        : current);
    }, 1000);
    return () => window.clearTimeout(id);
  }, [banner]);

  const close = React.useCallback(() => {
    tradeTicketStore.getState().close();
    const previous = previousFocusRef.current;
    window.setTimeout(() => previous?.focus(), 0);
  }, []);

  const request = React.useMemo(() => {
    if (accountId === null) return null;
    return buildRequest(accountId, form);
  }, [accountId, form]);

  const previewDisabled = request === null;
  const confirmDisabled = inFlight
    || preview === null
    || preview.cap_status === 'exceeded'
    || (preview.position_sanity.status === 'extreme' && !attestedExtreme);

  async function handlePreview(): Promise<void> {
    if (request === null) return;
    setPreviewError(null);
    setBanner(null);
    const response = await previewOrder(request);
    tradeTicketStore.getState().setPreview(response);
    setAttestedExtreme(false);
  }

  async function handleConfirm(): Promise<void> {
    const current = tradeTicketStore.getState();
    if (request === null || preview === null || current.inFlight || placingRef.current || current.clientOrderId === null) return;
    placingRef.current = true;
    current.setInFlight(true);
    setBanner(null);
    try {
      const result = await placeOrder(request, preview.nonce, current.clientOrderId);
      const storedOrder: StoredOrderResponse = {
        ...result.order,
        last_event_at: result.order.last_event_at ?? result.order.updated_at,
      };
      addOrder(storedOrder);
      toast({ title: 'Order submitted', tone: 'success' });
      close();
    } catch (error) {
      const retryAfter = retryAfterSeconds(error);
      if (retryAfter !== null) {
        setBanner({ kind: 'maintenance', seconds: retryAfter });
      } else {
        setBanner({ kind: 'kill-switch' });
      }
    } finally {
      placingRef.current = false;
      tradeTicketStore.getState().setInFlight(false);
    }
  }

  React.useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (!dialogRef.current?.contains(document.activeElement)) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = getFocusable(dialogRef.current);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [close]);

  return (
    <div className="fixed inset-0 z-50 bg-bg/80">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="trade-ticket-title"
        tabIndex={-1}
        className={cn(
          'fixed inset-0 flex flex-col border-border bg-panel p-4 text-fg shadow-lg',
          'md:inset-auto md:left-1/2 md:top-1/2 md:max-h-[calc(100vh-4rem)] md:w-full md:max-w-[34rem]',
          'md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-lg md:border md:p-5',
        )}
      >
        <header className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 id="trade-ticket-title" className="text-lg font-semibold">Trade ticket</h2>
            {accountId !== null ? (
              <p data-testid="trade-ticket-account-id" className="sr-only">{accountId}</p>
            ) : null}
            {clientOrderId !== null ? (
              <p className="text-xs text-fg-muted">Client order {clientOrderId}</p>
            ) : null}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={close}
            aria-label="Close trade ticket"
            data-focus-end="true"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </header>

        <div className="flex-1 overflow-auto">
          {banner !== null ? <BlockingBannerView banner={banner} /> : null}
          {previewError !== null ? (
            <div className="mb-3 rounded-md border border-destructive/60 bg-destructive/10 p-3 text-sm text-destructive">
              {previewError}
            </div>
          ) : null}

          {preview === null ? (
            <TradeTicketForm
              form={form}
              setForm={setForm}
              onPreview={() => {
                void handlePreview().catch(() => setPreviewError('Preview failed'));
              }}
              previewDisabled={previewDisabled}
              {...(activeBroker === 'ibkr' || activeBroker === 'futu'
                ? { broker: activeBroker }
                : {})}
            />
          ) : (
            <PreviewStep
              preview={preview}
              attestedExtreme={attestedExtreme}
              setAttestedExtreme={setAttestedExtreme}
              confirmDisabled={confirmDisabled}
              inFlight={inFlight}
              onBack={() => tradeTicketStore.getState().setPreview(null)}
              onConfirm={() => { void handleConfirm(); }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function TradeTicketForm({
  form,
  setForm,
  onPreview,
  previewDisabled,
  broker,
}: {
  form: FormState;
  setForm: React.Dispatch<React.SetStateAction<FormState>>;
  onPreview: () => void;
  previewDisabled: boolean;
  broker?: 'ibkr' | 'futu';
}): React.JSX.Element {
  const limitRequired = form.orderType === 'LIMIT' && form.limitPrice.trim() === '';
  const stopRequired = form.orderType === 'STOP' && form.stopPrice.trim() === '';
  const canPreview = !previewDisabled && !limitRequired && !stopRequired;
  const contractInputId = React.useId();
  const orderTypeId = React.useId();
  const qtyId = React.useId();
  const limitPriceId = React.useId();
  const stopPriceId = React.useId();
  const tifId = React.useId();
  const ac = ((form.contract as TradeTicketContract).asset_class ?? '').toUpperCase();
  const stopDisabled = ac === 'WARRANT' || ac === 'CBBC';

  React.useEffect(() => {
    if (stopDisabled && form.orderType === 'STOP') {
      setForm((s) => ({ ...s, orderType: 'LIMIT' }));
    }
  }, [stopDisabled, form.orderType, setForm]);

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (canPreview) onPreview();
      }}
    >
      <fieldset className="grid grid-cols-2 gap-2" aria-label="Side">
        {(['BUY', 'SELL'] as const).map((side) => (
          <Button
            key={side}
            type="button"
            variant={form.side === side ? 'default' : 'outline'}
            onClick={() => setForm((s) => ({ ...s, side }))}
          >
            {side}
          </Button>
        ))}
      </fieldset>

      <label htmlFor={contractInputId} className="flex flex-col gap-1 text-sm font-medium">
        Contract
        <ContractSearchInput
          id={contractInputId}
          onSelect={(contract) => setForm((s) => ({ ...s, contract }))}
          {...(broker !== undefined ? { broker } : {})}
        />
      </label>

      <label htmlFor={orderTypeId} className="flex flex-col gap-1 text-sm font-medium">
        Order type
        <select
          id={orderTypeId}
          className="h-10 rounded-md border border-border bg-panel px-3 text-sm text-fg"
          value={form.orderType}
          onChange={(event) => {
            const orderType = event.currentTarget.value as OrderType;
            setForm((s) => ({ ...s, orderType }));
          }}
        >
          <option value="MARKET">MARKET</option>
          <option value="LIMIT">LIMIT</option>
          <option value="STOP" disabled={stopDisabled}>
            STOP{stopDisabled ? ' (unavailable for HK warrants/CBBC)' : ''}
          </option>
        </select>
      </label>

      <label htmlFor={qtyId} className="flex flex-col gap-1 text-sm font-medium">
        Qty
        <Input
          id={qtyId}
          inputMode="decimal"
          variant="numeric"
          value={form.qty}
          onChange={(event) => {
            const qty = event.currentTarget.value;
            setForm((s) => ({ ...s, qty }));
          }}
        />
      </label>

      {form.orderType === 'LIMIT' ? (
        <label htmlFor={limitPriceId} className="flex flex-col gap-1 text-sm font-medium">
          Limit price
          <Input
            id={limitPriceId}
            inputMode="decimal"
            variant="numeric"
            value={form.limitPrice}
            onChange={(event) => {
              const limitPrice = event.currentTarget.value;
              setForm((s) => ({ ...s, limitPrice }));
            }}
          />
        </label>
      ) : null}

      {form.orderType === 'STOP' ? (
        <label htmlFor={stopPriceId} className="flex flex-col gap-1 text-sm font-medium">
          Stop price
          <Input
            id={stopPriceId}
            inputMode="decimal"
            variant="numeric"
            value={form.stopPrice}
            onChange={(event) => {
              const stopPrice = event.currentTarget.value;
              setForm((s) => ({ ...s, stopPrice }));
            }}
          />
        </label>
      ) : null}

      <label htmlFor={tifId} className="flex flex-col gap-1 text-sm font-medium">
        TIF
        <select
          id={tifId}
          className="h-10 rounded-md border border-border bg-panel px-3 text-sm text-fg"
          value={form.tif}
          onChange={(event) => {
            const tif = event.currentTarget.value as Tif;
            setForm((s) => ({ ...s, tif }));
          }}
        >
          <option value="DAY">DAY</option>
          <option value="GTC">GTC</option>
        </select>
      </label>

      <Button type="submit" disabled={!canPreview}>Preview</Button>
    </form>
  );
}

function PreviewStep({
  preview,
  attestedExtreme,
  setAttestedExtreme,
  confirmDisabled,
  inFlight,
  onBack,
  onConfirm,
}: {
  preview: PreviewResponse;
  attestedExtreme: boolean;
  setAttestedExtreme: (value: boolean) => void;
  confirmDisabled: boolean;
  inFlight: boolean;
  onBack: () => void;
  onConfirm: () => void;
}): React.JSX.Element {
  return (
    <section className="flex flex-col gap-4">
      <dl className="grid grid-cols-2 gap-3 rounded-md border border-border p-3 text-sm">
        <dt className="text-fg-muted">Cap status</dt>
        <dd className="font-medium">{preview.cap_status}</dd>
        <dt className="text-fg-muted">Notional</dt>
        <dd className="font-mono">{preview.notional} {preview.notional_currency}</dd>
        <dt className="text-fg-muted">Position sanity</dt>
        <dd className="font-medium">{preview.position_sanity.status}</dd>
      </dl>

      {preview.warnings.length > 0 ? (
        <ul className="rounded-md border border-warning/60 bg-warning/10 p-3 text-sm" aria-label="Warnings">
          {preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}
        </ul>
      ) : null}

      {preview.position_sanity.status === 'extreme' ? (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={attestedExtreme}
            onChange={(event) => setAttestedExtreme(event.currentTarget.checked)}
          />
          I understand this is an extreme position size
        </label>
      ) : null}

      <div className="mt-2 flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onBack}>Back</Button>
        <Button type="button" onClick={onConfirm} disabled={confirmDisabled}>
          {inFlight ? 'Confirming' : 'Confirm'}
        </Button>
      </div>
    </section>
  );
}

function BlockingBannerView({ banner }: { banner: BlockingBanner }): React.JSX.Element {
  const text = banner.kind === 'maintenance'
    ? `Broker maintenance - retrying in ${banner.seconds}s`
    : 'Trading suspended by kill-switch';
  return (
    <div className="mb-3 rounded-md border border-destructive/60 bg-destructive/10 p-3 text-sm font-medium text-destructive">
      {text}
    </div>
  );
}

function buildRequest(accountId: string, form: FormState): PreviewRequest | null {
  const conid = form.contract.conid.trim() || form.contract.symbol.trim();
  const qty = form.qty.trim();
  if (conid === '' || !positiveInteger(qty)) return null;
  if (form.orderType === 'LIMIT' && form.limitPrice.trim() === '') return null;
  if (form.orderType === 'STOP' && form.stopPrice.trim() === '') return null;
  return {
    account_id: accountId,
    conid,
    side: form.side,
    order_type: form.orderType,
    tif: form.tif,
    qty: qty as DecimalString,
    limit_price: form.orderType === 'LIMIT' ? form.limitPrice.trim() as DecimalString : null,
    stop_price: form.orderType === 'STOP' ? form.stopPrice.trim() as DecimalString : null,
  };
}

function positiveInteger(value: string): boolean {
  return /^[1-9]\d*$/.test(value.trim());
}

function getFocusable(root: HTMLElement | null): HTMLElement[] {
  if (root === null) return [];
  const elements = Array.from(root.querySelectorAll<HTMLElement>(focusableSelector))
    .filter((element) => !element.hasAttribute('disabled') && element.tabIndex !== -1);
  const regular = elements.filter((element) => element.dataset['focusEnd'] !== 'true');
  const end = elements.filter((element) => element.dataset['focusEnd'] === 'true');
  return [...regular, ...end];
}

function retryAfterSeconds(error: unknown): number | null {
  if (typeof error !== 'object' || error === null) return null;
  const record = error as { retryAfter?: unknown; headers?: { get?: (key: string) => string | null } };
  if (typeof record.retryAfter === 'string') {
    const parsed = Number.parseInt(record.retryAfter, 10);
    return Number.isFinite(parsed) ? parsed : null;
  }
  const header = record.headers?.get?.('Retry-After');
  if (header !== null && header !== undefined) {
    const parsed = Number.parseInt(header, 10);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}
