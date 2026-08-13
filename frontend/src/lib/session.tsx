import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Me } from "./api";

type State =
  | { status: "loading" }
  | { status: "signedIn"; me: Me }
  | { status: "signedOut" };

interface SessionValue {
  state: State;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue>({
  state: { status: "loading" },
  signOut: async () => {},
});

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    api
      .me()
      .then((me) => setState({ status: "signedIn", me }))
      .catch(() => setState({ status: "signedOut" }));
  }, []);

  /** Clears the cookie and drops to the signed-out screen without reloading.
   *  A reload would refetch /me, take a 401, and - before this change - bounce
   *  through the IdP and sign straight back in. */
  const signOut = async () => {
    try {
      await api.logout();
    } finally {
      setState({ status: "signedOut" });
    }
  };

  return (
    <SessionContext.Provider value={{ state, signOut }}>
      {children}
    </SessionContext.Provider>
  );
}

export const useSession = () => useContext(SessionContext).state;
export const useSignOut = () => useContext(SessionContext).signOut;
