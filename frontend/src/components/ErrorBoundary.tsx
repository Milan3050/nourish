import { Component, type ReactNode } from 'react'

/** Keeps a crash in one page from blanking the whole app (and the chat running beside it). */
export class ErrorBoundary extends Component<{ children: ReactNode; label: string }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div role="alert" className="rounded-2xl border border-chilli/40 bg-chilli/10 p-4 text-sm">
        <p className="m-0 font-medium">The {this.props.label} page hit an error.</p>
        <p className="m-0 text-xs text-dim mt-1">{this.state.error.message}</p>
        <button className="mt-2 text-xs text-turmeric underline" onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    )
  }
}
