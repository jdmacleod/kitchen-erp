import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import type { SearchHit } from "../api/catalog";
import { ProductTypeahead } from "../components/catalog/ProductTypeahead";
import { createQueryClient } from "../lib/queryClient";
import { hits } from "./catalog-fixtures";
import { jsonResponse, mockApi } from "./helpers";

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

});
