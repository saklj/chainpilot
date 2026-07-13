import { z } from "zod";

export const HealthResponseSchema = z.object({
  status: z.literal("ok"),
  service: z.literal("chainpilot-api"),
  version: z.string(),
});

export type HealthResponse = z.infer<typeof HealthResponseSchema>;
