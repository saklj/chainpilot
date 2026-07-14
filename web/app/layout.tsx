import type { Metadata } from "next";

import "./globals.css";

import { AppShell } from "@/components/layout/app-shell";

export const metadata: Metadata = {
  title: "ChainPilot",
  description: "Supply chain intelligence agent",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" className="dark font-sans">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
