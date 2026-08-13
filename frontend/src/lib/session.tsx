import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Me } from "./api";

type State = { status: "loading" } | { status: "signedIn"; me: Me } | { status: "signedOut" };

const SessionContext = createContext<State>({ status: "loading" });

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    api
      .me()
      .then((me) => setState({ status: "signedIn", me }))
      .catch(() => setState({ status: "signedOut" }));
  }, []);

  return <SessionContext.Provider value={state}>{children}</SessionContext.Provider>;
}

export const useSession = () => useContext(SessionContext);
