import { notFound } from "next/navigation";

import { DevelopmentAuthCheck } from "@/features/auth/DevelopmentAuthCheck";

export default function DevelopmentAuthCheckPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <DevelopmentAuthCheck />;
}
