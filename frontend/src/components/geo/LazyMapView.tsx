import { Suspense, lazy } from "react";
import type { MapViewProps } from "./MapView";

// maplibre-gl is the biggest dependency in the bundle; only the map and home
// base pages need it, so it loads on demand.
const MapViewImpl = lazy(() => import("./MapView").then((m) => ({ default: m.MapView })));

export function LazyMapView(props: MapViewProps) {
  return (
    <Suspense
      fallback={
        <div className={`relative ${props.className ?? ""}`}>
          <p role="status" className="p-3 text-sm text-neutral-600 dark:text-neutral-400">
            Loading map…
          </p>
        </div>
      }
    >
      <MapViewImpl {...props} />
    </Suspense>
  );
}
