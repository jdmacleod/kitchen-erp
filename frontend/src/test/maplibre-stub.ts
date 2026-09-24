// A light stand-in for maplibre-gl under jsdom, which has no WebGL. Markers
// append their element to the map container so pins are in the document, and
// `fire()` lets a test simulate a map click.
import { vi } from "vitest";

type Handler = (event: unknown) => void;

export class MapStub {
  container: HTMLElement;
  private handlers = new globalThis.Map<string, Handler[]>();
  constructor(options: { container: HTMLElement }) {
    this.container = options.container;
    instances.push(this);
  }
  on(type: string, handler: Handler) {
    const list = this.handlers.get(type) ?? [];
    list.push(handler);
    this.handlers.set(type, list);
    return this;
  }
  off() {
    return this;
  }
  fire(type: string, event: unknown) {
    for (const handler of this.handlers.get(type) ?? []) handler(event);
  }
  addControl() {
    return this;
  }
  remove() {
    instances.splice(instances.indexOf(this), 1);
  }
  /** Every jumpTo, so a test can assert the view followed a point it was given. */
  jumps: { center: [number, number]; zoom?: number }[] = [];
  zoom = 9;
  jumpTo(options: { center: [number, number]; zoom?: number }) {
    this.jumps.push(options);
    if (options.zoom !== undefined) this.zoom = options.zoom;
  }
  getZoom() {
    return this.zoom;
  }
  fitBounds() {}
  getContainer() {
    return this.container;
  }
}

export class Marker {
  private element: HTMLElement;
  private lngLat = { lng: 0, lat: 0 };
  constructor(options?: { element?: HTMLElement }) {
    this.element = options?.element ?? document.createElement("div");
  }
  setLngLat([lng, lat]: [number, number]) {
    this.lngLat = { lng, lat };
    this.element.dataset.lng = String(lng);
    this.element.dataset.lat = String(lat);
    return this;
  }
  addTo(map: MapStub) {
    this.element.classList.add("maplibregl-marker");
    map.container.appendChild(this.element);
    return this;
  }
  remove() {
    this.element.remove();
  }
  getElement() {
    return this.element;
  }
  getLngLat() {
    return this.lngLat;
  }
  on() {
    return this;
  }
}

export class NavigationControl {}

export const addProtocol = vi.fn();

/** Every map created and not yet removed, oldest first. */
export const instances: MapStub[] = [];

export { MapStub as Map };
