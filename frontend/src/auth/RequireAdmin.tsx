import type { ReactNode } from "react";
import { NotPermittedPage } from "../pages/NotPermittedPage";
import { useCurrentUser } from "./context";

export function RequireAdmin({ children }: { children: ReactNode }) {
  const user = useCurrentUser();
  if (user.role !== "admin") {
    return <NotPermittedPage />;
  }
  return <>{children}</>;
}
