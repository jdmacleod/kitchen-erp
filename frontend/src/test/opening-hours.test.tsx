import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { OpeningHoursInput } from "../components/geo/OpeningHoursInput";
import { describeOpeningHours, presetToString } from "../lib/openingHours";
import { jsonResponse, mockApi } from "./helpers";

function Harness() {
  const [value, setValue] = useState("");
  return (
    <form>
      <OpeningHoursInput idPrefix="t" value={value} onChange={setValue} />
      <output data-testid="value">{value}</output>
      <button type="button">elsewhere</button>
    </form>
  );
}

describe("opening hours", () => {
  it("presets write OpenStreetMap syntax", () => {
    expect(presetToString({ kind: "daily", days: "Mo-Su", open: "08:00", close: "20:00" })).toBe("Mo-Su 08:00-20:00");
    expect(presetToString({ kind: "weekly", day: "Sa", open: "08:00", close: "13:00" })).toBe("Sa 08:00-13:00");
    expect(presetToString({ kind: "seasonal", from: "Apr", to: "Oct", day: "Sa", open: "08:00", close: "13:00" })).toBe("Apr-Oct Sa 08:00-13:00");
  });

  it("describes stored strings readably and leaves the unknown alone", () => {
    expect(describeOpeningHours("Mo-Fr 08:00-21:00; Sa,Su 09:00-20:00")).toBe("Monday–Friday 08:00–21:00; Saturday, Sunday 09:00–20:00");
    expect(describeOpeningHours("Apr-Oct Sa 08:00-13:00")).toBe("April–October Saturday 08:00–13:00");
    expect(describeOpeningHours("Mo-Su 07:00-22:00")).toBe("every day 07:00–22:00");
    expect(describeOpeningHours("24/7")).toBe("Open 24 hours, every day");
    expect(describeOpeningHours("PH off")).toBe("public holidays closed");
    expect(describeOpeningHours(null)).toBe("Hours unknown");
    expect(describeOpeningHours("week 1-10 Mo 08:00-12:00")).toBe("week 1-10 Monday 08:00–12:00");
  });

  it("the builder writes the three presets and validates raw text on blur", async () => {
    const calls = mockApi({
      "POST /opening-hours/validate": (call) => {
        const text = (call.body as { text: string }).text;
        return jsonResponse(200, text.includes("Xx") ? { valid: false, error: "Unknown day 'Xx' at position 1." } : { valid: true, error: null });
      },
    });
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("radio", { name: "Daily hours" }));
    expect(screen.getByTestId("value")).toHaveTextContent("Mo-Su 08:00-20:00");
    await user.selectOptions(screen.getByLabelText("Days"), "Mo-Fr");
    expect(screen.getByTestId("value")).toHaveTextContent("Mo-Fr 08:00-20:00");

    await user.click(screen.getByRole("radio", { name: "Weekly market" }));
    expect(screen.getByTestId("value")).toHaveTextContent("Sa 08:00-13:00");

    await user.click(screen.getByRole("radio", { name: "Seasonal" }));
    expect(screen.getByTestId("value")).toHaveTextContent("Apr-Oct Sa 08:00-13:00");
    await user.selectOptions(screen.getByLabelText("From"), "May");
    expect(screen.getByTestId("value")).toHaveTextContent("May-Oct Sa 08:00-13:00");
    expect(screen.getByText(/May–October Saturday 08:00–13:00/)).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "Raw" }));
    const raw = screen.getByLabelText("opening_hours");
    expect(raw).toHaveValue("May-Oct Sa 08:00-13:00");
    await user.clear(raw);
    await user.type(raw, "Xx 08:00-13:00");
    await user.click(screen.getByRole("button", { name: "elsewhere" }));

    await waitFor(() => expect(screen.getByText("Unknown day 'Xx' at position 1.")).toBeInTheDocument());
    const post = calls.find((c) => c.path === "/opening-hours/validate");
    expect(post?.body).toEqual({ text: "Xx 08:00-13:00" });

    await user.click(screen.getByRole("radio", { name: "Not set" }));
    expect(screen.getByTestId("value")).toHaveTextContent("");
  });
});
