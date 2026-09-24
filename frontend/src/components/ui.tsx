import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

export const focusRing =
  "focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:focus-visible:outline-blue-400";

const buttonVariants = {
  primary:
    "bg-blue-600 text-white hover:bg-blue-700 disabled:bg-blue-600/50 dark:bg-blue-500 dark:hover:bg-blue-400",
  secondary:
    "border border-neutral-300 bg-white text-neutral-900 hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100 dark:hover:bg-neutral-800",
  danger:
    "border border-red-300 bg-white text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:bg-neutral-900 dark:text-red-300 dark:hover:bg-red-950",
  ghost:
    "text-neutral-700 hover:bg-neutral-200 disabled:opacity-50 dark:text-neutral-300 dark:hover:bg-neutral-800",
} as const;

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof buttonVariants;
};

export function Button({ variant = "primary", className = "", type = "button", ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={`inline-flex min-h-10 items-center justify-center rounded-md px-3 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed ${buttonVariants[variant]} ${focusRing} ${className}`}
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
    <div className={`flex flex-col gap-1 ${className}`}>
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        aria-describedby={hintId}
        className={`min-h-10 rounded-md border border-neutral-300 bg-white px-3 py-2 text-base text-neutral-900 placeholder:text-neutral-400 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100 ${focusRing}`}
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
  tone: "error" | "success" | "info";
  children: ReactNode;
  className?: string;
};

const alertTones = {
  error: "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200",
  success:
    "border-green-300 bg-green-50 text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-200",
  info: "border-blue-300 bg-blue-50 text-blue-800 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200",
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

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
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
      className={`rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900 ${className}`}
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
