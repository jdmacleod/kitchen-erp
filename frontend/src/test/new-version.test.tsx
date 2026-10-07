import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { queryKeys } from "../api/queries";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

/** Health answers with each commit in turn, then keeps repeating the last. */
function mountWith(...commits: (string | undefined)[]) {
  let call = 0;
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => {
      const commit = commits[Math.min(call++, commits.length - 1)];
      return jsonResponse(200, { status: "ok", version: "dev", ...(commit === undefined ? {} : { commit }) });
    },
    "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
  });
  return renderApp("/shop/purchases");
}

async function pollHealthAgain(client: ReturnType<typeof renderApp>["client"]) {
  await act(() => client.refetchQueries({ queryKey: queryKeys.health }));
}

describe("new version notice (issue 209)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("offers a reload once the api reports a different commit", async () => {
    const { client } = mountWith("a1b2c3d", "e4f5a6b");
    await screen.findByTestId("build-identity");
    expect(screen.queryByTestId("new-version")).not.toBeInTheDocument();

    await pollHealthAgain(client);
    const notice = await screen.findByTestId("new-version");
    expect(notice).toHaveTextContent("A new version is ready.");

    const reload = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload });
    await userEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it("stays quiet while the commit is unchanged", async () => {
    const { client } = mountWith("a1b2c3d");
    await screen.findByTestId("build-identity");
    await pollHealthAgain(client);
    await pollHealthAgain(client);
    expect(screen.queryByTestId("new-version")).not.toBeInTheDocument();
  });

  it("says nothing for a dev build or an api without a commit", async () => {
    const { client } = mountWith("unknown", undefined, "a1b2c3d");
    await waitFor(() => expect(screen.getByRole("main")).toBeInTheDocument());
    await pollHealthAgain(client);
    await pollHealthAgain(client);
    // The first real commit is where the tab starts counting from, not a change.
    expect(screen.queryByTestId("new-version")).not.toBeInTheDocument();
  });

  it("can be put off until the next deploy", async () => {
    const { client } = mountWith("a1b2c3d", "e4f5a6b", "e4f5a6b", "c7d8e9f");
    await screen.findByTestId("build-identity");
    await pollHealthAgain(client);
    await userEvent.click(await screen.findByRole("button", { name: "Not now" }));
    expect(screen.queryByTestId("new-version")).not.toBeInTheDocument();

    await pollHealthAgain(client);
    expect(screen.queryByTestId("new-version")).not.toBeInTheDocument();
    await pollHealthAgain(client);
    expect(await screen.findByTestId("new-version")).toBeInTheDocument();
  });
});
