import { useEffect, useRef, useState } from "react";
import { Button, Card, PageHeader, secondaryLinkClass } from "../../components/ui";
import { bookmarkletCode, bookmarkletHref } from "../../lib/bookmarklet";
import { usePageTitle } from "../../lib/usePageTitle";

/**
 * Settings → Capture (10; PD19): the "Save to Kitchen ERP" bookmarklet, tied to
 * this app's address, with a copy-code fallback for browsers that can't drag.
 */
export function CapturePage() {
  usePageTitle("Capture");
  const origin = window.location.origin;
  const link = useRef<HTMLAnchorElement>(null);
  const [copied, setCopied] = useState(false);

  // React refuses javascript: URLs in href, so the link gets its address here.
  useEffect(() => {
    link.current?.setAttribute("href", bookmarkletHref(origin));
  }, [origin]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`javascript:${bookmarkletCode(origin)}`);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <>
      <PageHeader title="Capture" description="Save a product from a store's web page with one click." />
      <Card className="flex flex-col gap-4">
        <div>
          {/* Secondary, not herb: clicking it here does nothing (10). */}
          <a ref={link} className={secondaryLinkClass} data-testid="bookmarklet" onClick={(e) => e.preventDefault()}>
            Save to Kitchen ERP
          </a>
        </div>
        <p className="text-sm">Drag the button above to your browser's bookmarks bar. On a store's product page, click the bookmark to save the product here.</p>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          It is tied to <span className="font-medium text-neutral-900 dark:text-neutral-100">{origin}</span>. Reinstall if this address changes.
        </p>
        <div className="flex flex-col gap-2">
          <p className="text-sm text-neutral-600 dark:text-neutral-400">Can't drag it (Safari on iPhone)? Copy the code, add any page as a bookmark, then edit that bookmark and paste the code as its address.</p>
          <span className="flex items-center gap-3">
            <Button variant="secondary" onClick={() => void copy()}>
              Copy the code
            </Button>
            {copied ? (
              <span role="status" className="text-sm">
                Copied.
              </span>
            ) : null}
          </span>
        </div>
      </Card>
    </>
  );
}
