import type { PhotoSummary } from "../../api/productPhotos";
import { categoryClass, type CategoryKey } from "../CategoryChip";

/**
 * A product's photo as a small square, or its placeholder (08, PD14–PD15).
 *
 * A cutout sits on whatever card surface it is shown on, with the cutout shadow;
 * a photo without one fills its square. With no photo, a square in the
 * category's tint carries the product's first letter. The name is always shown
 * beside it, so the image itself is decorative.
 */
export function ProductThumb({
  name,
  photo,
  categoryKey,
  size = 40,
}: {
  name: string;
  photo: PhotoSummary | null | undefined;
  categoryKey: CategoryKey | null | undefined;
  size?: number;
}) {
  const box = { width: size, height: size };
  const urls = photo?.urls;
  if (urls) {
    const src = size > 160 ? urls.medium : urls.small;
    if (photo.has_cutout && urls.cutout_medium) {
      return (
        <img src={urls.cutout_medium} alt="" style={box} loading="lazy" decoding="async" className="flex-none object-contain cutout-shadow" />
      );
    }
    return (
      <img
        src={src}
        alt=""
        style={box}
        loading="lazy"
        decoding="async"
        className="flex-none rounded-md bg-neutral-100 object-cover dark:bg-neutral-800"
      />
    );
  }
  const letter = name.trim().charAt(0).toLocaleUpperCase() || "?";
  return (
    <span aria-hidden="true" data-testid="photo-placeholder" style={{ ...box, fontSize: size * 0.5 }} className={`${categoryClass(categoryKey)} cat-placeholder`}>
      {letter}
    </span>
  );
}
