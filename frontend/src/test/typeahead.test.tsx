import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import type { SearchHit } from "../api/catalog";
import { ProductTypeahead } from "../components/catalog/ProductTypeahead";
import { createQueryClient } from "../lib/queryClient";
import { hits } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

describe("ProductTypeahead", () => {
  it("lists ranked hits as the user types and selects with the keyboard", async () => {
    const calls = mockApi({
      "GET /products/search": (call) =>
        jsonResponse(200, { items: call.query.get("q") === "flour" ? hits : [] }),
    });
    const onSelect = vi.fn<(hit: SearchHit) => void>();
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={createQueryClient({ retry: false })}>
        <ProductTypeahead id="pick" onSelect={onSelect} />
      </QueryClientProvider>,
    );

    const box = screen.getByRole("combobox", { name: "Search products" });
    expect(box).toHaveAttribute("aria-expanded", "false");
    await user.type(box, "flour");

    const list = await screen.findByRole("listbox", { name: "Products" });
    const options = await within(list).findAllByRole("option");
    expect(options).toHaveLength(2);
    expect(options[0]).toHaveTextContent("All-Purpose Flour");
    expect(options[0]).toHaveTextContent("Millstone");
    expect(options[0]).toHaveTextContent("5 lb");
    expect(options[0]).toHaveTextContent("all-purpose flour");
    expect(options[0]).toHaveTextContent("name");
    expect(options[1]).toHaveTextContent("ingredient");
    expect(box).toHaveAttribute("aria-expanded", "true");

    // Nothing is chosen without a deliberate key.
    expect(box).not.toHaveAttribute("aria-activedescendant");
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(options[1]).toHaveAttribute("aria-selected", "true");
    expect(box).toHaveAttribute("aria-activedescendant", options[1].id);
    await user.keyboard("{Enter}");

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].id).toBe(hits[1].id);
    expect(box).toHaveValue("");
    await waitFor(() => expect(box).toHaveAttribute("aria-expanded", "false"));
    // Every call sent the query text and a limit.
    expect(calls.every((c) => c.path.startsWith("/products/search?q=") && c.query.get("limit") === "10")).toBe(true);
  });

  it("closes on Escape without selecting", async () => {
    mockApi({ "GET /products/search": () => jsonResponse(200, { items: hits }) });
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={createQueryClient({ retry: false })}>
        <ProductTypeahead id="pick" onSelect={onSelect} />
      </QueryClientProvider>,
    );
    const box = screen.getByRole("combobox", { name: "Search products" });
    await user.type(box, "fl");
    await screen.findAllByRole("option");
    await user.keyboard("{ArrowDown}{Escape}");
    await waitFor(() => expect(box).toHaveAttribute("aria-expanded", "false"));
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();
    expect(box).toHaveValue("fl");
  });

  it("opens the chosen product from the products page", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: [] }),
      "GET /products": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /products/search": () => jsonResponse(200, { items: hits }),
      [`GET /products/${hits[0].id}`]: () =>
        jsonResponse(200, {
          id: hits[0].id,
          ingredient: hits[0].ingredient,
          brand: hits[0].brand,
          name: hits[0].name,
          pack_qty: hits[0].pack_qty,
          pack_unit: hits[0].pack_unit,
          barcode: hits[0].barcode,
          quality_rating: hits[0].quality_rating,
          exclusive_vendor_id: null,
          density_override: null,
          density_override_source: null,
          density_override_confirmed: false,
          active: true,
          notes: null,
          created_at: "2026-03-02T00:00:00Z",
          updated_at: "2026-03-02T00:00:00Z",
        }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");
    await user.type(await screen.findByRole("combobox", { name: "Search products" }), "flour");
    await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));
    expect(await screen.findByRole("heading", { name: "Millstone All-Purpose Flour" })).toBeInTheDocument();
  });
});
