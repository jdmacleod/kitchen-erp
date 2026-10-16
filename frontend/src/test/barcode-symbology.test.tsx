import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { Product } from "../api/catalog";
import { checkDigit, needsSymbology, upceToUpca } from "../lib/barcode";
import { flour, flourProduct, flourProductId, ingredientMatch, units } from "./catalog-fixtures";
import { chainLocation, chainLocationId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

// Codes are made here, with their check digits computed, so no fixed digit run
// in this file looks like a real product's barcode.
const withCheck = (body: string) => body + String(checkDigit(body));

/** The first eight-digit code from `start` up that `fits`. */
function findCode(fits: (code: string) => boolean, start = 0): string {
  for (let n = start; n < 10 ** 7; n++) {
    const code = withCheck(String(n).padStart(7, "0"));
    if (fits(code)) return code;
  }
  throw new Error("no such code");
}

const AMBIGUOUS = findCode(needsSymbology);
const OTHER_AMBIGUOUS = findCode(needsSymbology, Number(AMBIGUOUS.slice(0, 7)) + 1);
// EAN-8 only: UPC-E starts with 0 or 1, so a code starting with 5 reads one way.
const EAN8_ONLY = findCode(() => true, 5 * 10 ** 6);
const QUESTION = "Is this EAN-8 or UPC-E?";
const REQUIRED = "Say whether the barcode is EAN-8 or UPC-E.";

afterEach(() => localStorage.clear());

describe("which codes need a symbology (spec 03, 1H; criterion 87)", () => {
  it("asks only for eight digits valid both ways whose readings differ", () => {
    expect(needsSymbology(AMBIGUOUS)).toBe(true);
    expect(upceToUpca(AMBIGUOUS)).not.toBeNull();
    expect(needsSymbology(` ${AMBIGUOUS} `)).toBe(true);
    expect(needsSymbology(EAN8_ONLY)).toBe(false);
    // A wrong check digit is refused by the server, not asked about.
    const wrong = AMBIGUOUS.slice(0, 7) + String((Number(AMBIGUOUS[7]) + 1) % 10);
    expect(needsSymbology(wrong)).toBe(false);
    // Twelve digits, and codes that are not barcodes, are never ambiguous.
    expect(needsSymbology(withCheck("0".repeat(4) + AMBIGUOUS.slice(0, 7)))).toBe(false);
    expect(needsSymbology("ABC-1")).toBe(false);
  });

  it("matches the check digit the server computes", () => {
    // The GS1 weights alternate 3 and 1 from the right of the body.
    expect(checkDigit("0".repeat(6) + "1")).toBe(7);
    expect(checkDigit("0".repeat(5) + "10")).toBe(9);
  });
});

function productRoutes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /products": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /products/search": () => jsonResponse(200, { items: [] }),
    "GET /ingredients/search": () => jsonResponse(200, { items: [ingredientMatch(flour)] }),
  };
}

async function openAddDrawer(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("heading", { name: "Products" });
  await user.click(screen.getAllByRole("button", { name: "Add product" })[0]);
  const dialog = await screen.findByRole("dialog", { name: "Add product" });
  const form = within(dialog).getByRole("form", { name: "Add product" });
  await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "flour");
  await user.click(await within(form).findByRole("option", { name: /all-purpose flour/ }));
  await user.type(within(form).getByLabelText("Name"), "Flour");
  return { dialog, form };
}

describe("the product form asks EAN-8 or UPC-E (spec 03, 1H; criterion 87)", () => {
  it("asks for an ambiguous code, holds the save until answered, then sends the answer", async () => {
    const calls = mockApi({
      ...productRoutes(),
      "POST /products": () => jsonResponse(201, { ...flourProduct, barcode: AMBIGUOUS }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");
    const { dialog, form } = await openAddDrawer(user);

    expect(within(form).queryByRole("group", { name: QUESTION })).toBeNull();
    await user.type(within(form).getByLabelText("Barcode"), AMBIGUOUS);
    const question = within(form).getByRole("group", { name: QUESTION });

    await user.click(within(dialog).getByRole("button", { name: "Add product" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(REQUIRED);
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await user.click(within(question).getByRole("radio", { name: "UPC-E" }));
    await user.click(within(dialog).getByRole("button", { name: "Add product" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.path === "/products");
    expect(post?.body).toMatchObject({ barcode: AMBIGUOUS, barcode_symbology: "upce" });
  });

  it("forgets the answer when the code changes, and never asks for a code that reads one way", async () => {
    const calls = mockApi({
      ...productRoutes(),
      "POST /products": () => jsonResponse(201, { ...flourProduct, barcode: EAN8_ONLY }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");
    const { dialog, form } = await openAddDrawer(user);

    const field = within(form).getByLabelText("Barcode");
    await user.type(field, AMBIGUOUS);
    await user.click(within(form).getByRole("radio", { name: "EAN-8" }));
    await user.clear(field);
    await user.type(field, OTHER_AMBIGUOUS);
    expect(within(form).getByRole("radio", { name: "EAN-8" })).not.toBeChecked();

    await user.clear(field);
    await user.type(field, EAN8_ONLY);
    expect(within(form).queryByRole("group", { name: QUESTION })).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Add product" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.path === "/products");
    expect(post?.body).toMatchObject({ barcode: EAN8_ONLY });
    expect(post?.body).not.toHaveProperty("barcode_symbology");
  });

  it("doesn't ask about the saved code when editing, and asks for a new one", async () => {
    let product: Product = { ...flourProduct, barcode: AMBIGUOUS };
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, product),
      [`GET /products/${flourProductId}/prices`]: () =>
        jsonResponse(200, { points: [], latest: [] }),
      [`PATCH /products/${flourProductId}`]: () => {
        product = { ...product, barcode: OTHER_AMBIGUOUS, updated_at: "2026-03-03T00:00:00Z" };
        return jsonResponse(200, product);
      },
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);

    const form = await screen.findByRole("form", { name: "Edit product" });
    expect(within(form).getByLabelText("Barcode")).toHaveValue(AMBIGUOUS);
    expect(within(form).queryByRole("group", { name: QUESTION })).toBeNull();

    await user.clear(within(form).getByLabelText("Barcode"));
    await user.type(within(form).getByLabelText("Barcode"), OTHER_AMBIGUOUS);
    await user.click(within(form).getByRole("radio", { name: "EAN-8" }));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      barcode: OTHER_AMBIGUOUS,
      barcode_symbology: "ean8",
    });
  });
});

describe("a product made at the shelf asks too (spec 03, 1H; UI-4.7)", () => {
  it("asks before creating the product for an unknown ambiguous barcode", async () => {
    localStorage.setItem("kerp.lastVendorLocationId", chainLocationId);
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /inbox": () =>
        jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation] }),
      "GET /products/search": () => jsonResponse(200, { items: [] }),
      "GET /ingredients/search": () => jsonResponse(200, { items: [ingredientMatch(flour)] }),
      "GET /price-observations": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
      "POST /products": () => jsonResponse(201, { ...flourProduct, barcode: AMBIGUOUS }),
    });
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");

    await user.type(
      await screen.findByRole("combobox", { name: "Barcode or product name" }),
      AMBIGUOUS,
    );
    await user.click(await screen.findByRole("button", { name: "Create product" }));
    const group = screen.getByRole("group", { name: "New product" });
    await user.type(within(group).getByRole("combobox", { name: "Ingredient" }), "flour");
    await user.click(await within(group).findByRole("option", { name: /all-purpose flour/ }));
    await user.type(within(group).getByLabelText("Name"), "Flour");

    const question = within(group).getByRole("group", { name: QUESTION });
    await user.click(within(group).getByRole("button", { name: "Create product" }));
    expect(within(group).getByRole("alert")).toHaveTextContent(REQUIRED);
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await user.click(within(question).getByRole("radio", { name: "UPC-E" }));
    await user.click(within(group).getByRole("button", { name: "Create product" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
      barcode: AMBIGUOUS,
      barcode_symbology: "upce",
    });
  });
});
