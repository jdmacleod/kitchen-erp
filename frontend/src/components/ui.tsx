import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, Ref } from "react";

/**
 * The keyboard focus ring. Tailwind 4's outline-none sets --tw-outline-style to
 * none and outline-2 draws with that variable, so the ring names its style
 * (outline-solid) or it computes to no outline at all.
 */
export const focusRing =
  "focus:outline-none focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:focus-visible:outline-blue-400";

const buttonVariants = {
  // Dark keeps blue-600: white on the remapped blue-500 is 3.7:1 and fails the 4.5:1 floor.
  primary:
    "bg-blue-600 text-white hover:bg-blue-700 disabled:bg-blue-600/50 dark:hover:bg-blue-700",
  secondary:
    "border border-neutral-300 bg-white text-neutral-900 hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100 dark:hover:bg-neutral-800",
  danger:
    "border border-red-300 bg-white text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:bg-neutral-900 dark:text-red-300 dark:hover:bg-red-950",
  // The one confirming button of a destructive action (remove a purchase, #74).
  dangerFill: "bg-red-700 text-white hover:bg-red-800 disabled:bg-red-700/50 dark:hover:bg-red-800",
  ghost:
    "text-neutral-700 hover:bg-neutral-200 disabled:opacity-50 dark:text-neutral-300 dark:hover:bg-neutral-800",
} as const;

/**
 * A text link that is a 44px touch target below lg (UI-4.2) and plain inline text
 * at lg and wider, where the row it sits in is already dense enough to scan. The
 * width floor is for a short name ("salt" is 27px wide); the text stays left.
 */
export const tapTarget =
  "inline-flex min-h-11 min-w-11 items-center lg:inline lg:min-h-0 lg:min-w-0";

/**
 * A section heading (08): Fraunces 20px, weight 600. Every page section, card,
 * drawer and sheet title uses this one string, so the headings cannot drift
 * apart again (issue 314). Labels, table headers and body text stay Inter.
 */
export const sectionHeading = "font-display text-xl font-semibold";

/** The primary button's look, for a link that acts as the page's primary action. */
export const primaryLinkClass = `inline-flex min-h-11 lg:min-h-10 items-center justify-center rounded-md px-3 text-sm font-medium ${buttonVariants.primary} ${focusRing}`;

/**
 * The secondary button's look, for a link. It sets its own text colour, so the
 * base rule that makes links herb (theme.css) cannot turn it into a primary action.
 */
export const secondaryLinkClass = `inline-flex min-h-11 lg:min-h-10 items-center justify-center rounded-md px-3 text-sm font-medium ${buttonVariants.secondary} ${focusRing}`;

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof buttonVariants;
  /** React 19 passes refs as props; the drawer focuses "Keep editing" through one. */
  ref?: Ref<HTMLButtonElement>;
};

/** A caller's own minimum height, which replaces the default rather than competing with it. */
const OWN_MIN_HEIGHT = /(^|\s)(lg:)?min-h-/;
// The same holds for display: a caller's unprefixed "hidden" (as in "hidden
// lg:inline-flex") lost to the default inline-flex, so a desktop-only button
// showed on phones. A caller that sets its own display gets no default.
const OWN_DISPLAY = /(^|\s)(hidden|block|inline-block|inline|flex|grid|contents)(\s|$)/;

export function Button({
  variant = "primary",
  className = "",
  type = "button",
  ...rest
}: ButtonProps) {
  // Two min-h utilities on one element are settled by stylesheet order, not by
  // which class came last: "min-h-11 lg:min-h-8" from a caller lost to the
  // default lg:min-h-10, so every compact desktop button was 40px (QA 2026-10-13).
  const height = OWN_MIN_HEIGHT.test(className) ? "" : "min-h-11 lg:min-h-10";
  const display = OWN_DISPLAY.test(className) ? "" : "inline-flex";
  return (
    <button
      type={type}
      className={`${display} ${height} items-center justify-center rounded-md px-3 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed ${buttonVariants[variant]} ${focusRing} ${className}`}
      {...rest}
    />
  );
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  id: string;
  label: string;
  hint?: string;
};

export function Field({ id, label, hint, className = "", ...rest }: FieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className={`flex min-w-0 flex-col gap-1 ${className}`}>
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        aria-describedby={hintId}
        className={`min-w-0 min-h-11 lg:min-h-10 rounded-md border border-neutral-300 bg-white px-3 py-2 text-base text-neutral-900 placeholder:text-neutral-400 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100 ${focusRing}`}
        {...rest}
      />
      {hint ? (
        <p id={hintId} className="text-xs text-neutral-600 dark:text-neutral-400">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

type AlertProps = {
  tone: "error" | "success" | "info" | "warn";
  children: ReactNode;
  className?: string;
};

export const alertTones = {
  error:
    "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200",
  success:
    "border-green-300 bg-green-50 text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-200",
  // Blue means "you can act on this" (spec 08), so information is neutral.
  info: "border-neutral-300 bg-neutral-100 text-neutral-800 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200",
  // Squash: something a person should look at before trusting it (spec 08).
  warn: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200",
} as const;

export function Alert({ tone, children, className = "" }: AlertProps) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`rounded-md border px-3 py-2 text-sm ${alertTones[tone]} ${className}`}
    >
      {children}
    </div>
  );
}

/**
 * The page header (docs/spec/10, Desktop page pattern): the Fraunces title, a
 * one-line description, and the page's actions, the primary one last.
 */
export function PageHeader({
  title,
  tag,
  description,
  children,
}: {
  title: string;
  tag?: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        {/* `tag` sits after the title: the kitchen tag of a scoped page (10, The tag). */}
        <h1 className="flex flex-wrap items-center gap-x-3 gap-y-1 text-3xl leading-tight md:text-[2.5rem]">
          <span>{title}</span>
          {tag}
        </h1>
        {description ? (
          <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">{description}</p>
        ) : null}
      </div>
      {children}
    </header>
  );
}

/**
 * `action` is for an empty state the user cannot resolve on the page they are on.
 * Most callers do not need it: their create form is directly above the list, so
 * "Add one above" is the whole instruction. Pass it when the way out is elsewhere.
 */
export function EmptyState({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className="rounded-lg border border-dashed border-neutral-300 bg-white px-6 py-12 text-center dark:border-neutral-700 dark:bg-neutral-900"
    >
      <p className="text-base font-medium">{title}</p>
      {children ? (
        <div className="mt-2 text-sm text-neutral-600 dark:text-neutral-400">{children}</div>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </section>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section
      className={`rounded-lg border border-neutral-200 bg-white p-4 shadow-card dark:border-neutral-800 dark:bg-neutral-900 ${className}`}
    >
      {children}
    </section>
  );
}

export function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh items-center justify-center p-6">
      <div className="w-full max-w-sm">{children}</div>
    </div>
  );
}
