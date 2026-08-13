/** Tabs that exist in the shell but are not built yet.
 *
 * Shown rather than hidden on purpose: a visible "not yet" is honest, where a
 * missing tab reads as a bug. */
export function Placeholder({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      <p>{body}</p>
    </div>
  );
}
