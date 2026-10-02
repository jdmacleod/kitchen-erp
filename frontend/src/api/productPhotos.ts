// Product photos (03, 1I): a product's photos, adding them, and choosing the
// main one. Photo files are served at versioned media addresses the API hands
// out, so they are never built here.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { API_BASE, ApiError, api } from "./client";
import { catalogKeys } from "./catalog";
import type { ErrorEnvelope } from "./types";

export type PhotoRole = "product" | "label_front" | "label_nutrition" | "label_ingredients" | "shelf_tag";
export type PhotoStatus = "processing" | "candidate" | "active" | "hidden" | "failed";
export type PhotoSource = "user_photo" | "manufacturer" | "open_food_facts" | "vendor_listing";

export interface PhotoUrls {
  small: string;
  medium: string;
  large: string;
  cutout_medium: string | null;
  cutout_large: string | null;
}

/** A product's main photo, as lists and headers show it. */
export interface PhotoSummary {
  id: string;
  width: number | null;
  height: number | null;
  has_cutout: boolean;
  urls: PhotoUrls | null;
}

export interface ProductPhoto extends PhotoSummary {
  product_id: string | null;
  role: PhotoRole;
  status: PhotoStatus;
  source_kind: PhotoSource;
  source_url: string | null;
  attribution: string | null;
  cutout_source: "device" | "tool" | null;
  pinned: boolean;
  is_stock_suspect: boolean;
  ocr_text: string | null;
  captured_at: string | null;
  created_at: string;
  is_main: boolean;
}

export interface ProductPhotoList {
  items: ProductPhoto[];
  primary_image_id: string | null;
}

export const MAX_PHOTOS = 4;

export const ROLE_LABELS: Record<PhotoRole, string> = {
  product: "Product",
  label_front: "Front label",
  label_nutrition: "Nutrition",
  label_ingredients: "Ingredients",
  shelf_tag: "Shelf tag",
};

export const SOURCE_LABELS: Record<PhotoSource, string> = {
  user_photo: "Your photo",
  manufacturer: "Manufacturer",
  open_food_facts: "Open Food Facts",
  vendor_listing: "Store page",
};

export const photoKeys = {
  list: (productId: string) => ["products", "photos", productId] as const,
};

/** Photos still being prepared: the list asks again until they are done. */
export function isPreparing(list: ProductPhotoList | undefined): boolean {
  return Boolean(list?.items.some((p) => p.status === "processing"));
}

export function useProductPhotos(productId: string) {
  return useQuery({
    queryKey: photoKeys.list(productId),
    queryFn: () => api<ProductPhotoList>(`/products/${encodeURIComponent(productId)}/photos`),
    refetchInterval: (query) => (isPreparing(query.state.data) ? 2000 : false),
  });
}

export interface PhotoUploadInput {
  productId: string;
  photos: { file: File; role: PhotoRole }[];
}

export async function addProductPhotos({ productId, photos }: PhotoUploadInput): Promise<ProductPhotoList> {
  const form = new FormData();
  form.append("product_id", productId);
  for (const { file, role } of photos) {
    form.append("photos", file, file.name);
    form.append("roles", role);
  }
  const response = await fetch(`${API_BASE}/product-photos`, {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json" },
    body: form,
  });
  const text = await response.text();
  let payload: unknown;
  try {
    payload = text ? JSON.parse(text) : undefined;
  } catch {
    payload = undefined;
  }
  if (!response.ok) {
    const err = (payload as ErrorEnvelope | undefined)?.error;
    if (err && typeof err.code === "string") throw new ApiError(response.status, err.code, err.message, err.details);
    throw new ApiError(response.status, "http_error", `Upload failed with status ${response.status}.`);
  }
  return payload as ProductPhotoList;
}

/** What to say when adding a photo is refused (PD6). */
export function photoErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "image_too_large") return "That photo is over 50 megapixels. Try a smaller one.";
    if (error.code === "unsupported_image") return "That file isn't a photo this can read. Use JPEG, PNG, WebP or HEIC.";
    if (error.code === "payload_too_large") return "That photo is too large to upload.";
    return error.message;
  }
  return "Could not reach the server.";
}

function useRefresh(productId: string) {
  const client = useQueryClient();
  return (list?: ProductPhotoList) => {
    if (list) client.setQueryData(photoKeys.list(productId), list);
    void client.invalidateQueries({ queryKey: photoKeys.list(productId) });
    void client.invalidateQueries({ queryKey: catalogKeys.product(productId) });
    void client.invalidateQueries({ queryKey: [...catalogKeys.products, "list"] });
  };
}

export function useAddProductPhotos(productId: string) {
  const refresh = useRefresh(productId);
  return useMutation({ mutationFn: addProductPhotos, onSuccess: (list) => refresh(list) });
}

export type PhotoAction = "use-as-main" | "unset-main" | "hide" | "show" | "retry";

export function usePhotoAction(productId: string) {
  const refresh = useRefresh(productId);
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: PhotoAction }) =>
      api<ProductPhoto>(`/product-photos/${encodeURIComponent(id)}/${action}`, { method: "POST" }),
    onSuccess: () => refresh(),
  });
}
