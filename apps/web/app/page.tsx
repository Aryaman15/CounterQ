import Link from "next/link";

export default function Home() {
  return (
    <main className="launcher-page">
      <section className="launcher-panel">
        <p className="launcher-kicker">Stage 1.3 preview</p>
        <h1>CounterQ Interview Room</h1>
        <p>
          Open the deterministic visual development room for the first polished Monaco and interviewer-surface review.
        </p>
        <Link className="launcher-link" href="/interview/demo">
          Open Interview Room Preview
        </Link>
      </section>
    </main>
  );
}
