import { clerkMiddleware } from "@clerk/nextjs/server";

function isPublicPath(pathname: string): boolean {
  return pathname === "/"
    || pathname.startsWith("/sign-in")
    || pathname.startsWith("/sign-up");
}

function isDevelopmentDemo(pathname: string): boolean {
  return process.env.NODE_ENV !== "production" && (
    pathname === "/interview/demo"
    || pathname === "/countermap/demo"
    || pathname === "/mastery/demo"
    || pathname === "/retests/demo"
  );
}

export default clerkMiddleware(async (auth, request) => {
  const pathname = request.nextUrl.pathname;
  if (pathname === "/development/auth-check") {
    if (process.env.NODE_ENV === "production") {
      return new Response("Not Found", { status: 404 });
    }
    return;
  }
  if (!isPublicPath(pathname) && !isDevelopmentDemo(pathname)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/(.*)",
  ],
};
