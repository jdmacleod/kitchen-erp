import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { useCurrentKitchen } from "../../lib/kitchen";
import { focusRing } from "../ui";

const chip = `flex min-h-11 w-full items-center justify-between gap-2 rounded-md bg-neutral-200 px-3 text-sm font-medium text-neutral-800 hover:bg-neutral-300/70 lg:min-h-9 dark:bg-neutral-800 dark:text-neutral-200 dark:hover:bg-neutral-700 ${focusRing}`;

function Chevron({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className={`size-4 shrink-0 ${open ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

function Check() {
  return (
    <svg viewBox="0 0 24 24" className="size-4 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m5 12 5 5 9-10" />
    </svg>
  );
}

/**
 * The sidebar's kitchen switcher (17, 4A; 10, The kitchen switcher; UI-2.4): a
 * neutral chip naming the current kitchen, a menu button whose menu lists every
 * kitchen with a check beside the current one. Choosing one writes the server
 * setting (UI-2.5). Shown only with two or more kitchens; with one, nothing is
 * here. This is the one mounted caller that writes a resolved fallback back.
 */
export function KitchenSwitcher() {
  const kitchen = useCurrentKitchen({ writeBack: true });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const ids = useId();
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const items = kitchen.kitchens;

  // Focus follows the active item; a click outside closes the menu.
  useEffect(() => {
    if (!open) return;
    menu.current?.querySelectorAll<HTMLElement>("[role='menuitemradio']")[active]?.focus();
  }, [open, active]);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menu.current?.contains(target) && !button.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  if (!kitchen.built || items.length < 2 || !kitchen.current) return null;

  const show = () => {
    setActive(Math.max(0, items.findIndex((k) => k.id === kitchen.currentId)));
    setOpen(true);
  };
  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  };
  const choose = (id: string) => {
    kitchen.switchTo(id);
    close(true);
  };
  const onMenuKey = (event: KeyboardEvent<HTMLDivElement>) => {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setActive((i) => (i + 1) % items.length);
        break;
      case "ArrowUp":
        event.preventDefault();
        setActive((i) => (i - 1 + items.length) % items.length);
        break;
      case "Home":
        event.preventDefault();
        setActive(0);
        break;
      case "End":
        event.preventDefault();
        setActive(items.length - 1);
        break;
      case "Escape":
        event.preventDefault();
        close(true);
        break;
      case "Tab":
        close(false);
        break;
      default:
    }
  };

  return (
    <div className="relative px-1" data-testid="kitchen-switcher">
      <button
        ref={button}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? `${ids}-menu` : undefined}
        disabled={kitchen.switching}
        className={chip}
        onClick={() => (open ? close(false) : show())}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            show();
          }
        }}
      >
        <span className="min-w-0 truncate">
          <span className="sr-only">Kitchen:</span>{" "}
          {kitchen.current.name}
        </span>
        <Chevron open={open} />
      </button>
      {open ? (
        <div
          ref={menu}
          id={`${ids}-menu`}
          role="menu"
          aria-label="Switch kitchen"
          onKeyDown={onMenuKey}
          className="absolute inset-x-1 bottom-full z-20 mb-1 rounded-lg border border-neutral-200 bg-white p-1 shadow-md dark:border-neutral-700 dark:bg-neutral-900"
        >
          {items.map((k, i) => {
            const current = k.id === kitchen.currentId;
            return (
              <button
                key={k.id}
                type="button"
                role="menuitemradio"
                aria-checked={current}
                tabIndex={i === active ? 0 : -1}
                onMouseMove={() => setActive(i)}
                onClick={() => choose(k.id)}
                className={`flex min-h-11 w-full items-center justify-between gap-2 rounded-md px-3 text-left text-sm lg:min-h-9 ${focusRing} ${
                  current ? "font-semibold text-neutral-900 dark:text-neutral-100" : "text-neutral-800 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800"
                }`}
              >
                <span className="min-w-0 truncate">{current ? k.name : `Switch to ${k.name}`}</span>
                {current ? <Check /> : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The More sheet's switcher (09, Phone and tablet; 10, More sheet; UI-8.10): the
 * same chip as the sheet's first row, full width at 44px, opening a radio list
 * of the kitchens in place. Shown only with two or more kitchens.
 */
export function KitchenSwitcherRow() {
  const kitchen = useCurrentKitchen();
  const [open, setOpen] = useState(false);
  const ids = useId();
  if (!kitchen.built || kitchen.kitchens.length < 2 || !kitchen.current) return null;
  return (
    <section aria-labelledby={`${ids}-label`} className="mb-3" data-testid="kitchen-switcher-row">
      <h3 id={`${ids}-label`} className="sr-only">
        Kitchen
      </h3>
      <button type="button" aria-expanded={open} aria-controls={`${ids}-list`} disabled={kitchen.switching} className={chip} onClick={() => setOpen((o) => !o)}>
        <span className="min-w-0 truncate">
          <span className="sr-only">Kitchen:</span>{" "}
          {kitchen.current.name}
        </span>
        <Chevron open={open} />
      </button>
      {open ? (
        <fieldset id={`${ids}-list`} className="mt-1 flex flex-col">
          <legend className="sr-only">Switch kitchen</legend>
          {kitchen.kitchens.map((k) => (
            <label key={k.id} className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-md px-3 text-sm hover:bg-neutral-100 dark:hover:bg-neutral-800`}>
              <input
                type="radio"
                name={`${ids}-kitchen`}
                value={k.id}
                checked={k.id === kitchen.currentId}
                className={`size-4 accent-neutral-800 dark:accent-neutral-200 ${focusRing}`}
                onChange={() => {
                  kitchen.switchTo(k.id);
                  setOpen(false);
                }}
              />
              {k.name}
            </label>
          ))}
        </fieldset>
      ) : null}
    </section>
  );
}
