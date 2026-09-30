import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, alertTones } from "./ui";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Keeps one page's crash from blanking the whole app. Without it, an error
 * thrown while rendering unmounts everything and leaves a white screen (a tab
 * still running the previous build after an update followed a new inbox link
 * to a route it didn't know, and rendered the new API's answer as the wrong
 * page). The navigation stays; the page says what happened and offers Reload,
 * which loads the current build. AppShell keys it by path, so moving to
 * another page starts clean.
 */
export class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("page failed to render", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className={`flex flex-col gap-3 rounded-2xl border px-4 py-4 text-sm ${alertTones.error}`}>
        <p className="font-medium">This page couldn&apos;t be shown.</p>
        <p>If Kitchen ERP was just updated, reloading loads the new version. If it happens again after a reload, the page has a bug.</p>
        <div>
          <Button variant="secondary" onClick={() => window.location.reload()}>
            Reload
          </Button>
        </div>
      </div>
    );
  }
}
