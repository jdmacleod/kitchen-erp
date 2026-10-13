import { type APIRequestContext, expect } from "@playwright/test";

/**
 * A kitchen, two invented stores and a four-item list a trip can be planned
 * from (17, 4B and 4C), made through the API for the trip specs. The e2e stack
 * is not seeded, so each worker makes its own, stamped with its tag.
 *
 * Geography is the synthetic grid from SECURITY.md: the kitchen sits at a
 * random point in the grid's south-west corner, away from the other specs'
 * pins, and the two stores lie about a kilometre from it. Every name is
 * invented. With `ROUTING_URL` empty on the e2e stack, every leg is an
 * estimate and every plan reads "estimated".
 *
 * Prices (1 kg bags, one per item):
 *   Oats      north 4.00 · south 4.60     -> bought at north
 *   Barley    north 3.00                  -> north only
 *   Millet    north 2.60 · south 2.00     -> bought at south
 *   Lentils   south 5.00                  -> south only
 * So every plan has both stores, and whichever comes first has one later item
 * it also sells: its "Cheaper at stop 2".
 */
export interface TripWorld {
  kitchenId: string;
  kitchenName: string;
  north: { id: string; name: string };
  south: { id: string; name: string };
  /** Ingredient (and list item) names by key. */
  items: Record<"oats" | "barley" | "millet" | "lentils", string>;
}

const coord = (n: number) => n.toFixed(6);

async function created<T>(what: string, response: Awaited<ReturnType<APIRequestContext["post"]>>): Promise<T> {
  expect(response.status(), `${what}: ${await response.text()}`).toBe(201);
  return (await response.json()) as T;
}

export async function makeTripWorld(request: APIRequestContext, stamp: string): Promise<TripWorld> {
  // Lat 33.04–33.12, lon -120.97 to -120.89: inside the grid with room for the stores.
  const lat = 33.04 + Math.random() * 0.08;
  const lon = -120.97 + Math.random() * 0.08;
  const kitchenName = `E2E Kelp kitchen ${stamp}`;
  const kitchen = await created<{ id: string }>(
    "kitchen",
    await request.post("/api/v1/home-bases", { data: { name: kitchenName, lat: coord(lat), lon: coord(lon) } }),
  );

  const store = async (name: string, dLat: number, dLon: number) =>
    created<{ id: string; name: string }>(
      name,
      await request.post("/api/v1/vendor-locations", {
        data: { name, lat: coord(lat + dLat), lon: coord(lon + dLon), vendor: { name, kind: "independent", price_scope: "location" } },
      }),
    );
  const north = await store(`E2E Tidewater Grocer ${stamp}`, 0.009, 0.002);
  const south = await store(`E2E Driftwood Market ${stamp}`, -0.007, 0.006);

  const items = {
    oats: `E2E Oats ${stamp}`,
    barley: `E2E Barley ${stamp}`,
    millet: `E2E Millet ${stamp}`,
    lentils: `E2E Lentils ${stamp}`,
  };
  const prices: Record<keyof typeof items, [string | null, string | null]> = {
    oats: ["4.00", "4.60"],
    barley: ["3.00", null],
    millet: ["2.60", "2.00"],
    lentils: [null, "5.00"],
  };
  for (const key of Object.keys(items) as (keyof typeof items)[]) {
    const name = items[key];
    const product = await created<{ id: string; ingredient: { id: string } }>(
      `product ${name}`,
      await request.post("/api/v1/products", {
        data: { ingredient: { name, canonical_unit: "g" }, name: `${name} bag`, pack_qty: "1", pack_unit: "kg" },
      }),
    );
    const [atNorth, atSouth] = prices[key];
    for (const [location, price] of [
      [north.id, atNorth],
      [south.id, atSouth],
    ] as const) {
      if (price === null) continue;
      await created(
        `price of ${name}`,
        await request.post("/api/v1/price-observations", { data: { product_id: product.id, vendor_location_id: location, price, unit: "each" } }),
      );
    }
    await created(
      `list item ${name}`,
      await request.post("/api/v1/plan/list/items", {
        data: { home_base_id: kitchen.id, kind: "ingredient", ingredient_id: product.ingredient.id, amounts: [{ amount: "1", unit: "kg" }] },
      }),
    );
  }
  return { kitchenId: kitchen.id, kitchenName, north, south, items };
}

/** A plan made and chosen through the API (Balanced), for pages that need one to exist. */
export async function makeChosenTrip(request: APIRequestContext, world: TripWorld): Promise<string> {
  const plan = await created<{ id: string; variants: { id: string; kind: string }[] }>(
    "trip",
    await request.post("/api/v1/plan/trips", {
      data: { home_base_id: world.kitchenId, planned_at: new Date(Date.now() + 3_600_000).toISOString(), max_stops: 3 },
    }),
  );
  const balanced = plan.variants.find((v) => v.kind === "balanced");
  expect(balanced, "a balanced variant").toBeTruthy();
  const chosen = await request.post(`/api/v1/plan/trips/${plan.id}/choose`, { data: { variant_id: balanced!.id } });
  expect(chosen.status(), await chosen.text()).toBe(200);
  return plan.id;
}
