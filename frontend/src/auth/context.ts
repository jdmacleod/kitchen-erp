import { createContext, useContext } from "react";
import type { User } from "../api/types";

export const CurrentUserContext = createContext<User | null>(null);

/** The signed-in user. Only valid beneath RequireAuth. */
export function useCurrentUser(): User {
  const user = useContext(CurrentUserContext);
  if (!user) {
    throw new Error("useCurrentUser must be used beneath RequireAuth");
  }
  return user;
}
