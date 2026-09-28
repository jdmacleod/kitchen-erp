import { useState } from "react";
import { errorMessage, isApiError } from "../../api/client";
import { formatDistance, useMapPlaces, type MapPlace } from "../../api/geo";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { Combobox } from "../catalog/Combobox";

const KIND_LABEL: Record<MapPlace["kind"], string> = { town: "town", neighbourhood: "neighbourhood", poi: "place" };

/**
 * Find a town, a neighbourhood or a shop by name in the map extract (#62),
 * without leaving the page for another map application to copy coordinates.
 * Towns are found anywhere in the extract; neighbourhoods and shops near the
 * map's centre, so a town first brings the map close enough for its shops.
 */
export function PlaceSearch({
  id,
  near,
  onPick,
}: {
  id: string;
  near: { lat: string; lon: string } | null;
  onPick: (place: MapPlace) => void;
}) {
  const [text, setText] = useState("");
  const debounced = useDebouncedValue(text, 250);
  // Rounded to about 100 m, so panning a little does not repeat the search.
  const around = near ? { lat: Number(near.lat).toFixed(3), lon: Number(near.lon).toFixed(3) } : null;
  const places = useMapPlaces(debounced, around);
  const trimmed = text.trim();
  const found = trimmed.length >= 2 ? (places.data ?? []) : [];

  let status: string | undefined;
  if (trimmed.length >= 2) {
    if (places.isError) {
      status = isApiError(places.error) && places.error.code === "tiles_missing" ? "No map extract is installed to search." : errorMessage(places.error);
    } else if (debounced !== text || places.isFetching) status = "Searching…";
    else if (found.length === 0) status = "Nothing by that name here. Search for the town first to bring the map to it.";
  }

  return (
    <Combobox<MapPlace>
      id={id}
      label="Find a place"
      placeholder="A town, a neighbourhood or a shop"
      hint="Searches this deployment's own map: towns anywhere, shops near the middle of the map."
      listLabel="Places"
      inputValue={text}
      onInputChange={setText}
      items={found}
      getKey={(p) => `${p.kind}:${p.name}:${p.lat}:${p.lon}`}
      status={status}
      onSelect={(p) => {
        setText(p.name);
        onPick(p);
      }}
      renderItem={(p) => (
        <span className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span className="font-medium">{p.name}</span>
          <span className="text-xs text-neutral-600 dark:text-neutral-400">
            {[p.detail && p.detail !== p.kind ? p.detail.replaceAll("_", " ") : KIND_LABEL[p.kind], formatDistance(p.distance_m)].filter(Boolean).join(" · ")}
          </span>
        </span>
      )}
    />
  );
}

/** How close to bring the map for each kind of place. */
export const PLACE_ZOOM: Record<MapPlace["kind"], number> = { town: 13, neighbourhood: 15, poi: 17 };
