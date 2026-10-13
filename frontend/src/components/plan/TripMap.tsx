import { useMemo, useState } from "react";
import type { HomeBase, VendorLocation } from "../../api/geo";
import type { Stop } from "../../api/trips";
import { LazyMapView } from "../geo/LazyMapView";
import type { MapPin } from "../geo/MapView";
import { Alert } from "../ui";

interface TripMapProps {
  kitchen: HomeBase | null;
  stops: Stop[];
  locationById: (id: string) => VendorLocation | undefined;
  selectedId: string | null;
  onSelect: (stopId: string | null) => void;
}

/**
 * A picture of the plan, not a route (10, Trip page: Map; UI-8.16): the vendors
 * map with the kitchen pin, numbered walnut stop pins and straight segments
 * from the kitchen through the stops and back. The selected stop's pin is herb.
 */
export function TripMap({ kitchen, stops, locationById, selectedId, onSelect }: TripMapProps) {
  const [mapError, setMapError] = useState<string | null>(null);
  const { pins, path, missing } = useMemo(() => {
    const pins: MapPin[] = [];
    const path: { lat: string; lon: string }[] = [];
    let missing = 0;
    if (kitchen) {
      pins.push({ id: "kitchen", lat: kitchen.lat, lon: kitchen.lon, kind: "home", label: `${kitchen.name} (kitchen)` });
      path.push({ lat: kitchen.lat, lon: kitchen.lon });
    }
    for (const stop of stops) {
      const location = locationById(stop.vendor_location_id);
      if (!location) {
        missing += 1;
        continue;
      }
      pins.push({ id: stop.id, lat: location.lat, lon: location.lon, kind: "stop", number: stop.seq, label: `Stop ${stop.seq}: ${stop.vendor_location_name}` });
      path.push({ lat: location.lat, lon: location.lon });
    }
    if (kitchen && path.length > 1) path.push({ lat: kitchen.lat, lon: kitchen.lon });
    return { pins, path, missing };
  }, [kitchen, stops, locationById]);

  return (
    <div className="flex flex-col gap-2" data-testid="trip-map">
      {mapError ? <Alert tone="error">{mapError}</Alert> : null}
      <LazyMapView
        label="Trip map"
        className="h-80 overflow-hidden rounded-lg border border-neutral-200 lg:h-[28rem] dark:border-neutral-800"
        pins={pins}
        path={path}
        selectedId={selectedId}
        onPinSelect={(id) => onSelect(id === "kitchen" ? null : id)}
        onMapError={setMapError}
      />
      <p className="text-xs text-neutral-600 dark:text-neutral-400">
        Straight lines between the stops in order, not the route.
        {missing > 0 ? ` ${missing} ${missing === 1 ? "stop could not be placed: its location is gone." : "stops could not be placed: their locations are gone."}` : ""}
      </p>
    </div>
  );
}
