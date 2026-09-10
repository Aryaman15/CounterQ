"use client";

import { UserButton } from "@clerk/nextjs";
import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import type { CurrentUserResponse } from "@/lib/counterq-api";

const navigation = [
  { href: "/", label: "Home" },
  { href: "/history", label: "Interviews" },
  { href: "/mastery", label: "Mastery" },
];

export function ProductShell({
  me,
  children,
}: {
  me: CurrentUserResponse;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  return (
    <div className="product-shell">
      <header className="product-header">
        <Link className="product-wordmark" href="/" aria-label="CounterQ home">
          <span aria-hidden="true">CQ</span>
          <strong>CounterQ</strong>
        </Link>
        <nav className="product-navigation" aria-label="Primary navigation">
          {navigation.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isCurrentPath(pathname, item.href) ? "page" : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="product-account-controls">
          <Link className="product-new-interview" href="/interview/setup">
            New interview <ArrowUpRight size={15} aria-hidden="true" />
          </Link>
          <Link
            className="product-account-link"
            href="/account"
            aria-current={pathname === "/account" ? "page" : undefined}
          >
            Account
          </Link>
          <UserButton
            appearance={{ elements: { avatarBox: "counterq-user-avatar" } }}
            fallback={me.profile?.display_name ?? "Account"}
          />
        </div>
      </header>
      <main className="product-main">{children}</main>
    </div>
  );
}

function isCurrentPath(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}
