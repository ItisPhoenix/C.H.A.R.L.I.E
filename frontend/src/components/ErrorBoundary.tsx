"use client";

import React from "react";

interface ErrorBoundaryProps {
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    console.error("ErrorBoundary caught:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex h-full w-full items-center justify-center bg-[var(--color-canvas)] p-8">
          <div className="max-w-md rounded-xl border border-[var(--color-glass-border)] bg-[var(--color-glass-bg)] p-8 text-center">
            <div className="mb-4 text-4xl">!</div>
            <h2 className="mb-2 text-lg font-semibold text-[var(--color-text-primary)]">
              Something went wrong
            </h2>
            <p className="mb-4 text-sm text-[var(--color-text-secondary)]">
              {this.state.error?.message || "An unexpected error occurred."}
            </p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="rounded-lg bg-[var(--color-aurora)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
            >
              Try Again
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
