import { useEffect, useState } from "react";
import { useDebouncedValue } from "./useDebouncedValue";

/**
 * A search field whose text is kept in the address as `?q=`.
 *
 * The URL takes only text the debounce has settled on, so an old pending value
 * can't be written back over a newer one. The field follows `q` when it changes
 * from outside (Back, a link, Clear search), but not when `q` arrives at the
 * value this field just wrote: by the time that write renders, the person may
 * have typed another letter, and following it would throw the letter away
 * (issue 401).
 */
export function useSearchText(
  q: string,
  write: (q: string | null) => void,
  delayMs = 250,
): [string, (text: string) => void] {
  const [text, setText] = useState(q);
  const debounced = useDebouncedValue(text.trim(), delayMs);
  const [seenQ, setSeenQ] = useState(q);
  if (q !== seenQ) {
    setSeenQ(q);
    if (q !== debounced) setText(q);
  }
  useEffect(() => {
    if (debounced === text.trim() && debounced !== q) write(debounced || null);
    // Only the typed text drives the URL; q changing on its own is read above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);
  return [text, setText];
}
