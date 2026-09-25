import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Drawer } from "../components/Drawer";

function Harness({ busy = false, onSubmit = vi.fn() }: { busy?: boolean; onSubmit?: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Add product
      </button>
      {open ? (
        <Drawer
          title="Add product"
          thing="product"
          dirty={name.trim() !== ""}
          onClose={() => {
            setOpen(false);
            setName("");
          }}
          formId="add-product"
          primaryLabel="Add product"
          busy={busy}
          busyLabel="Adding…"
        >
          <form
            id="add-product"
            onSubmit={(e) => {
              e.preventDefault();
              onSubmit();
            }}
          >
            <label htmlFor="name">Name</label>
            <input id="name" value={name} onChange={(e) => setName(e.target.value)} />
          </form>
        </Drawer>
      ) : null}
    </>
  );
}

async function openDrawer() {
  const user = userEvent.setup();
  render(<Harness />);
  const opener = screen.getByRole("button", { name: "Add product" });
  await user.click(opener);
  return { user, opener, drawer: screen.getByRole("dialog", { name: "Add product" }) };
}

describe("the Drawer (spec 08, UI-3.2, UI-3.3)", () => {
  it("opens with focus inside, and Escape closes it clean, returning focus", async () => {
    const { user, opener } = await openDrawer();
    expect(screen.getByLabelText("Name")).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("asks before discarding typed input, with focus on Keep editing (D5)", async () => {
    const { user } = await openDrawer();
    await user.type(screen.getByLabelText("Name"), "Oat milk");
    await user.keyboard("{Escape}");

    const bar = await screen.findByRole("alert");
    expect(bar).toHaveTextContent("Discard this product? What you typed will be lost.");
    expect(screen.getByRole("button", { name: "Keep editing" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Oat milk");
  });

  it("asks on Cancel and on a backdrop click too, and Discard closes", async () => {
    const { user, opener } = await openDrawer();
    await user.type(screen.getByLabelText("Name"), "Oat milk");

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep editing" }));

    await user.click(screen.getByTestId("drawer-backdrop"));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("keeps Tab inside the drawer", async () => {
    const { user, drawer } = await openDrawer();
    for (let i = 0; i < 5; i += 1) {
      await user.tab();
      expect(drawer.contains(document.activeElement)).toBe(true);
    }
  });

  it("submits its form from the footer, and disables the button while saving", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} />);
    await user.click(screen.getByRole("button", { name: "Add product" }));
    const dialog = screen.getByRole("dialog");
    await user.click(dialog.querySelector("footer button[type=submit]") as HTMLButtonElement);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("disables the primary button while saving, so a second click cannot submit twice", async () => {
    const user = userEvent.setup();
    render(<Harness busy />);
    await user.click(screen.getByRole("button", { name: "Add product" }));
    expect(screen.getByRole("button", { name: "Adding…" })).toBeDisabled();
  });
});
