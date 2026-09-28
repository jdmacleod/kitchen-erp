import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { User } from "../api/types";
import { adminUser, errorResponse, jsonResponse, memberUser, mockApi, renderApp } from "./helpers";

// #75: members can be edited, deactivated and given a new password, and
// anyone can change their own password.
function usersRoutes(state: { member: User }) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /users": () => jsonResponse(200, { items: [adminUser, state.member] }),
    [`PATCH /users/${memberUser.id}`]: (call: { body: unknown }) => {
      state.member = { ...state.member, ...(call.body as Partial<User>) };
      return jsonResponse(200, state.member);
    },
    [`POST /users/${memberUser.id}/password`]: () => jsonResponse(200, state.member),
  };
}

describe("household members", () => {
  it("edits a member's name and role from a drawer", async () => {
    const calls = mockApi(usersRoutes({ member: memberUser }));
    const user = userEvent.setup();
    renderApp("/settings/users");

    await user.click(await screen.findByRole("button", { name: "Edit Member Example" }));
    const drawer = screen.getByRole("dialog", { name: "Edit Member Example" });
    const name = within(drawer).getByLabelText("Display name");
    await user.clear(name);
    await user.type(name, "Sam");
    await user.click(within(drawer).getByLabelText(/^Admin/));
    await user.click(within(drawer).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ display_name: "Sam", role: "admin" }));
    expect(await screen.findByText("Saved Sam.")).toBeInTheDocument();
  });

  it("deactivates a member only after saying what it does", async () => {
    const calls = mockApi(usersRoutes({ member: memberUser }));
    const user = userEvent.setup();
    renderApp("/settings/users");

    await user.click(await screen.findByRole("button", { name: "Edit Member Example" }));
    const drawer = screen.getByRole("dialog", { name: "Edit Member Example" });
    await user.click(within(drawer).getByRole("button", { name: "Deactivate Member Example" }));
    expect(within(drawer).getByText(/signed out everywhere, and their API tokens stop working/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    await user.click(within(drawer).getByRole("button", { name: "Deactivate" }));

    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ active: false }));
    expect(await screen.findByText("Member Example is deactivated and signed out everywhere.")).toBeInTheDocument();
  });

  it("sets a new password for a member", async () => {
    const calls = mockApi(usersRoutes({ member: memberUser }));
    const user = userEvent.setup();
    renderApp("/settings/users");

    await user.click(await screen.findByRole("button", { name: "Edit Member Example" }));
    const drawer = screen.getByRole("dialog", { name: "Edit Member Example" });
    const set = within(drawer).getByRole("button", { name: "Set password" });
    await user.type(within(drawer).getByLabelText("Set a new password"), "short");
    expect(set).toBeDisabled();
    await user.type(within(drawer).getByLabelText("Set a new password"), "-but-longer");
    await user.click(set);
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path.endsWith("/password"))?.body).toEqual({ password: "short-but-longer" }));
  });

  it("does not offer to remove your own admin access or set your own password", async () => {
    mockApi(usersRoutes({ member: memberUser }));
    const user = userEvent.setup();
    renderApp("/settings/users");

    await user.click(await screen.findByRole("button", { name: "Edit Admin Example" }));
    const drawer = screen.getByRole("dialog", { name: "Edit Admin Example" });
    expect(within(drawer).getByLabelText("Member")).toBeDisabled();
    expect(within(drawer).queryByRole("button", { name: /^Deactivate/ })).not.toBeInTheDocument();
    expect(within(drawer).queryByLabelText("Set a new password")).not.toBeInTheDocument();
    expect(within(drawer).getByRole("link", { name: "Settings, System" })).toHaveAttribute("href", "/settings/system");
  });
});

describe("changing your own password", () => {
  function systemRoutes(reply: () => Response) {
    return {
      "GET /auth/me": () => jsonResponse(200, memberUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "POST /auth/password": reply,
    };
  }

  it("checks the two new passwords match before sending", async () => {
    const calls = mockApi(systemRoutes(() => jsonResponse(204)));
    const user = userEvent.setup();
    renderApp("/settings/system");

    await user.type(await screen.findByLabelText("Current password"), "old-secret");
    await user.type(screen.getByLabelText("New password"), "new-secret-1");
    await user.type(screen.getByLabelText("New password again"), "new-secret-2");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(screen.getByText("The two new passwords don't match.")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/auth/password")).toBe(false);

    await user.clear(screen.getByLabelText("New password again"));
    await user.type(screen.getByLabelText("New password again"), "new-secret-1");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/auth/password")?.body).toEqual({ current_password: "old-secret", new_password: "new-secret-1" }));
    expect(await screen.findByText("Password changed. You're signed out on your other devices.")).toBeInTheDocument();
  });

  it("says when the current password is wrong", async () => {
    mockApi(systemRoutes(() => errorResponse(422, "wrong_password", "The current password is not right.")));
    const user = userEvent.setup();
    renderApp("/settings/system");

    await user.type(await screen.findByLabelText("Current password"), "not-it");
    await user.type(screen.getByLabelText("New password"), "new-secret-1");
    await user.type(screen.getByLabelText("New password again"), "new-secret-1");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The current password is not right.");
  });
});
