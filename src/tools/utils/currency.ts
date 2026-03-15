import type { ToolImplementation, ToolContext } from "../registry.js";

export const CurrencyConverterTool: ToolImplementation = {
  definition: {
    name: "convert_currency",
    description:
      "Convert between currencies using live exchange rates. No API key needed.",
    parameters: {
      type: "object",
      properties: {
        amount: {
          type: "number",
          description: "The amount to convert",
        },
        from: {
          type: "string",
          description: 'Source currency code, e.g. "USD"',
        },
        to: {
          type: "string",
          description: 'Target currency code, e.g. "EUR"',
        },
      },
      required: ["amount", "from", "to"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const amount = Number(args.amount);
      const from = String(args.from).toUpperCase();
      const to = String(args.to).toUpperCase();

      if (!isFinite(amount) || amount < 0) {
        return "Error: Amount must be a non-negative number.";
      }

      const url = `https://api.exchangerate-api.com/v4/latest/${from}`;
      const response = await fetch(url, {
        signal: AbortSignal.timeout(15000),
      });

      if (!response.ok) {
        return `Error: Failed to fetch exchange rates for ${from} (HTTP ${response.status}). Check that the currency code is valid.`;
      }

      const data = (await response.json()) as {
        rates: Record<string, number>;
      };

      if (!data.rates || !data.rates[to]) {
        return `Error: Currency "${to}" not found. Check the currency code.`;
      }

      const rate = data.rates[to];
      const converted = amount * rate;

      return `${amount} ${from} = ${converted.toFixed(2)} ${to}\nExchange rate: 1 ${from} = ${rate.toFixed(6)} ${to}`;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error converting currency: ${msg}`;
    }
  },
};
