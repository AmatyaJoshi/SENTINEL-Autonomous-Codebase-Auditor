import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertOctagon, RotateCcw } from "lucide-react";

interface Props {
  children: ReactNode;
  fallback?: (error: Error, reset: () => void) => ReactNode;
}
interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[Sentinel] UI error", error, info.componentStack);
  }

  private reset = () => this.setState({ error: null });

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return (
      <div className="min-h-[60vh] flex items-center justify-center p-6">
        <div className="card p-6 max-w-md w-full text-center" role="alert">
          <div className="mx-auto h-12 w-12 rounded-2xl bg-rose-500/10 text-rose-500 flex items-center justify-center mb-3">
            <AlertOctagon className="h-6 w-6" />
          </div>
          <h1 className="text-lg font-semibold">Something went wrong</h1>
          <p className="text-sm muted mt-1 break-words">{error.message}</p>
          <div className="mt-4 flex justify-center gap-2">
            <button className="btn-outline" onClick={this.reset}>
              <RotateCcw className="h-4 w-4" /> Try again
            </button>
            <button className="btn-primary" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </div>
      </div>
    );
  }
}
