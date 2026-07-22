import { Component, type ReactNode } from "react";

interface Props {
  label: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** One component failing must not blank the whole app. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(`[${this.props.label}]`, error);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="status-badge status-error" role="alert">
          {this.props.label} failed: {this.state.error.message}
        </div>
      );
    }
    return this.props.children;
  }
}
