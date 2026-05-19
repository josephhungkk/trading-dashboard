import { useState, useRef } from 'react';
import { getRouteApi } from '@tanstack/react-router';
import { BacktestConfigForm } from '../components/BacktestConfigForm';
import { BacktestProgressBar } from '../components/BacktestProgressBar';
import { BacktestReportKpis } from '../components/BacktestReportKpis';
import { BacktestTradeTable } from '../components/BacktestTradeTable';
import { useBacktestStream } from '../hooks/useBacktestStream';
import { submitBacktest, cancelBacktest } from '../../../services/backtests/api';
import type { BacktestReport } from '../../../services/backtests/types';
import type { BacktestSubmitConfig } from '../../../services/backtests/types';

type PageState = 'configure' | 'running' | 'done' | 'failed';

const routeApi = getRouteApi('/bots/$botId/backtest');

export function BacktestPage() {
  const { botId } = routeApi.useParams();
  const [state, setState] = useState<PageState>('configure');
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState({ pct: 0, tradesSoFar: 0, currentBarTs: '' });
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const jobIdRef = useRef<string | null>(null);

  useBacktestStream({
    botId,
    jobId: state === 'running' && jobId ? jobId : null,
    onProgress: (pct, tradesSoFar, currentBarTs) =>
      setProgress({ pct, tradesSoFar, currentBarTs }),
    onDone: (r) => {
      setReport(r);
      setState('done');
    },
    onFailed: (msg) => {
      setErrorMsg(msg);
      setState('failed');
    },
  });

  async function handleSubmit(config: BacktestSubmitConfig) {
    try {
      const job = await submitBacktest(botId, config);
      jobIdRef.current = job.id;
      setJobId(job.id);
      setProgress({ pct: 0, tradesSoFar: 0, currentBarTs: '' });
      setState('running');
    } catch (err) {
      setErrorMsg(String(err));
      setState('failed');
    }
  }

  async function handleCancel() {
    if (jobId) {
      try {
        await cancelBacktest(botId, jobId);
      } catch {
        // best-effort
      }
    }
    setJobId(null);
    jobIdRef.current = null;
    setState('configure');
  }

  function handleNewBacktest() {
    setJobId(null);
    jobIdRef.current = null;
    setReport(null);
    setErrorMsg(null);
    setState('configure');
  }

  if (state === 'configure') {
    return <BacktestConfigForm botId={botId} onSubmit={handleSubmit} />;
  }

  if (state === 'running') {
    return (
      <BacktestProgressBar
        pct={progress.pct}
        tradesSoFar={progress.tradesSoFar}
        currentBarTs={progress.currentBarTs}
        onCancel={handleCancel}
      />
    );
  }

  if (state === 'done' && report) {
    return (
      <div>
        <BacktestReportKpis report={report} />
        <BacktestTradeTable trades={report.trades} />
        <button onClick={handleNewBacktest}>New Backtest</button>
      </div>
    );
  }

  return (
    <div role="alert">
      <p>{errorMsg ?? 'Backtest failed'}</p>
      <button onClick={handleNewBacktest}>New Backtest</button>
    </div>
  );
}
