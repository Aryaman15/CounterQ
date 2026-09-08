import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <main className="auth-page">
      <section className="auth-context" aria-labelledby="sign-in-title">
        <p className="launcher-kicker">CounterQ account</p>
        <h1 id="sign-in-title">Return to the reasoning you are building.</h1>
        <p>Your interview evidence and preferences remain attached to your CounterQ account.</p>
      </section>
      <SignIn path="/sign-in" routing="path" signUpUrl="/sign-up" forceRedirectUrl="/onboarding" />
    </main>
  );
}
