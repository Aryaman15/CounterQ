import Link from "next/link";

export default function Home() {
  return (
    <main className="launcher-page">
      <section className="launcher-panel">
        <p className="launcher-kicker">CounterQ</p>
        <h1>CounterQ Interview Room</h1>
        <p>
          CounterQ observes your reasoning and code, waits for the moments that matter,
          and tests whether your decisions survive scrutiny.
        </p>
        <div className="launcher-actions">
          <Link className="launcher-link" href="/sign-up">Create account</Link>
          <Link className="launcher-link launcher-link-secondary" href="/sign-in">Sign in</Link>
          <Link className="launcher-link launcher-link-secondary" href="/onboarding">
            Continue to preferences
          </Link>
        </div>
        {process.env.NODE_ENV !== "production" ? (
          <Link className="launcher-development-link" href="/interview/demo">
            Open Interview Room Preview
          </Link>
        ) : null}
      </section>
    </main>
  );
}
