import { useState, type FormEvent } from "react";
import { errorMessage } from "../../api/client";
import { formatLatLon, geoErrorMessage, useCreateHomeBase, useDeleteHomeBase, useHomeBases, useUpdateHomeBase, type HomeBase } from "../../api/geo";
import { LazyMapView as MapView } from "../../components/geo/LazyMapView";
import type { MapPin } from "../../components/geo/MapView";
import { Alert, Button, Card, EmptyState, Field, PageHeader } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";

export function HomeBasesPage() {
  usePageTitle("Kitchens");
  const homeBases = useHomeBases();
  const [tilesPresent, setTilesPresent] = useState<boolean | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ lat: string; lon: string } | null>(null);
  const [name, setName] = useState("");
  const [label, setLabel] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);
  const create = useCreateHomeBase();

  const pins: MapPin[] = (homeBases.data ?? []).map((h) => ({ id: h.id, lat: h.lat, lon: h.lon, kind: "home", label: `${h.name} (home base)` }));

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft) {
      setInvalid("Drop a pin on the map first.");
      return;
    }
    if (!name.trim()) {
      setInvalid("A name is required.");
      return;
    }
    setInvalid(null);
    create.mutate(
      { name: name.trim(), lat: draft.lat, lon: draft.lon, ...(label.trim() ? { label: label.trim() } : {}) },
      {
        onSuccess: () => {
          setDraft(null);
          setName("");
          setLabel("");
        },
      },
    );
  };

  return (
    <>
      <PageHeader title="Kitchens" />
      <div className="flex flex-col gap-6">
        <Card>
          <form onSubmit={onSubmit} className="flex flex-col gap-4" aria-labelledby="create-home-heading" noValidate>
            <h2 id="create-home-heading" className="text-lg font-medium">
              Add a home base
            </h2>
            <p className="text-sm text-neutral-600 dark:text-neutral-400">
              Click the map where the base is, then give it a name. New vendor locations default to the nearest base.
            </p>
            {tilesPresent === false ? <Alert tone="info">Map tiles are missing; see docs/tiles.md. Pins are still placed at their coordinates.</Alert> : null}
            {mapError ? <Alert tone="error">{mapError}</Alert> : null}
            <MapView label="Home bases" className="h-72 overflow-hidden rounded-md border border-neutral-200 dark:border-neutral-800" pins={pins} placing draft={draft} onMapClick={(lat, lon) => setDraft({ lat, lon })} onTilesStatus={setTilesPresent} onMapError={setMapError} />
            <p className="font-mono text-xs text-neutral-600 dark:text-neutral-400" data-testid="draft-point">
              {draft ? formatLatLon(draft.lat, draft.lon) : "No pin yet — click the map."}
            </p>
            {invalid ? <Alert tone="error">{invalid}</Alert> : null}
            {create.isError ? <Alert tone="error">{geoErrorMessage(create.error)}</Alert> : null}
            <div className="grid gap-4 sm:grid-cols-2">
              <Field id="new-home-name" label="Name" autoComplete="off" required value={name} onChange={(e) => setName(e.target.value)} />
              <Field id="new-home-label" label="Label" autoComplete="off" value={label} onChange={(e) => setLabel(e.target.value)} hint="Optional, e.g. “weekday” or “weekend place”." />
            </div>
            <div>
              <Button type="submit" disabled={create.isPending || !draft}>
                {create.isPending ? "Creating…" : "Create home base"}
              </Button>
            </div>
          </form>
        </Card>

        <Card>
          <h2 className="mb-3 text-lg font-medium">Your home bases</h2>
          {homeBases.isPending ? (
            <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
              Loading…
            </p>
          ) : homeBases.isError ? (
            <Alert tone="error">{errorMessage(homeBases.error)}</Alert>
          ) : homeBases.data.length === 0 ? (
            <EmptyState title="No home bases yet" />
          ) : (
            <ul aria-label="Home bases" className="divide-y divide-neutral-200 dark:divide-neutral-800">
              {homeBases.data.map((h) => (
                <HomeBaseRow key={h.id} home={h} />
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

function HomeBaseRow({ home }: { home: HomeBase }) {
  const update = useUpdateHomeBase();
  const remove = useDeleteHomeBase();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [name, setName] = useState(home.name);
  const [label, setLabel] = useState(home.label ?? "");

  const save = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!name.trim()) return;
    update.mutate({ id: home.id, name: name.trim(), label: label.trim() || null }, { onSuccess: () => setEditing(false) });
  };

  return (
    <li className="py-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">
            {home.name}
            {home.label ? <span className="text-neutral-600 dark:text-neutral-400"> · {home.label}</span> : null}
          </p>
          <p className="font-mono text-xs text-neutral-600 dark:text-neutral-400">{formatLatLon(home.lat, home.lon)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" className="min-h-8 px-2 text-xs" onClick={() => setEditing((v) => !v)} aria-expanded={editing} aria-label={`Edit ${home.name}`}>
            Edit
          </Button>
          {confirming ? (
            <span className="flex gap-2" role="group" aria-label={`Confirm deleting ${home.name}`}>
              <Button variant="danger" className="min-h-8 px-2 text-xs" disabled={remove.isPending} onClick={() => remove.mutate(home.id, { onSettled: () => setConfirming(false) })}>
                {remove.isPending ? "Deleting…" : "Confirm delete"}
              </Button>
              <Button variant="secondary" className="min-h-8 px-2 text-xs" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </span>
          ) : (
            <Button variant="danger" className="min-h-8 px-2 text-xs" onClick={() => setConfirming(true)} aria-label={`Delete ${home.name}`}>
              Delete
            </Button>
          )}
        </div>
      </div>
      {remove.isError ? (
        <Alert tone="error" className="mt-2">
          {geoErrorMessage(remove.error)}
        </Alert>
      ) : null}
      {editing ? (
        <form onSubmit={save} className="mt-3 flex flex-col gap-3" aria-label={`Edit home base ${home.name}`}>
          {update.isError ? <Alert tone="error">{geoErrorMessage(update.error)}</Alert> : null}
          <div className="grid gap-3 sm:grid-cols-2">
            <Field id={`home-${home.id}-name`} label="Name" autoComplete="off" required value={name} onChange={(e) => setName(e.target.value)} />
            <Field id={`home-${home.id}-label`} label="Label" autoComplete="off" value={label} onChange={(e) => setLabel(e.target.value)} />
          </div>
          <div className="flex gap-2">
            <Button type="submit" disabled={update.isPending}>
              {update.isPending ? "Saving…" : "Save"}
            </Button>
            <Button variant="secondary" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </li>
  );
}
