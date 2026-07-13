"use client";

import { useEffect, useState } from "react";

import { HealthResponseSchema, type HealthResponse } from "@/lib/schemas";

type HealthState =
  | { status: "checking" }
  | { status: "ok"; health: HealthResponse }
  | { status: "unreachable" };

export default function Home() {
  const [healthState, setHealthState] = useState<HealthState>({ status: "checking" });

  useEffect(() => {
    const controller = new AbortController();

    async function checkHealth(): Promise<void> {
      try {
        const response = await fetch("/api/health", { signal: controller.signal });

        if (!response.ok) {
          throw new Error(`Health check failed with status ${response.status}`);
        }

        const health = HealthResponseSchema.parse(await response.json());
        setHealthState({ status: "ok", health });
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }

        setHealthState({ status: "unreachable" });
      }
    }

    void checkHealth();

    return () => controller.abort();
  }, []);

  const isHealthy = healthState.status === "ok";
  const label =
    healthState.status === "checking" ? "checking" : isHealthy ? healthState.health.status : "unreachable";
  const dotColor =
    healthState.status === "checking"
      ? "bg-slate-500"
      : isHealthy
        ? "bg-emerald-400"
        : "bg-red-400";

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 text-slate-100">
      <section className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-10 shadow-2xl">
        <p className="mb-3 text-sm font-medium uppercase tracking-[0.28em] text-cyan-400">
          Supply chain intelligence
        </p>
        <h1 className="text-4xl font-semibold tracking-tight">ChainPilot</h1>

        <div className="mt-10 flex items-center justify-between rounded-xl bg-slate-950/70 px-5 py-4">
          <span className="text-sm text-slate-400">Backend status</span>
          <span className="flex items-center gap-2 font-mono text-sm">
            <span
              aria-hidden="true"
              className={`h-2.5 w-2.5 rounded-full ${dotColor}`}
            />
            {label}
          </span>
        </div>
      </section>
    </main>
  );
}
