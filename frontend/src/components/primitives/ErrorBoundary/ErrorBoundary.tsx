import * as React from 'react';

interface ErrorBoundaryProps {
  fallback?: React.ReactNode | ((error: Error, retry: () => void) => React.ReactNode);
  onError?: (error: Error, info: React.ErrorInfo) => void;
  children: React.ReactNode;
}
interface ErrorBoundaryState { error: Error | null; }

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };
  static getDerivedStateFromError(error: Error): ErrorBoundaryState { return { error }; }
  componentDidCatch(error: Error, info: React.ErrorInfo): void { this.props.onError?.(error, info); }
  private retry = (): void => this.setState({ error: null });
  render(): React.ReactNode {
    if (!this.state.error) return this.props.children;
    if (typeof this.props.fallback === 'function') return this.props.fallback(this.state.error, this.retry);
    return this.props.fallback ?? (
      <div role="alert" className="p-8">
        <h2 className="text-lg font-semibold text-fg">Something went wrong</h2>
        <pre className="mt-2 text-sm text-fg-muted whitespace-pre-wrap">{this.state.error.message}</pre>
        <button
          type="button"
          onClick={this.retry}
          className="mt-4 rounded-md border border-border bg-panel px-3 py-1.5 text-sm text-fg hover:bg-elevated"
        >
          Retry
        </button>
      </div>
    );
  }
}
