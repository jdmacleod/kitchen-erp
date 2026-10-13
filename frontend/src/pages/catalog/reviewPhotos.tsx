// Photo choices on the product review page: which main photo to preselect, and a photo's size.
import type { PhotoSummary, ProductPhoto } from "../../api/productPhotos";

/**
 * The main photo preselected on review: the first product photo that arrived, or on an
 * update, the product's current main photo unless a photo with more pixels arrived.
 */
export function defaultMain(arrived: ProductPhoto[], current: PhotoSummary | null): string | null {
  if (!current) return arrived[0]?.id ?? null;
  const have = pixels(current) ?? Infinity;
  const larger = arrived
    .filter((p) => (pixels(p) ?? 0) > have)
    .sort((x, y) => (pixels(y) ?? 0) - (pixels(x) ?? 0));
  return larger[0]?.id ?? current.id;
}

/** Width × height, or null when the photo's size isn't known yet. */
export function pixels(photo: PhotoSummary | null): number | null {
  return photo?.width && photo.height ? photo.width * photo.height : null;
}

export function PhotoSize({ photo }: { photo: PhotoSummary }) {
  if (!photo.width || !photo.height) return null;
  return (
    <span className={`text-xs tabular-nums text-neutral-600 dark:text-neutral-400`}>
      {photo.width} × {photo.height} px
    </span>
  );
}
