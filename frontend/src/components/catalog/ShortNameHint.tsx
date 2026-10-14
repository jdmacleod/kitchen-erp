import { useQuery } from "@tanstack/react-query";
import { qs } from "../../api/catalog";
import { api } from "../../api/client";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { Button } from "../ui";

/**
 * Offers a product's short name (issue 347) when the typed name repeats the brand
 * or the pack, which have fields of their own: "Shorter: “Thick cut bacon” · Use it".
 */
export function ShortNameHint({
  name,
  brand,
  hasPack,
  onUse,
}: {
  name: string;
  brand: string;
  hasPack: boolean;
  onUse: (name: string) => void;
}) {
  const typed = useDebouncedValue(name.trim(), 400);
  const short = useQuery({
    queryKey: ["products", "short-name", typed, brand.trim(), hasPack],
    queryFn: () =>
      api<{ name: string }>(
        `/products/short-name${qs({ title: typed, brand: brand.trim() || undefined, has_pack: hasPack })}`,
      ),
    enabled: typed.length > 0,
    staleTime: 60_000,
  });
  const suggested = short.data?.name;
  if (!suggested || suggested === name.trim()) return null;
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs" data-testid="short-name-hint">
      <span>
        Shorter: <span className="font-medium">“{suggested}”</span>
      </span>
      <Button
        variant="ghost"
        className="min-h-11 lg:min-h-8 px-2 text-xs"
        onClick={() => onUse(suggested)}
      >
        Use it
      </Button>
    </p>
  );
}
