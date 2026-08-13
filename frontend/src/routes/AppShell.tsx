import { Link, NavLink, Outlet } from "react-router-dom";
import { useSession, useSignOut } from "../lib/session";

/** Persistent chrome. Rendered once, above every route, so nothing here ever
 *  unmounts or scrolls away. Route children own their own scroll containers -
 *  the document itself never scrolls, which is what keeps this pinned without
 *  position: sticky. */
export function AppShell() {
  const session = useSession();
  const signOut = useSignOut();

  if (session.status === "loading") {
    return <div className="shell shell--centered">Loading</div>;
  }

  if (session.status === "signedOut") {
    return (
      <div className="shell shell--centered">
        <h1 className="wordmark wordmark--large">Augur</h1>
        <p className="lede">Reads the signs. Remembers the telling.</p>
        <a className="btn btn--go" href="/api/auth/login">
          Sign in
        </a>
      </div>
    );
  }

  return (
    <div className="shell">
      <header className="globalnav">
        <Link to="/" className="wordmark">
          Augur
        </Link>
        <nav aria-label="Global" className="globalnav__nav">
          <NavLink to="/" end className="tab">
            Campaigns
          </NavLink>
        </nav>
        <div className="globalnav__meta">
          <span className="globalnav__who">
            {session.me.displayName ?? session.me.email ?? "signed in"}
          </span>
          <button className="btn" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>
      <Outlet />
    </div>
  );
}
