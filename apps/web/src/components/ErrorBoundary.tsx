import { Component, type ErrorInfo, type ReactNode } from "react";

export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null; key?: string }> {
  state: { error: Error | null; key?: string } = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  static getDerivedStateFromProps(p: { resetKey?: string }, s: { error: Error | null; key?: string }) { return p.resetKey !== s.key ? { error: null, key: p.resetKey } : null; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("UI error", error, info.componentStack); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="mx-auto max-w-md rounded-xl border border-line bg-badsoft p-6 text-bad">
        <p className="font-semibold">This page hit an unexpected problem.</p>
        <p className="mt-1 text-sm">Your data is safe. Try again, or go back to the overview.</p>
        <button className="mt-3 underline" onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    );
  }
}
