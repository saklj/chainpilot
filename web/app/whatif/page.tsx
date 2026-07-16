import { AlertTriangle } from "lucide-react";

import { WhatIfWorkspace } from "@/components/whatif/whatif-workspace";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getWhatIfSuppliers } from "@/lib/api";
import type { WhatIfSupplier } from "@/lib/schemas";

type WhatIfPageState =
  | { status: "ready"; suppliers: WhatIfSupplier[] }
  | { status: "error"; message: string };

async function loadWhatIfPage(): Promise<WhatIfPageState> {
  try {
    return { status: "ready", suppliers: await getWhatIfSuppliers() };
  } catch (error) {
    return {
      status: "error",
      message: error instanceof Error ? error.message : "未知错误",
    };
  }
}

export default async function WhatIfPage() {
  const state = await loadWhatIfPage();
  if (state.status === "error") {
    return (
      <section className="mx-auto w-full max-w-7xl">
        <Card className="border border-border bg-card ring-0">
          <CardHeader>
            <CardTitle className="flex items-center gap-3">
              <AlertTriangle className="size-5 text-muted-foreground" aria-hidden="true" />
              暂时无法加载 What-if 模拟
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>请确认 ChainPilot API 已启动且供应风险数据可用，然后刷新页面。</p>
            <p className="font-mono text-xs text-foreground/70">{state.message}</p>
          </CardContent>
        </Card>
      </section>
    );
  }
  return <WhatIfWorkspace suppliers={state.suppliers} />;
}
