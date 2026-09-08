"use client";

import { ClerkProvider } from "@clerk/nextjs";

export function CounterQAuthProvider({ children }: { children: React.ReactNode }) {
  return <ClerkProvider>{children}</ClerkProvider>;
}
