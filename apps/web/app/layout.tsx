import type { Metadata } from "next";

import { CounterQAuthProvider } from "@/features/auth/CounterQAuthProvider";

import "./globals.css";

export const metadata: Metadata = {
  title: "CounterQ",
  description: "Adaptive technical interview practice with evidence-backed memory.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <CounterQAuthProvider>{children}</CounterQAuthProvider>
      </body>
    </html>
  );
}
