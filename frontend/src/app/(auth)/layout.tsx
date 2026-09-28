import Link from "next/link";

import { BrandMark } from "@/components/brand-mark";

/** Sign-in and invites: one narrow column, no app shell. */
export default function AuthLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-sm flex-col px-6">
      <header className="py-6">
        <Link href="/" className="flex items-center gap-2.5 font-medium">
          <BrandMark className="size-7" />
          <span>Tamga</span>
        </Link>
      </header>
      <main className="flex flex-1 flex-col justify-center pb-[18vh]">{children}</main>
    </div>
  );
}
