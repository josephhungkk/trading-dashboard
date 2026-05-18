import * as React from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';
import { cn } from '@/lib/utils';
import { useFocusedSymbol } from '@/hooks/useFocusedSymbol';
import { useToast } from '@/hooks/use-toast';
import type { BrokerCapabilitiesResponse } from '@/services/capabilities/types';
import { useBrokerCapabilities } from '@/services/capabilities/useBrokerCapabilities';
import { previewOrder, placeOrder, RiskGateBlockedError, type RiskBlocker } from '@/services/orders';
import type { DecimalString, PreviewRequest, PreviewResponse } from '@/services/types';
import { useOrdersStore, type OrderResponse as StoredOrderResponse } from '@/stores/global/orders';
import { useActiveStores } from '@/stores/registry';
import type { SizingMethod, SizingRequest } from '@/services/sizing/types';
import { usePositionSizing } from '@/services/sizing/usePositionSizing';
import { useSizingDefaults } from '@/services/sizing/useSizingDefaults';
import { ContractSearchInput, type ContractSearchInputValue } from './ContractSearchInput';
import { TradeTicketAiSection } from '@/features/orders/TradeTicketAiSection';
import { tradeTicketStore, useTradeTicketStore } from './use-trade-ticket';
import { OptionDetailsSection } from '@/features/options/OptionDetailsSection';
import { ComboBuilder } from '@/features/options/combo/ComboBuilder';
import { FutureDetailsSection } from '@/features/futures/FutureDetailsSection';

type Side = PreviewRequest['side'];
type SubmittableOrderType = PreviewRequest['order_type'];
type OrderType = SubmittableOrderType | 'TRAIL' | 'TRAIL_LIMIT' | 'MOC' | 'MOO' | 'LOC' | 'LOO';
type Tif = PreviewRequest['tif'];
type TradeTicketContract = ContractSearchInputValue & {
  asset_class?: string;
  optionRow?: import('@/features/options/types').OptionChainRow;
  expiryIso?: string;
  positionEffect?: 'OPEN' | 'CLOSE';
  futureContract?: import('@/services/futures/types').FutureContractMonth;
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

const ORDER_TYPES = ['MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT', 'TRAIL', 'TRAIL_LIMIT', 'MOC', 'MOO', 'LOC', 'LOO'] as const;
const TIFS = ['DAY', 'GTC', 'IOC', 'FOK'] as const;
const NOT_SUPPORTED = 'Not supported for this broker';
const LOADING_CAPABILITIES = 'Loading capabilities...';

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
  brokerId,
}: {
  storyBanner?: BlockingBanner | null;
  brokerId?: string | null;
}): React.JSX.Element | null {
  const isOpen = useTradeTicketStore((s) => s.isOpen);
  const clientOrderId = useTradeTicketStore((s) => s.clientOrderId);

  if (!isOpen) return null;
  return (
    <TradeTicketModalContent
      key={clientOrderId}
      storyBanner={storyBanner}
      {...(brokerId !== undefined ? { brokerId } : {})}
    />
  );
}

function TradeTicketModalContent({
  storyBanner,
  brokerId,
}: {
  storyBanner: BlockingBanner | null;
  brokerId?: string | null;
}): React.JSX.Element {
  const accountId = useTradeTicketStore((s) => s.accountId);
  const accountsStore = useActiveStores().useAccounts;
  const activeBroker = accountsStore(
    (s) => s.accounts.find((a) => a.id === accountId)?.broker,
  );
  const effectiveBrokerId = brokerId ?? activeBroker ?? null;
  const capabilities = useBrokerCapabilities(effectiveBrokerId);
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
  const focusedSymbol = form.contract.symbol.trim() || defaultSymbol || null;
  useFocusedSymbol(focusedSymbol);
  const [attestedExtreme, setAttestedExtreme] = React.useState(false);
  // Phase 10a E2: separate acknowledgement for risk-gate WARN verdicts; reset
  // on every preview so a fresh acknowledgement is required per quote refresh.
  const [acknowledgedRiskWarnings, setAcknowledgedRiskWarnings] = React.useState(false);
  const [banner, setBanner] = React.useState<BlockingBanner | null>(storyBanner);
  const [previewError, setPreviewError] = React.useState<string | null>(null);
  // E7-fix (spec/quality H2): place_order can also return 422
  // risk_gate_blocked AFTER a clean preview (e.g. a fresh kill switch
  // flipped between preview and confirm). Surface those server-side
  // blockers in the same banner shape preview uses.
  const [placeOrderBlockers, setPlaceOrderBlockers] = React.useState<readonly RiskBlocker[]>([]);
  const [tradeMode, setTradeMode] = React.useState<'single' | 'combo'>('single');

  React.useEffect(() => {
    if (effectiveBrokerId === null || capabilities.data === undefined || capabilities.isError) return undefined;
    if (capabilities.isSupported(form.orderType, form.tif)) return undefined;
    const fallback = firstSupportedCombo(capabilities.data, form.orderType, form.tif);
    if (fallback === null) return undefined;
    const id = window.setTimeout(() => {
      setForm((current) => ({
        ...current,
        orderType: fallback.orderType,
        tif: fallback.tif,
      }));
    }, 0);
    return () => window.clearTimeout(id);
  }, [capabilities, effectiveBrokerId, form.orderType, form.tif]);

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
    setPlaceOrderBlockers([]);
    const response = await previewOrder(request);
    tradeTicketStore.getState().setPreview(response);
    setAttestedExtreme(false);
    setAcknowledgedRiskWarnings(false);
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
      } else if (error instanceof RiskGateBlockedError) {
        // E7-fix (spec/quality H2): surface server-side risk blockers
        // returned from place_order's 422 response. The PreviewStep
        // renders them in the same banner shape as preview-time
        // blockers (via the placeOrderBlockers state).
        setPlaceOrderBlockers(error.blockers);
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

        {tradeMode === 'combo' && accountId !== null ? (
          <div className="flex-1 overflow-auto">
            <ComboBuilder accountId={accountId} onClose={close} />
          </div>
        ) : null}

        <div className={`flex-1 overflow-auto${tradeMode === 'combo' ? ' hidden' : ''}`}>
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
              accountId={accountId}
              brokerId={effectiveBrokerId}
              capabilities={capabilities}
              {...(activeBroker === 'ibkr' || activeBroker === 'futu'
                ? { broker: activeBroker }
                : {})}
            />
          ) : (
            <PreviewStep
              preview={preview}
              attestedExtreme={attestedExtreme}
              setAttestedExtreme={setAttestedExtreme}
              acknowledgedRiskWarnings={acknowledgedRiskWarnings}
              setAcknowledgedRiskWarnings={setAcknowledgedRiskWarnings}
              placeOrderBlockers={placeOrderBlockers}
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
  accountId,
  broker,
  brokerId,
  capabilities,
}: {
  form: FormState;
  setForm: React.Dispatch<React.SetStateAction<FormState>>;
  onPreview: () => void;
  previewDisabled: boolean;
  accountId: string | null;
  broker?: 'ibkr' | 'futu';
  brokerId: string | null;
  capabilities: ReturnType<typeof useBrokerCapabilities>;
}): React.JSX.Element {
  const capabilityLoading = brokerId !== null && capabilities.isLoading;
  const capabilityError = brokerId !== null && capabilities.isError;
  const limitRequired = (form.orderType === 'LIMIT' || form.orderType === 'STOP_LIMIT') && form.limitPrice.trim() === '';
  const stopRequired = (form.orderType === 'STOP' || form.orderType === 'STOP_LIMIT') && form.stopPrice.trim() === '';
  // MED-4: include !capabilityError so the gate fails closed, not open, on
  // capability API errors. The backend capability check remains the authoritative
  // gate; this prevents optimistic placement when the FE has no valid data.
  const canPreview = !previewDisabled
    && !capabilityLoading
    && !capabilityError
    && isSubmittableOrderType(form.orderType)
    && !limitRequired
    && !stopRequired;
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

  // ── Phase 10b.1 — position-sizing section state ──────────────────────────
  const [sizingOpen, setSizingOpen] = React.useState(false);
  const sizingDefaults = useSizingDefaults(accountId ?? undefined);
  const [sizingMethodOverride, setSizingMethodOverride] =
    React.useState<SizingMethod | null>(null);
  const [sizingPctOverride, setSizingPctOverride] = React.useState<string | null>(null);

  // Derived: prefer the operator's in-modal override, else the per-account
  // default from /api/risk/sizing-defaults, else a sensible static fallback.
  // Computed during render avoids the react-hooks/set-state-in-effect smell.
  const sizingMethod: SizingMethod =
    sizingMethodOverride ?? sizingDefaults.data?.method ?? 'fixed_fractional';
  const sizingPct =
    sizingPctOverride
    ?? (sizingDefaults.data
      ? sizingMethod === 'fixed_fractional'
        ? String(sizingDefaults.data.fixed_fractional_risk_pct)
        : sizingMethod === 'risk_per_trade'
          ? String(sizingDefaults.data.risk_per_trade_risk_pct)
          : String(sizingDefaults.data.vol_targeted_target_vol_pct)
      : '2.00');

  // Build the sizing request when the section is open and we have enough
  // inputs to compute. conid is the broker-side identifier; the BE
  // resolves it to instrument_id via InstrumentResolver (10b1-d0).
  const sizingPriceInput = form.limitPrice.trim() || form.qty.trim();
  const conid = form.contract.conid.trim() || form.contract.symbol.trim();
  const sizingRequest: SizingRequest | null =
    sizingOpen && accountId && brokerId && conid && sizingPriceInput
      ? {
          account_id: accountId,
          conid,
          broker_id: brokerId,
          method: sizingMethod,
          side: form.side.toLowerCase() as 'buy' | 'sell',
          inputs:
            sizingMethod === 'fixed_fractional'
              ? {
                  kind: 'fixed_fractional',
                  risk_pct: sizingPct,
                  price: sizingPriceInput,
                }
              : sizingMethod === 'risk_per_trade'
                ? {
                    kind: 'risk_per_trade',
                    risk_pct: sizingPct,
                    entry: sizingPriceInput,
                    stop: form.stopPrice.trim() || '0',
                  }
                : {
                    kind: 'vol_targeted',
                    target_vol_pct: sizingPct,
                    price: sizingPriceInput,
                  },
        }
      : null;
  const sizing = usePositionSizing(sizingRequest);

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (canPreview) onPreview();
      }}
    >
      {/* MED-4: fail-closed warning when capability data is unavailable */}
      {capabilityError && (
        <div role="alert" className="rounded-md bg-yellow-50 px-3 py-2 text-sm text-yellow-800">
          Unable to load order capabilities. Preview is disabled until data is available.
        </div>
      )}
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
          disabled={capabilityLoading}
          onChange={(event) => {
            const orderType = event.currentTarget.value as OrderType;
            setForm((s) => ({ ...s, orderType }));
          }}
        >
          {capabilityLoading ? <option value={form.orderType}>{LOADING_CAPABILITIES}</option> : null}
          {!capabilityLoading ? ORDER_TYPES.map((orderType) => {
            const disabledReason = orderTypeDisabledReason(orderType, form.tif, stopDisabled, capabilities, capabilityError, brokerId);
            return (
              <option key={orderType} value={orderType} disabled={disabledReason !== null} title={disabledReason ?? undefined}>
                {optionLabel(orderType, disabledReason)}
              </option>
            );
          }) : null}
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

      {form.orderType === 'LIMIT' || form.orderType === 'STOP_LIMIT' ? (
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

      {form.orderType === 'STOP' || form.orderType === 'STOP_LIMIT' ? (
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
          disabled={capabilityLoading}
          onChange={(event) => {
            const tif = event.currentTarget.value as Tif;
            setForm((s) => ({ ...s, tif }));
          }}
        >
          {capabilityLoading ? <option value={form.tif}>{LOADING_CAPABILITIES}</option> : null}
          {!capabilityLoading ? TIFS.map((tif) => {
            const disabledReason = tifDisabledReason(form.orderType, tif, capabilities, capabilityError, brokerId);
            return (
              <option key={tif} value={tif} disabled={disabledReason !== null} title={disabledReason ?? undefined}>
                {optionLabel(tif, disabledReason)}
              </option>
            );
          }) : null}
        </select>
      </label>

      {/* ── Phase 12 — Option details section ───────────────────────── */}
      {(form.contract as TradeTicketContract).asset_class === 'OPTION' &&
        (form.contract as TradeTicketContract).optionRow != null && (
          <OptionDetailsSection
            row={(form.contract as TradeTicketContract).optionRow as NonNullable<TradeTicketContract['optionRow']>}
            underlyingSymbol={form.contract.symbol.trim()}
            expiryIso={(form.contract as TradeTicketContract).expiryIso ?? ''}
            onSideChange={(side: 'BUY' | 'SELL', positionEffect: 'OPEN' | 'CLOSE') => {
              setForm((s) => ({
                ...s,
                side,
                contract: { ...s.contract, positionEffect } as ContractSearchInputValue,
              }));
            }}
          />
        )}

      {/* ── Phase 14 — Future details section ───────────────────────── */}
      {(form.contract as TradeTicketContract).asset_class === 'FUTURE' &&
        (form.contract as TradeTicketContract).futureContract != null && (
          <FutureDetailsSection
            contract={
              (form.contract as TradeTicketContract).futureContract as NonNullable<TradeTicketContract['futureContract']>
            }
          />
        )}

      {/* ── Phase 11a-D — AI context section ────────────────────────── */}
      {form.contract.symbol.trim() && (
        <TradeTicketAiSection
          symbol={form.contract.symbol.trim()}
          side={form.side}
          qty={Number.parseFloat(form.qty) || 0}
        />
      )}

      {/* ── Phase 10b.1 — position-sizing section ─────────────────────── */}
      <details
        open={sizingOpen}
        onToggle={(e) =>
          setSizingOpen((e.currentTarget as HTMLDetailsElement).open)
        }
        className="rounded-md border border-border p-3"
        data-testid="sizing-section"
      >
        <summary className="cursor-pointer text-sm font-medium">
          Position sizing
        </summary>
        <div className="mt-3 space-y-3">
          <div className="flex items-center gap-2">
            <label className="text-xs" htmlFor="sizing-method">
              Method:
            </label>
            <select
              id="sizing-method"
              value={sizingMethod}
              onChange={(e) => {
                setSizingMethodOverride(e.currentTarget.value as SizingMethod);
                setSizingPctOverride(null); // re-derive pct for the new method
              }}
              className="rounded-md border border-border bg-panel p-1 text-sm"
              data-testid="sizing-method-select"
            >
              <option value="fixed_fractional">Fixed-fractional</option>
              <option value="risk_per_trade">Fixed-risk-per-trade</option>
              <option value="vol_targeted">Vol-targeted</option>
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs" htmlFor="sizing-pct">
              {sizingMethod === 'vol_targeted' ? 'Target vol %' : 'Risk %'}:
            </label>
            <input
              id="sizing-pct"
              type="text"
              inputMode="decimal"
              value={sizingPct}
              onChange={(e) => setSizingPctOverride(e.currentTarget.value)}
              className="w-24 rounded-md border border-border bg-panel p-1 text-sm"
              data-testid="sizing-risk-pct"
            />
          </div>
          {sizing.loading ? (
            <div className="text-xs text-muted-foreground">Computing…</div>
          ) : null}
          {sizing.result ? (
            <div className="text-sm">
              <div>
                <span className="font-medium">Suggested qty:</span>{' '}
                <span data-testid="sizing-suggested-qty">
                  {sizing.result.suggested_qty}
                </span>{' '}
                <span className="text-xs text-muted-foreground">
                  ({sizing.result.base_currency_notional}{' '}
                  {sizing.result.breakdown.account_currency})
                </span>
              </div>
              <Button
                type="button"
                size="sm"
                className="mt-2"
                onClick={() => {
                  const suggested = sizing.result?.suggested_qty;
                  if (suggested) {
                    setForm((s) => ({ ...s, qty: suggested }));
                  }
                }}
                disabled={sizing.result.risk_verdict.final_verdict === 'block'}
                data-testid="sizing-use-button"
              >
                Use this size
              </Button>
            </div>
          ) : null}
          {(sizing.result?.risk_verdict.blockers?.length ?? 0) > 0 ? (
            <div
              className="rounded-md border border-destructive/60 bg-destructive/10 p-2 text-xs text-destructive"
              role="alert"
              aria-label="Risk gate blockers (sizing)"
            >
              <p className="font-semibold">
                Risk gate at suggestion time — BLOCK
              </p>
              <ul className="mt-1 list-inside list-disc">
                {(sizing.result?.risk_verdict.blockers ?? []).map((b) => (
                  <li key={`${b.check}:${b.code}`}>
                    {b.message}{' '}
                    <span className="font-mono opacity-70">({b.code})</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {(sizing.result?.risk_verdict.warnings?.length ?? 0) > 0 ? (
            <div
              className="rounded-md border border-warning/60 bg-warning/10 p-2 text-xs"
              role="alert"
              aria-label="Risk gate warnings (sizing)"
            >
              <p className="font-semibold">
                Risk gate at suggestion time — WARN
              </p>
              <ul className="mt-1 list-inside list-disc">
                {(sizing.result?.risk_verdict.warnings ?? []).map((w) => (
                  <li key={`${w.check}:${w.message}`}>{w.message}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {sizing.error ? (
            <div
              className="text-xs text-destructive"
              data-testid="sizing-error"
            >
              {sizing.error.message}
            </div>
          ) : null}
        </div>
      </details>

      <Button type="submit" disabled={!canPreview}>Preview</Button>
    </form>
  );
}

function PreviewStep({
  preview,
  attestedExtreme,
  setAttestedExtreme,
  acknowledgedRiskWarnings,
  setAcknowledgedRiskWarnings,
  placeOrderBlockers,
  confirmDisabled,
  inFlight,
  onBack,
  onConfirm,
}: {
  preview: PreviewResponse;
  attestedExtreme: boolean;
  setAttestedExtreme: (value: boolean) => void;
  acknowledgedRiskWarnings: boolean;
  setAcknowledgedRiskWarnings: (value: boolean) => void;
  placeOrderBlockers: readonly RiskBlocker[];
  confirmDisabled: boolean;
  inFlight: boolean;
  onBack: () => void;
  onConfirm: () => void;
}): React.JSX.Element {
  // Phase 10a E2: structured risk-gate verdict surfaces.
  const riskWarnings = preview.risk_warnings ?? [];
  // E7-fix (spec/quality H2): merge place_order 422 blockers in with
  // preview-time blockers so a freshly-flipped kill switch surfaces
  // identically regardless of which station detected it.
  const riskBlockers = [...(preview.risk_blockers ?? []), ...placeOrderBlockers];
  const hasRiskWarnings = riskWarnings.length > 0;
  const hasRiskBlockers = riskBlockers.length > 0;

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

      {hasRiskBlockers ? (
        <div
          className="rounded-md border border-destructive/60 bg-destructive/10 p-3 text-sm text-destructive"
          role="alert"
          aria-live="assertive"
          aria-atomic="true"
          aria-label="Risk gate blockers"
        >
          <p className="font-semibold">Order blocked by the risk gate.</p>
          <ul className="mt-2 list-inside list-disc">
            {riskBlockers.map((blocker) => (
              <li key={`${blocker.check}:${blocker.code}`}>
                <span className="font-medium">{blocker.message}</span>
                <span className="ml-2 font-mono text-xs opacity-70">({blocker.code})</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {hasRiskWarnings ? (
        // E7-fix (quality H5): WARN list is now visible even when BLOCK
        // is also set, so the operator sees every check the gate flagged.
        // The acknowledgement checkbox only matters when BLOCK is absent
        // (BLOCK supersedes WARN for the Confirm button gate).
        <div
          className="rounded-md border border-warning/60 bg-warning/10 p-3 text-sm"
          role="alert"
          aria-live="polite"
          aria-atomic="true"
          aria-label="Risk gate warnings"
        >
          <p className="font-semibold">Risk warnings — review before confirming.</p>
          <ul className="mt-2 list-inside list-disc">
            {riskWarnings.map((warning) => (
              <li key={`${warning.check}:${warning.message}`}>{warning.message}</li>
            ))}
          </ul>
          {!hasRiskBlockers ? (
            <label className="mt-3 flex items-center gap-2">
              <input
                type="checkbox"
                checked={acknowledgedRiskWarnings}
                onChange={(event) =>
                  setAcknowledgedRiskWarnings(event.currentTarget.checked)
                }
              />
              I understand these warnings
            </label>
          ) : null}
        </div>
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
        <Button
          type="button"
          onClick={onConfirm}
          disabled={confirmDisabled || hasRiskBlockers || (hasRiskWarnings && !acknowledgedRiskWarnings)}
        >
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

function firstSupportedCombo(
  capabilities: BrokerCapabilitiesResponse,
  currentOrderType: OrderType,
  currentTif: Tif,
): { orderType: SubmittableOrderType; tif: Tif } | null {
  const preferred = capabilities.combos.find((combo) => (
    combo.supported && combo.order_type === 'LIMIT' && combo.time_in_force === 'DAY'
  ));
  if (preferred !== undefined) {
    if (preferred.order_type === currentOrderType && preferred.time_in_force === currentTif) return null;
    return { orderType: 'LIMIT', tif: 'DAY' };
  }
  for (const orderType of ORDER_TYPES) {
    if (!isSubmittableOrderType(orderType)) continue;
    for (const tif of TIFS) {
      const supported = capabilities.combos.some((combo) => (
        combo.supported && combo.order_type === orderType && combo.time_in_force === tif
      ));
      if (!supported) continue;
      if (orderType === currentOrderType && tif === currentTif) return null;
      return { orderType, tif };
    }
  }
  return null;
}

function orderTypeDisabledReason(
  orderType: OrderType,
  tif: Tif,
  stopDisabled: boolean,
  capabilities: ReturnType<typeof useBrokerCapabilities>,
  capabilityError: boolean,
  brokerId: string | null,
): string | null {
  if (orderType === 'STOP' && stopDisabled) return 'Unavailable for HK warrants/CBBC';
  if (brokerId === null || capabilityError) return null;
  if (capabilities.isSupported(orderType, tif)) return null;
  return capabilities.notesFor(orderType, tif) ?? NOT_SUPPORTED;
}

function tifDisabledReason(
  orderType: OrderType,
  tif: Tif,
  capabilities: ReturnType<typeof useBrokerCapabilities>,
  capabilityError: boolean,
  brokerId: string | null,
): string | null {
  if (brokerId === null || capabilityError) return null;
  if (capabilities.isSupported(orderType, tif)) return null;
  return capabilities.notesFor(orderType, tif) ?? NOT_SUPPORTED;
}

function optionLabel(value: string, disabledReason: string | null): string {
  return disabledReason === null ? value : `${value} (${disabledReason})`;
}

function isSubmittableOrderType(value: string): value is SubmittableOrderType {
  return value === 'MARKET' || value === 'LIMIT' || value === 'STOP' || value === 'STOP_LIMIT';
}

function buildRequest(accountId: string, form: FormState): PreviewRequest | null {
  const conid = form.contract.conid.trim() || form.contract.symbol.trim();
  const qty = form.qty.trim();
  if (conid === '' || !positiveInteger(qty)) return null;
  if (!isSubmittableOrderType(form.orderType)) return null;
  if ((form.orderType === 'LIMIT' || form.orderType === 'STOP_LIMIT') && form.limitPrice.trim() === '') return null;
  if ((form.orderType === 'STOP' || form.orderType === 'STOP_LIMIT') && form.stopPrice.trim() === '') return null;
  return {
    account_id: accountId,
    conid,
    side: form.side,
    order_type: form.orderType,
    tif: form.tif,
    qty: qty as DecimalString,
    limit_price: form.orderType === 'LIMIT' || form.orderType === 'STOP_LIMIT' ? form.limitPrice.trim() as DecimalString : null,
    stop_price: form.orderType === 'STOP' || form.orderType === 'STOP_LIMIT' ? form.stopPrice.trim() as DecimalString : null,
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
