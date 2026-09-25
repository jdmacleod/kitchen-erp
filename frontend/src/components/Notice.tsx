import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate, type NavigateOptions, type To } from "react-router";
import { alertTones, focusRing } from "./ui";

/**
 * The Notice (docs/spec/08, D18): the one shared confirmation. An inline, polite
 * live region at the top of the main content, never a floating toast.
 *
 * - One at a time: showing a notice replaces the one on screen.
 * - It stays until dismissed or until the user navigates, unless it sets
 *   `timeoutMs` (the shelf-price "Saved" notice clears after 3 s, G4).
 * - It can ride along a navigation in router state. The destination consumes it
 *   and replaces its own history entry, so Back and reload do not replay it.
 */
export interface NoticeData {
  tone: "success" | "info" | "error";
  message: string;
  action?: { label: string; to: To };
  timeoutMs?: number;
  /** Move focus to the action once shown, e.g. "Open it" after a save (G10). */
  focusAction?: boolean;
}

interface Shown extends NoticeData {
  id: string;
  pathname: string;
}

interface NoticeApi {
  /** Show a notice, replacing any other; returns its id for `update`. */
  show: (notice: NoticeData) => string;
  /**
   * Change a notice that is still showing, e.g. to add an action once it is known.
   * A notice that was dismissed or replaced in the meantime stays gone.
   */
  update: (id: string, patch: Partial<NoticeData>) => void;
  dismiss: () => void;
}

const NoticeContext = createContext<NoticeApi>({ show: () => "", update: () => {}, dismiss: () => {} });

export function useNotice(): NoticeApi {
  return useContext(NoticeContext);
}

/** Navigate and show a notice on arrival. */
export function useNavigateWithNotice() {
  const navigate = useNavigate();
  return useCallback(
    (to: To, notice: NoticeData, options: NavigateOptions = {}) =>
      navigate(to, { ...options, state: { ...(options.state as object | undefined), notice } }),
    [navigate],
  );
}

function carried(state: unknown): { notice: NoticeData | null; rest: Record<string, unknown> | null } {
  if (typeof state !== "object" || state === null || !("notice" in state)) return { notice: null, rest: null };
  const { notice, ...rest } = state as { notice: NoticeData } & Record<string, unknown>;
  return { notice, rest: Object.keys(rest).length > 0 ? rest : null };
}

let nextId = 0;

export function NoticeProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [shown, setShown] = useState<Shown | null>(null);

  const show = useCallback(
    (notice: NoticeData) => {
      const id = `shown-${++nextId}`;
      setShown({ ...notice, id, pathname: location.pathname });
      return id;
    },
    [location.pathname],
  );
  const update = useCallback(
    (id: string, patch: Partial<NoticeData>) => setShown((current) => (current?.id === id ? { ...current, ...patch } : current)),
    [],
  );
  const dismiss = useCallback(() => setShown(null), []);

  // A notice carried in router state is picked up once per history entry, while
  // rendering, so it is on screen in the same paint as the page it belongs to.
  const [pickedUp, setPickedUp] = useState<string | null>(null);
  const incoming = carried(location.state).notice;
  if (incoming && pickedUp !== location.key) {
    setPickedUp(location.key);
    setShown({ ...incoming, id: `carried-${location.key}`, pathname: location.pathname });
  } else if (shown && shown.pathname !== location.pathname) {
    // Navigating away ends a notice for good; coming back does not revive it.
    setShown(null);
  }
  // Then it is dropped from the entry, so Back and reload do not bring it back.
  useEffect(() => {
    const { notice, rest } = carried(location.state);
    if (!notice) return;
    navigate(`${location.pathname}${location.search}${location.hash}`, { replace: true, state: rest });
  }, [location, navigate]);

  // Auto-clear, for the notices that ask for it.
  useEffect(() => {
    if (!shown?.timeoutMs) return;
    const timer = setTimeout(() => setShown((current) => (current?.id === shown.id ? null : current)), shown.timeoutMs);
    return () => clearTimeout(timer);
  }, [shown]);

  const api = useMemo(() => ({ show, update, dismiss }), [show, update, dismiss]);
  // A notice belongs to the page it was shown on.
  const visible = shown && shown.pathname === location.pathname ? shown : null;

  return (
    <NoticeContext.Provider value={api}>
      <NoticeRegion notice={visible} onDismiss={dismiss} />
      {children}
    </NoticeContext.Provider>
  );
}

function NoticeRegion({ notice, onDismiss }: { notice: Shown | null; onDismiss: () => void }) {
  const action = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    if (notice?.focusAction) action.current?.focus();
  }, [notice]);

  // The polite live region is always in the page, so a notice that appears in it
  // is announced; the notice itself is the status, so an empty region is not one.
  return (
    <div aria-live="polite" aria-atomic="true" data-testid="notice-region">
      {notice ? (
        <div
          role="status"
          data-testid="notice"
          data-tone={notice.tone}
          className={`mb-6 flex flex-wrap items-center justify-between gap-2 rounded-2xl border px-4 py-3 text-sm ${alertTones[notice.tone]}`}
        >
          <span className="flex flex-wrap items-center gap-x-2">
            <span>{notice.message}</span>
            {notice.action ? (
              <Link ref={action} to={notice.action.to} className={`rounded font-medium underline ${focusRing}`}>
                {notice.action.label}
              </Link>
            ) : null}
          </span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={onDismiss}
            className={`-my-1 inline-flex size-11 items-center justify-center rounded-md hover:bg-black/5 lg:size-8 dark:hover:bg-white/10 ${focusRing}`}
          >
            <svg viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
      ) : null}
    </div>
  );
}
