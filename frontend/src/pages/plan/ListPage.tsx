import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import {
  itemName,
  listKeys,
  useArchivedLists,
  useArchiveList,
  useShoppingList,
  type ShoppingItem,
  type ShoppingList,
} from "../../api/shoppingList";
import { Badge, Disclosure } from "../../components/catalog/fields";
import { MoreActionsDialog, type MoreAction } from "../../components/MoreActionsDialog";
import { useNotice } from "../../components/Notice";
import { AddFromRecipeDrawer, fromRecipeSentence } from "../../components/plan/AddFromRecipeDrawer";
import { AddItemRow } from "../../components/plan/AddItemRow";
import { KitchenTag } from "../../components/plan/KitchenTag";
import { ListItemRow } from "../../components/plan/ListItemRow";
import {
  Alert,
  Button,
  EmptyState,
  focusRing,
  PageHeader,
  secondaryLinkClass,
  sectionHeading,
} from "../../components/ui";
import { formatDate } from "../../lib/format";
import { useCurrentKitchen } from "../../lib/kitchen";
import { LG_QUERY, useMediaQuery } from "../../lib/useMediaQuery";
import { usePageTitle } from "../../lib/usePageTitle";

const muted = "text-neutral-600 dark:text-neutral-400";
const DESCRIPTION = "What this kitchen needs next. Anyone can add, claim and check.";

/**
 * The shopping list (`/plan/list`; 10, Plan: Shopping list; UI-8.2–8.6, 8.11).
 * One open list per kitchen is the page; an archived one is read-only at
 * `?list_id=`. The kitchen comes from `lib/kitchen.ts` and is sent on every
 * call, never inferred by the server (09).
 */
export function ListPage() {
  const kitchen = useCurrentKitchen();
  const [params] = useSearchParams();
  const listId = params.get("list_id");
  const kitchenName = kitchen.current?.name ?? null;
  usePageTitle(kitchenName ? `${kitchenName} list` : "List");

  if (!kitchen.ready) {
    return (
      <>
        <PageHeader title="List" description={DESCRIPTION} />
        <Skeleton />
      </>
    );
  }
  if (!kitchen.currentId) {
    return (
      <>
        <PageHeader title="List" description={DESCRIPTION} />
        <EmptyState
          title="No kitchen yet"
          action={
            <Link to="/settings/kitchens" className={secondaryLinkClass}>
              Settings → Kitchens
            </Link>
          }
        >
          A list belongs to a kitchen. Add one under Settings → Kitchens, and the list opens here.
        </EmptyState>
      </>
    );
  }
  return (
    <ListView
      key={`${kitchen.currentId}:${listId ?? ""}`}
      homeBaseId={kitchen.currentId}
      kitchenName={kitchenName ?? "Kitchen"}
      listId={listId}
    />
  );
}

function RetryAlert({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Alert tone="error">
      <span className="flex flex-wrap items-center justify-between gap-2">
        <span>{message}</span>
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      </span>
    </Alert>
  );
}

function Skeleton() {
  return (
    <div
      role="status"
      aria-label="Loading the list"
      className="flex flex-col gap-3"
      data-testid="list-skeleton"
    >
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center gap-3">
          <span className="size-7 rounded-full bg-neutral-200 dark:bg-neutral-800" />
          <span className="h-4 w-40 rounded-sm bg-neutral-200 dark:bg-neutral-800" />
        </div>
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}

function ListView({
  homeBaseId,
  kitchenName,
  listId,
}: {
  homeBaseId: string;
  kitchenName: string;
  listId: string | null;
}) {
  const list = useShoppingList(homeBaseId, listId);
  const listKey = listKeys.list(homeBaseId, listId);
  const notice = useNotice();
  const wide = useMediaQuery(LG_QUERY);
  const addInput = useRef<HTMLInputElement | null>(null);
  const [fromRecipe, setFromRecipe] = useState(false);
  const [freshAsk, setFreshAsk] = useState(false);
  const [more, setMore] = useState(false);
  const [announced, setAnnounced] = useState("");
  const name = list.data?.home_base_name ?? kitchenName;
  const archived = list.data?.status === "archived";
  const title = archived
    ? `${name} list · archived ${formatDate(list.data?.archived_at)}`
    : `${name} list`;

  const onError = (e: unknown) => notice.show({ tone: "error", message: errorMessage(e) });
  const onNotice = (message: string) => notice.show({ tone: "success", message });
  const onToggled = (item: ShoppingItem, checked: boolean) => {
    const left = (list.data?.unchecked.length ?? 0) + (checked ? -1 : 1);
    setAnnounced(
      `${checked ? "Checked" : "Unchecked"} ${itemName(item)}. ${Math.max(0, left)} to get.`,
    );
  };

  const actions: MoreAction[] = archived
    ? []
    : [
        { label: "Add from recipe", open: () => setFromRecipe(true) },
        { label: "Start a fresh list", open: () => setFreshAsk(true), disabled: freshAsk },
      ];

  return (
    <>
      <div className="max-lg:sticky max-lg:top-0 max-lg:z-10 max-lg:-mx-4 max-lg:bg-neutral-50 max-lg:px-4 max-lg:pt-2 dark:max-lg:bg-neutral-950">
        <PageHeader
          title={title}
          tag={
            <span className="flex flex-wrap items-center gap-2">
              <KitchenTag name={name} />
              {archived ? <Badge>Archived</Badge> : null}
            </span>
          }
          description={DESCRIPTION}
        >
          {archived ? (
            <Link to="/plan/list" className={secondaryLinkClass}>
              Back to the current list
            </Link>
          ) : (
            <div className="flex flex-wrap gap-2">
              {wide ? (
                actions.map((a) => (
                  <Button key={a.label} variant="secondary" disabled={a.disabled} onClick={a.open}>
                    {a.label}
                  </Button>
                ))
              ) : (
                <Button variant="secondary" aria-haspopup="dialog" onClick={() => setMore(true)}>
                  More actions
                </Button>
              )}
              <Button onClick={() => addInput.current?.focus()}>Add item</Button>
            </div>
          )}
        </PageHeader>
      </div>

      {more ? (
        <MoreActionsDialog
          actions={actions.map((a) => ({
            ...a,
            open: () => {
              setMore(false);
              a.open();
            },
          }))}
          onClose={() => setMore(false)}
        />
      ) : null}

      <p aria-live="polite" className="sr-only" data-testid="list-announcement">
        {announced}
      </p>

      {freshAsk && list.data ? (
        <FreshListConfirm
          homeBaseId={homeBaseId}
          list={list.data}
          onClose={() => setFreshAsk(false)}
        />
      ) : null}

      {archived ? (
        <Alert tone="info" className="mb-4">
          This list was archived {formatDate(list.data?.archived_at)}. It is kept to read; nothing
          on it can change.
        </Alert>
      ) : null}

      {list.isPending ? (
        <Skeleton />
      ) : list.isError ? (
        <RetryAlert
          message={`Couldn't load the list: ${errorMessage(list.error)}`}
          onRetry={() => void list.refetch()}
        />
      ) : (
        <ListBody
          list={list.data}
          listKey={listKey}
          homeBaseId={homeBaseId}
          archived={archived}
          addInput={addInput}
          onToggled={onToggled}
          onError={onError}
          onNotice={onNotice}
        />
      )}

      {archived ? null : <PreviousLists homeBaseId={homeBaseId} />}

      {fromRecipe ? (
        <AddFromRecipeDrawer
          homeBaseId={homeBaseId}
          onClose={() => setFromRecipe(false)}
          onDone={(out, recipe) => {
            setFromRecipe(false);
            notice.show({
              tone: "success",
              message: fromRecipeSentence(out, recipe.title),
              action:
                out.skipped.length > 0
                  ? { label: "Resolve them", to: "/cook/recipes/resolve" }
                  : undefined,
            });
          }}
        />
      ) : null}
    </>
  );
}

interface ListBodyProps {
  list: ShoppingList;
  listKey: ReturnType<typeof listKeys.list>;
  homeBaseId: string;
  archived: boolean;
  addInput: React.RefObject<HTMLInputElement | null>;
  onToggled: (item: ShoppingItem, checked: boolean) => void;
  onError: (e: unknown) => void;
  onNotice: (message: string) => void;
}

function ListBody({
  list,
  listKey,
  homeBaseId,
  archived,
  addInput,
  onToggled,
  onError,
  onNotice,
}: ListBodyProps) {
  const empty = list.unchecked.length === 0 && list.checked.length === 0;
  const rows = (items: ShoppingItem[]) => (
    <ul>
      {items.map((item) => (
        <ListItemRow
          key={item.id}
          item={item}
          listKey={listKey}
          readOnly={archived}
          onToggled={onToggled}
          onError={onError}
          onNotice={onNotice}
        />
      ))}
    </ul>
  );

  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="to-get">
        <h2 id="to-get" className={sectionHeading}>
          To get ({list.unchecked.length})
        </h2>
        {archived ? null : (
          <div className="mt-2 rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900">
            <AddItemRow
              homeBaseId={homeBaseId}
              inputRef={addInput}
              autoFocus={empty}
              onAdded={(item) => onNotice(`Added ${itemName(item)}.`)}
            />
          </div>
        )}
        {empty ? (
          <div className="mt-3">
            <EmptyState title={archived ? "This list was empty." : "Nothing on the list."}>
              {archived
                ? null
                : "Add what you need above, add a recipe's ingredients with “Add from recipe”, or add an ingredient from its own page."}
            </EmptyState>
          </div>
        ) : list.unchecked.length === 0 ? (
          <p className={`mt-3 text-sm ${muted}`}>Everything is checked.</p>
        ) : (
          <div className="mt-2">{rows(list.unchecked)}</div>
        )}
      </section>

      {list.checked.length > 0 ? (
        <Disclosure summary={`Checked (${list.checked.length})`}>
          <section aria-label="Checked">{rows(list.checked)}</section>
        </Disclosure>
      ) : null}
    </div>
  );
}

/**
 * "Start a fresh list" confirms inline under the header, never in a dialog
 * (UI-8.2): the carry-over checkbox is on by default and focus lands on Cancel.
 */
function FreshListConfirm({
  homeBaseId,
  list,
  onClose,
}: {
  homeBaseId: string;
  list: ShoppingList;
  onClose: () => void;
}) {
  const archive = useArchiveList();
  const notice = useNotice();
  const [carry, setCarry] = useState(true);
  const cancel = useRef<HTMLButtonElement>(null);
  const n = list.unchecked.length;
  // Focus lands on Cancel (UI-8.2) after the More actions sheet, when there was
  // one, has handed focus back to its opener: that happens in the sheet's unmount
  // cleanup, which runs before this mount effect, so `autoFocus` would lose.
  useEffect(() => cancel.current?.focus(), []);
  const start = () =>
    archive.mutate(
      { home_base_id: homeBaseId, carry_over: carry },
      {
        onSuccess: (out) => {
          onClose();
          notice.show({
            tone: "success",
            message: "Started a fresh list.",
            action: { label: "Open the old one", to: `/plan/list?list_id=${out.archived.id}` },
          });
        },
      },
    );
  return (
    <Alert tone="warn" className="mb-4">
      <div className="flex flex-col gap-2" data-testid="fresh-list-confirm">
        <p>
          Archive this list and start a new one?{" "}
          {carry
            ? `${n} unchecked ${n === 1 ? "item carries" : "items carry"} over.`
            : `${n} unchecked ${n === 1 ? "item stays" : "items stay"} on the old list.`}
        </p>
        <label className="inline-flex min-h-11 items-center gap-2 text-sm lg:min-h-8">
          <input
            type="checkbox"
            className={`size-4 ${focusRing}`}
            checked={carry}
            onChange={(e) => setCarry(e.target.checked)}
          />
          Carry unchecked items over
        </label>
        <div className="flex flex-wrap gap-2">
          <Button ref={cancel} variant="secondary" onClick={onClose} disabled={archive.isPending}>
            Cancel
          </Button>
          <Button onClick={start} disabled={archive.isPending}>
            {archive.isPending ? "Starting…" : "Start a fresh list"}
          </Button>
        </div>
        {archive.isError ? (
          <span className="text-red-800 dark:text-red-200">
            Couldn&apos;t start a fresh list: {errorMessage(archive.error)}
          </span>
        ) : null}
      </div>
    </Alert>
  );
}

/** The kitchen's archived lists, loaded when opened, each readable at `?list_id=`. */
function PreviousLists({ homeBaseId }: { homeBaseId: string }) {
  const [open, setOpen] = useState(false);
  const lists = useArchivedLists(homeBaseId, open);
  return (
    <Disclosure summary="Previous lists" open={open} onOpenChange={setOpen} className="mt-8">
      {lists.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      ) : lists.isError ? (
        <Alert tone="error">
          Couldn&apos;t load the previous lists: {errorMessage(lists.error)}
        </Alert>
      ) : lists.data.length === 0 ? (
        <p className={`text-sm ${muted}`}>No previous lists yet.</p>
      ) : (
        <ul className="flex flex-col">
          {lists.data.map((l) => (
            <li
              key={l.id}
              className="flex flex-wrap items-baseline gap-x-3 border-t border-neutral-200 py-2 text-sm first:border-t-0 dark:border-neutral-800"
            >
              <Link
                to={`/plan/list?list_id=${l.id}`}
                className={`inline-flex min-h-11 items-center rounded-sm underline lg:min-h-0 ${focusRing}`}
              >
                Archived {formatDate(l.archived_at)}
              </Link>
              <span className={muted}>
                {l.item_count} {l.item_count === 1 ? "item" : "items"}
                {l.unchecked_count > 0 ? ` · ${l.unchecked_count} never checked` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Disclosure>
  );
}
