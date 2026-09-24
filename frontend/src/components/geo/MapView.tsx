import {
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  addProtocol,
  type LayerSpecification,
  type LngLatBoundsLike,
  type StyleSpecification,
} from "maplibre-gl";
import { noLabels } from "protomaps-themes-base";
import { Protocol } from "pmtiles";
import { useEffect, useRef, useState } from "react";
import type { VendorKind } from "../../api/geo";
import "maplibre-gl/dist/maplibre-gl.css";
import "./map.css";

export const TILES_URL = "/tiles/basemap.pmtiles";
export const ATTRIBUTION = "© OpenStreetMap contributors © Protomaps";

// The synthetic box from SECURITY.md is the empty-map view: open ocean, so no
// home or shop is implied before the first pin exists.
const EMPTY_CENTER_LON = -120.5;
const EMPTY_CENTER_LAT = 33.5;

export type PinKind = VendorKind | "home";

export interface MapPin {
  id: string;
  lat: string;
  lon: string;
  kind: PinKind;
  label: string;
  inactive?: boolean;
  /** A short text drawn under the pin, e.g. a price per unit. */
  priceLabel?: string;
  /** The price label is past its staleness threshold. */
  stale?: boolean;
}

export interface MapViewProps {
  pins: MapPin[];
  selectedId?: string | null;
  onPinSelect?: (id: string) => void;
  /** When placing, a click on the map (or dragging the draft pin) reports a point. */
  placing?: boolean;
  onMapClick?: (lat: string, lon: string) => void;
  draft?: { lat: string; lon: string } | null;
  /** Reports whether the tiles file is present, once checked. */
  onTilesStatus?: (present: boolean) => void;
  /** Accessible name for the map region. */
  label: string;
  className?: string;
  /** Start here instead of fitting the first pins. */
  initialView?: { lat: string; lon: string; zoom: number } | null;
  /**
   * Move the view to this point whenever it changes to a new one. For a point
   * chosen away from the map — typed, pasted, or read off the device — where
   * the pin would otherwise land outside the viewport with nothing to say so.
   * A point that came from a click is already in view and is not passed here.
   */
  centerOn?: { lat: string; lon: string } | null;
}

const PIN_KINDS: readonly PinKind[] = ["chain", "independent", "market", "stand", "home"];

let protocolRegistered = false;

function registerProtocol() {
  if (protocolRegistered) return;
  protocolRegistered = true;
  const protocol = new Protocol();
  addProtocol("pmtiles", protocol.tile);
}

/** True when a HEAD to the tiles file comes back OK. Any failure counts as missing. */
export async function tilesPresent(): Promise<boolean> {
  try {
    const response = await fetch(TILES_URL, { method: "HEAD", credentials: "include" });
    return response.ok;
  } catch {
    return false;
  }
}

function prefersDark(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : false;
}

function buildStyle(present: boolean): StyleSpecification {
  if (!present) {
    // No source at all: a plain neutral ground with the pins on top.
    return {
      version: 8,
      sources: {},
      layers: [{ id: "ground", type: "background", paint: { "background-color": prefersDark() ? "#262626" : "#e5e5e5" } }],
    };
  }
  // The Protomaps theme's label layers need glyphs (fonts) that ship from a
  // CDN. Criterion 29 forbids any request to another origin, so the symbol
  // layers are dropped instead of pointing `glyphs` at a remote host. The map
  // is therefore unlabelled; pins carry the names.
  const layers: LayerSpecification[] = noLabels("protomaps", prefersDark() ? "dark" : "light").filter(
    (layer) => layer.type !== "symbol",
  );
  return {
    version: 8,
    sources: {
      protomaps: { type: "vector", url: `pmtiles://${TILES_URL}`, attribution: ATTRIBUTION },
    },
    layers,
  };
}

function pinElement(pin: MapPin | { kind: "draft" }, label: string): HTMLButtonElement {
  const el = document.createElement("button");
  el.type = "button";
  el.className = `kerp-pin kerp-pin--${pin.kind}`;
  el.setAttribute("aria-label", label);
  const shape = document.createElement("span");
  shape.className = "kerp-pin__shape";
  shape.setAttribute("aria-hidden", "true");
  if (pin.kind === "home") {
    shape.innerHTML =
      '<svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"><path d="M12 2.5 2.5 11h3v10h5v-6h3v6h5V11h3z"/></svg>';
  }
  el.appendChild(shape);
  const text = document.createElement("span");
  text.className = "kerp-pin__label";
  text.setAttribute("aria-hidden", "true");
  el.appendChild(text);
  return el;
}

function num(value: string): number {
  return Number.parseFloat(value);
}

function fix(value: number): string {
  return value.toFixed(6);
}

/**
 * A MapLibre map with DOM-marker pins. Reads the PMTiles extract from the app's
 * own origin, and when the extract is missing draws a plain ground instead.
 * Nothing here ever reaches another origin.
 */
export function MapView({ pins, selectedId, onPinSelect, placing = false, onMapClick, draft, onTilesStatus, label, className = "", initialView = null, centerOn = null }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const markersRef = useRef(new globalThis.Map<string, Marker>());
  const draftRef = useRef<Marker | null>(null);
  const fittedRef = useRef(initialView !== null);
  const initialViewRef = useRef(initialView);
  const [ready, setReady] = useState<MapLibreMap | null>(null);
  // Latest callbacks, read by handlers registered once.
  const callbacks = useRef({ onPinSelect, onMapClick, placing, onTilesStatus });
  useEffect(() => {
    callbacks.current = { onPinSelect, onMapClick, placing, onTilesStatus };
  });

  // Create the map once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let cancelled = false;
    let map: MapLibreMap | null = null;
    const markers = markersRef.current;
    void tilesPresent().then((present) => {
      if (cancelled) return;
      callbacks.current.onTilesStatus?.(present);
      if (present) registerProtocol();
      const start = initialViewRef.current;
      const created = new MapLibreMap({
        container,
        style: buildStyle(present),
        center: start ? [num(start.lon), num(start.lat)] : [EMPTY_CENTER_LON, EMPTY_CENTER_LAT],
        zoom: start ? start.zoom : 9,
        attributionControl: false,
      });
      created.addControl(new NavigationControl({ showCompass: false }), "top-right");
      created.on("click", (event) => {
        if (!callbacks.current.placing) return;
        callbacks.current.onMapClick?.(fix(event.lngLat.lat), fix(event.lngLat.lng));
      });
      map = created;
      setReady(created);
    });
    return () => {
      cancelled = true;
      for (const marker of markers.values()) marker.remove();
      markers.clear();
      draftRef.current?.remove();
      draftRef.current = null;
      map?.remove();
      setReady(null);
    };
  }, []);

  // Sync pins to markers.
  useEffect(() => {
    const map = ready;
    if (!map) return;
    const markers = markersRef.current;
    const seen = new Set<string>();
    for (const pin of pins) {
      seen.add(pin.id);
      let marker = markers.get(pin.id);
      if (!marker) {
        const el = pinElement(pin, pin.label);
        el.addEventListener("click", (e) => {
          e.stopPropagation();
          callbacks.current.onPinSelect?.(pin.id);
        });
        marker = new Marker({ element: el, anchor: "center" }).setLngLat([num(pin.lon), num(pin.lat)]).addTo(map);
        markers.set(pin.id, marker);
      } else {
        marker.setLngLat([num(pin.lon), num(pin.lat)]);
      }
      // Toggle classes rather than assigning className: MapLibre keeps its own
      // positioning classes on the same element.
      const el = marker.getElement();
      for (const k of PIN_KINDS) el.classList.toggle(`kerp-pin--${k}`, pin.kind === k);
      el.classList.toggle("kerp-pin--selected", pin.id === selectedId);
      el.classList.toggle("kerp-pin--inactive", Boolean(pin.inactive));
      el.classList.toggle("kerp-pin--priced", Boolean(pin.priceLabel));
      el.classList.toggle("kerp-pin--stale", Boolean(pin.stale));
      const text = el.querySelector<HTMLElement>(".kerp-pin__label");
      if (text) text.textContent = pin.priceLabel ? (pin.stale ? `${pin.priceLabel} (stale)` : pin.priceLabel) : "";
      el.setAttribute("aria-label", pin.label);
      el.setAttribute("aria-pressed", pin.id === selectedId ? "true" : "false");
    }
    for (const [id, marker] of markers) {
      if (!seen.has(id)) {
        marker.remove();
        markers.delete(id);
      }
    }
    if (!fittedRef.current && pins.length > 0) {
      fittedRef.current = true;
      if (pins.length === 1) {
        map.jumpTo({ center: [num(pins[0].lon), num(pins[0].lat)], zoom: 13 });
      } else {
        let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity;
        for (const pin of pins) {
          const lon = num(pin.lon), lat = num(pin.lat);
          west = Math.min(west, lon); east = Math.max(east, lon);
          south = Math.min(south, lat); north = Math.max(north, lat);
        }
        const bounds: LngLatBoundsLike = [[west, south], [east, north]];
        map.fitBounds(bounds, { padding: 48, maxZoom: 14, duration: 0 });
      }
    }
  }, [ready, pins, selectedId]);

  // Move to a point chosen away from the map. Held as the two values rather than
  // the object so a caller that rebuilds it each render does not re-centre the
  // map out from under a drag.
  const centerLat = centerOn?.lat ?? null;
  const centerLon = centerOn?.lon ?? null;
  useEffect(() => {
    const map = ready;
    if (!map || centerLat === null || centerLon === null) return;
    // Keep the operator's zoom when they are already close in; only pull in when
    // the view is wide enough that a pin would be lost on it.
    map.jumpTo({ center: [num(centerLon), num(centerLat)], zoom: Math.max(map.getZoom(), 13) });
  }, [ready, centerLat, centerLon]);

  // Sync the draft pin.
  useEffect(() => {
    const map = ready;
    if (!map) return;
    if (!draft) {
      draftRef.current?.remove();
      draftRef.current = null;
      return;
    }
    if (!draftRef.current) {
      const el = pinElement({ kind: "draft" }, "New pin (drag to move)");
      const marker = new Marker({ element: el, anchor: "center", draggable: true }).setLngLat([num(draft.lon), num(draft.lat)]).addTo(map);
      marker.on("dragend", () => {
        const at = marker.getLngLat();
        callbacks.current.onMapClick?.(fix(at.lat), fix(at.lng));
      });
      draftRef.current = marker;
    } else {
      draftRef.current.setLngLat([num(draft.lon), num(draft.lat)]);
    }
  }, [ready, draft]);

  return (
    <div className={`relative ${className}`}>
      <div
        ref={containerRef}
        role="region"
        aria-label={label}
        data-testid="map-canvas"
        className={`h-full w-full ${placing ? "kerp-map--placing" : ""}`}
      />
      <div className="kerp-attribution" data-testid="map-attribution">
        {ATTRIBUTION}
      </div>
    </div>
  );
}
