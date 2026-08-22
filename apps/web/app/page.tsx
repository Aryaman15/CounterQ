export default function Home() {
  return (
    <main className="min-h-screen px-6 py-10">
      <section className="mx-auto flex max-w-4xl flex-col gap-6">
        <div>
          <p className="text-sm font-medium uppercase tracking-wide text-neutral-600">Stage 0</p>
          <h1 className="mt-2 text-4xl font-semibold text-neutral-950">CounterQ repository foundation</h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-neutral-700">
            The local frontend shell is ready for future vertical slices. Product interview behavior starts in later stages.
          </p>
        </div>
        <div className="grid gap-3 text-sm text-neutral-800 sm:grid-cols-3">
          <div className="border border-neutral-300 bg-white p-4">Next.js + TypeScript</div>
          <div className="border border-neutral-300 bg-white p-4">Generated API contracts</div>
          <div className="border border-neutral-300 bg-white p-4">FastAPI health check</div>
        </div>
      </section>
    </main>
  );
}

