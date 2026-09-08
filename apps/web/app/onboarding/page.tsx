import { auth } from "@clerk/nextjs/server";

import { OnboardingExperience } from "@/features/auth/OnboardingExperience";

export default async function OnboardingPage() {
  const { isAuthenticated, redirectToSignIn } = await auth();
  if (!isAuthenticated) return redirectToSignIn({ returnBackUrl: "/onboarding" });
  return <OnboardingExperience />;
}
