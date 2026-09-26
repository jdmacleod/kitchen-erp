import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReceiptImage } from "../components/purchases/ReceiptImage";

describe("the receipt image (#30)", () => {
  it("says it could not be shown rather than leaving an empty panel", () => {
    render(<ReceiptImage documentId="doc-1" alt="The receipt as photographed" />);
    const img = screen.getByRole("img", { name: "The receipt as photographed" });
    expect(img).toHaveAttribute("src", "/api/v1/receipts/doc-1/image");

    fireEvent.error(img);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("The receipt image couldn't be shown.");
  });

  it("asks for a scaled thumbnail, and says No preview when it fails", () => {
    render(<ReceiptImage documentId="doc-2" alt="Receipt" width={96} />);
    const img = screen.getByRole("img", { name: "Receipt" });
    expect(img).toHaveAttribute("src", "/api/v1/receipts/doc-2/image?width=96");
    fireEvent.error(img);
    expect(screen.getByRole("status")).toHaveTextContent("No preview");
  });

  it("tries again on request, and the fallback is never inside the link", () => {
    render(<ReceiptImage documentId="doc-3" alt="" width={96} link={{ label: "Open the receipt" }} />);
    expect(screen.getByRole("link", { name: "Open the receipt" })).toHaveAttribute("href", "/api/v1/receipts/doc-3/image");
    fireEvent.error(screen.getByRole("link").querySelector("img")!);

    expect(screen.queryByRole("link")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByRole("link", { name: "Open the receipt" }).querySelector("img")).toHaveAttribute("src", "/api/v1/receipts/doc-3/image?width=96");
  });
});
