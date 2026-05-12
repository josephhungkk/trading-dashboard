import * as React from 'react';
import { useCallback, useState } from 'react';

import { useNavigate, useSearch } from '@tanstack/react-router';

import { AssetClassDrillDrawer } from '@/features/portfolio/AssetClassDrillDrawer';
import { AssetClassExposureList } from '@/features/portfolio/AssetClassExposureList';
import { PerAccountTable } from '@/features/portfolio/PerAccountTable';
import { RollupCurveChart } from '@/features/portfolio/RollupCurveChart';
import { RollupKpiBar } from '@/features/portfolio/RollupKpiBar';
import { Route } from '@/routes/portfolio.rollup';
import { isPortfolioApiError } from '@/services/portfolio/api';
import type { BaseCurrency, CurveWindow } from '@/services/portfolio/types';
import { useRollupCurve } from '@/services/portfolio/useRollupCurve';
import { useRollupLive } from '@/services/portfolio/useRollupLive';
import { usePortfolioStore } from '@/stores/global/portfolio';

export function RollupPage(): React.JSX.Element {
  const base = usePortfolioStore((s) => s.portfolioRollupBase);
  const setBase = usePortfolioStore((s) => s.setBase);

  const search = useSearch({ from: Route.id });
  const navigate = useNavigate({ from: Route.id });
  const setWindow = (w: CurveWindow): void => {
    void navigate({ search: (prev) => ({ ...prev, window: w }) });
  };

  const [drillAssetClass, setDrillAssetClass] = useState<string | null>(null);
  const closeDrill = useCallback(() => setDrillAssetClass(null), []);

  const live = useRollupLive(base);
  const curve = useRollupCurve(base, search.window);

  if (live.isLoading) {
    return (
      <div className="p-6 text-sm text-muted-foreground" data-testid="rollup-loading">
        Loading rollup…
      </div>
    );
  }
  if (live.error || !live.data) {
    // Reviewer MED: distinguish 503 fx_rate_unavailable from generic
    // failures — softer "FX rates unavailable" banner (operator can still
    // see the page shell once FX returns), full failure for other errors.
    const isFxUnavailable
      = live.error
      && isPortfolioApiError(live.error)
      && live.error.status === 503;
    if (isFxUnavailable && !live.data) {
      return (
        <div
          className="m-6 rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900"
          data-testid="rollup-fx-unavailable"
        >
          FX rates unavailable — retrying. The rollup will appear when at
          least one account&apos;s currency can be priced in {base}.
        </div>
      );
    }
    return (
      <div className="p-6 text-sm text-red-600" data-testid="rollup-error">
        Failed to load rollup: {live.error?.message ?? 'unknown error'}
      </div>
    );
  }

  return (
    <div
      className="flex flex-col gap-4 p-4 md:p-6"
      data-testid="rollup-page"
    >
      <RollupKpiBar
        data={live.data}
        base={base}
        onBaseChange={(b: BaseCurrency) => setBase(b)}
        wsConnected={live.wsConnected}
      />
      <RollupCurveChart
        data={curve.data}
        window={search.window}
        onWindowChange={setWindow}
        loading={curve.isLoading}
      />
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <PerAccountTable accounts={live.data.accounts} />
        <AssetClassExposureList
          exposures={live.data.exposure_by_asset_class}
          onDrill={setDrillAssetClass}
        />
      </div>
      <AssetClassDrillDrawer
        assetClass={drillAssetClass}
        base={base}
        onClose={closeDrill}
      />
    </div>
  );
}
