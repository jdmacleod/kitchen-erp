import { useEffect } from "react";

export function usePageTitle(title: string): void {
  useEffect(() => {
    const previous = document.title;
    document.title = `${title} · Kitchen ERP`;
    return () => {
      document.title = previous;
    };
  }, [title]);
}
