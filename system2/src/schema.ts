import { z } from "zod";

// Mirrors ReviewRequest / ReviewResponse in src/schemas.py. Python stays the
// authority: it re-checks every verdict this service returns.

export const PropositionSchema = z.object({
  id: z.string().min(1),
  text: z.string().min(1),
  stakes: z.enum(["high", "standard"]),
  jev_probability: z.number().min(0).max(1),
});

export const ReviewRequestSchema = z.object({
  document: z.string().min(1).max(100_000),
  metadata: z.record(z.string(), z.string()).default({}),
  propositions: z.array(PropositionSchema).min(1).max(32),
});

export const VerdictSchema = z.object({
  id: z.string().describe("The proposition ID, copied exactly."),
  verdict: z.enum(["yes", "no", "cannot_determine"]),
  evidence: z
    .string()
    .describe("For yes: the exact words from the document that make the proposition true, copied verbatim. Otherwise an empty string."),
  rationale: z.string().describe("One sentence explaining the verdict."),
});

export const ReviewOutputSchema = z.object({
  verdicts: z.array(VerdictSchema),
});

export type Proposition = z.infer<typeof PropositionSchema>;
export type ReviewRequest = z.infer<typeof ReviewRequestSchema>;
export type ReviewOutput = z.infer<typeof ReviewOutputSchema>;
export type ReviewResponse = ReviewOutput & { model: string };
