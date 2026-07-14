"use client";

import { usePathname } from "next/navigation";

import { SidebarTrigger } from "@/components/ui/sidebar";

const titles: Record<string, string> = {
  "/": "供应风险看板",
  "/chat": "Chat 问数",
  "/report": "供应风险周报",
};

export function Topbar() {
  const pathname = usePathname();

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-background px-4">
      <SidebarTrigger />
      <div className="h-4 w-px bg-border" aria-hidden="true" />
      <p className="text-sm font-medium text-foreground">{titles[pathname] ?? "ChainPilot"}</p>
    </header>
  );
}
