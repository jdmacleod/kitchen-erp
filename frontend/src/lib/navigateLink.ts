/**
 * The link a stop's "Navigate" opens (10, Trip page; UI-8.16): the phone's own
 * maps application, in a new tab, so the trip page stays where it was. Nothing
 * here is fetched by the app; it is a link the person follows.
 */
export function navigateHref(lat: string, lon: string, name: string, userAgent: string = typeof navigator === "undefined" ? "" : navigator.userAgent): string {
  const at = `${lat},${lon}`;
  if (/iPhone|iPad|iPod/i.test(userAgent)) {
    // Apple's universal link opens Maps on iOS.
    return `https://maps.apple.com/?daddr=${encodeURIComponent(at)}&q=${encodeURIComponent(name)}`;
  }
  if (/Android/i.test(userAgent)) {
    // RFC 5870: the default maps application on Android.
    return `geo:${at}?q=${encodeURIComponent(`${at}(${name})`)}`;
  }
  return `https://www.openstreetmap.org/directions?to=${encodeURIComponent(at)}`;
}
