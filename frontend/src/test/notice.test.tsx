import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NoticeProvider, useNavigateWithNotice, useNotice, type NoticeData } from "../components/Notice";

const saved: NoticeData = { tone: "success", message: "Saved $3.49 at Pier Stand", timeoutMs: 3000 };
const added: NoticeData = { tone: "success", message: "Added Rolled Oats.", action: { label: "Open it", to: "/c" }, focusAction: true };

function PageA() {
  const go = useNavigateWithNotice();
  const notice = useNotice();
  const navigate = useNavigate();
  return (
    <>
      <h1>Page A</h1>
      <button type="button" onClick={() => navigate(1)}>Forward</button>
      <button type="button" onClick={() => go("/b", added)}>Go with notice</button>
      <button type="button" onClick={() => notice.show(saved)}>Save here</button>
      <button type="button" onClick={() => notice.show({ tone: "info", message: "Second" })}>Show another</button>
    </>
  );
}

function PageB() {
  const navigate = useNavigate();
  return (
    <>
      <h1>Page B</h1>
      <button type="button" onClick={() => navigate(-1)}>Back</button>
    </>
  );
}

function PageC() {
  return <h1>Page C</h1>;
}

function renderPages() {
  return render(
    <MemoryRouter initialEntries={["/a"]}>
      <NoticeProvider>
        <Routes>
          <Route path="/a" element={<PageA />} />
          <Route path="/b" element={<PageB />} />
          <Route path="/c" element={<PageC />} />
        </Routes>
      </NoticeProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.useRealTimers());

describe("the Notice (D18, UI-2.16)", () => {
  it("carries a confirmation across a navigation, as a status with a focused action", async () => {
    const user = userEvent.setup();
    renderPages();
    await user.click(screen.getByRole("button", { name: "Go with notice" }));

    expect(screen.getByRole("heading", { name: "Page B" })).toBeInTheDocument();
    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("Added Rolled Oats.");
    const action = within(notice).getByRole("link", { name: "Open it" });
    expect(action).toHaveAttribute("href", "/c");
    expect(action).toHaveFocus();
  });

  it("does not come back on Back and Forward", async () => {
    const user = userEvent.setup();
    renderPages();
    await user.click(screen.getByRole("button", { name: "Go with notice" }));
    expect(screen.getByRole("status")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("heading", { name: "Page A" })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // Forward to the entry the notice arrived on: it was consumed there.
    await user.click(screen.getByRole("button", { name: "Forward" }));
    expect(screen.getByRole("heading", { name: "Page B" })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows one at a time, and leaves when dismissed", async () => {
    const user = userEvent.setup();
    renderPages();
    await user.click(screen.getByRole("button", { name: "Save here" }));
    await user.click(screen.getByRole("button", { name: "Show another" }));
    expect(screen.getAllByTestId("notice")).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Second");

    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("clears a timed notice after its time, and is announced politely", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderPages();
    expect(screen.getByTestId("notice-region")).toHaveAttribute("aria-live", "polite");
    await user.click(screen.getByRole("button", { name: "Save here" }));
    expect(screen.getByRole("status")).toHaveTextContent("Saved $3.49 at Pier Stand");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2900);
    });
    expect(screen.getByRole("status")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
