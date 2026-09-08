import { SignUp } from "@clerk/nextjs";

export default function SignUpPage() {
  return (
    <main className="auth-page">
      <section className="auth-context" aria-labelledby="sign-up-title">
        <p className="launcher-kicker">CounterQ account</p>
        <h1 id="sign-up-title">Set up a private interview workspace.</h1>
        <p>CounterQ uses managed sign-in while keeping interview ownership in its own backend.</p>
      </section>
      <SignUp path="/sign-up" routing="path" signInUrl="/sign-in" forceRedirectUrl="/onboarding" />
    </main>
  );
}
