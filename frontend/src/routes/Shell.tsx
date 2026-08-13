import { Outlet } from "react-router-dom";
import { useSession } from "../lib/session";
import { api } from "../lib/api";

export function Shell() {
  const session = useSession();

  if (session.status === "loading") return <main className="shell">Loading</main>;

  if (session.status === "signedOut") {
    return (
      <main className="shell shell--centered">
        <h1 className="wordmark">Augur</h1>
        <p className="lede">Reads the signs. Remembers the telling.</p>
        <a className="button" href="/api/auth/login">
          Sign in
        </a>
      </main>
    );
  }

  return (
    <div className="shell">
      <header className="topbar">
        <span className="wordmark wordmark--small">Augur</span>
        <button
          className="button button--quiet"
          onClick={() => api.logout().then(() => location.reload())}
        >
          Sign out
        </button>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  );
}
