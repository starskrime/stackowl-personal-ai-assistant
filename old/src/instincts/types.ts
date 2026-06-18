export interface InstinctSpec {
  name: string;
  description: string;
  constraint: string;
  owlName: string;
  /** Optional keyword triggers for heuristic matching (0ms, no LLM) */
  keywords?: string[];
}
