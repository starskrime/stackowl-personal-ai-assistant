import type { ToolImplementation, ToolContext } from "../registry.js";

type ConversionMap = Record<string, Record<string, number>>;

const CONVERSIONS: Record<string, ConversionMap> = {
  length: {
    m: {
      km: 0.001,
      cm: 100,
      mm: 1000,
      mi: 0.000621371,
      ft: 3.28084,
      in: 39.3701,
      yd: 1.09361,
      nm: 1852000000,
    },
    km: {
      m: 1000,
      cm: 100000,
      mm: 1000000,
      mi: 0.621371,
      ft: 3280.84,
      in: 39370.1,
      yd: 1093.61,
    },
    mi: { km: 1.60934, m: 1609.34, ft: 5280, yd: 1760, in: 63360 },
    ft: {
      m: 0.3048,
      cm: 30.48,
      in: 12,
      yd: 0.333333,
      mi: 0.000189394,
      km: 0.0003048,
    },
    in: { cm: 2.54, mm: 25.4, m: 0.0254, ft: 0.0833333 },
    cm: { m: 0.01, mm: 10, in: 0.393701, ft: 0.0328084 },
    yd: { m: 0.9144, ft: 3, mi: 0.000568182, km: 0.0009144 },
  },
  weight: {
    kg: {
      g: 1000,
      mg: 1000000,
      lb: 2.20462,
      oz: 35.274,
      ton: 0.001,
      st: 0.157473,
    },
    g: { kg: 0.001, mg: 1000, lb: 0.00220462, oz: 0.035274 },
    lb: { kg: 0.453592, g: 453.592, oz: 16, st: 0.0714286, ton: 0.000453592 },
    oz: { g: 28.3495, kg: 0.0283495, lb: 0.0625 },
    ton: { kg: 1000, lb: 2204.62, g: 1000000 },
    st: { kg: 6.35029, lb: 14 },
  },
  volume: {
    l: {
      ml: 1000,
      gal: 0.264172,
      qt: 1.05669,
      pt: 2.11338,
      cup: 4.22675,
      fl_oz: 33.814,
      tbsp: 67.628,
      tsp: 202.884,
    },
    ml: {
      l: 0.001,
      fl_oz: 0.033814,
      tsp: 0.202884,
      tbsp: 0.067628,
      cup: 0.00422675,
    },
    gal: { l: 3.78541, qt: 4, pt: 8, cup: 16, fl_oz: 128, ml: 3785.41 },
    cup: { ml: 236.588, l: 0.236588, fl_oz: 8, tbsp: 16, tsp: 48 },
    fl_oz: { ml: 29.5735, l: 0.0295735, cup: 0.125, tbsp: 2, tsp: 6 },
  },
  speed: {
    "km/h": { "m/s": 0.277778, mph: 0.621371, knot: 0.539957 },
    mph: { "km/h": 1.60934, "m/s": 0.44704, knot: 0.868976 },
    "m/s": { "km/h": 3.6, mph: 2.23694, knot: 1.94384 },
    knot: { "km/h": 1.852, mph: 1.15078, "m/s": 0.514444 },
  },
  area: {
    sqm: {
      sqft: 10.7639,
      sqkm: 0.000001,
      acre: 0.000247105,
      ha: 0.0001,
      sqmi: 3.861e-7,
    },
    sqft: { sqm: 0.092903, acre: 0.0000229568, sqkm: 9.2903e-8 },
    acre: { sqm: 4046.86, sqft: 43560, ha: 0.404686, sqkm: 0.00404686 },
    ha: { sqm: 10000, acre: 2.47105, sqkm: 0.01 },
    sqkm: { sqm: 1000000, sqmi: 0.386102, acre: 247.105, ha: 100 },
  },
  data: {
    b: { kb: 0.001, mb: 0.000001, gb: 1e-9, tb: 1e-12 },
    kb: { b: 1000, mb: 0.001, gb: 0.000001, tb: 1e-9 },
    mb: { b: 1000000, kb: 1000, gb: 0.001, tb: 0.000001 },
    gb: { b: 1e9, kb: 1000000, mb: 1000, tb: 0.001 },
    tb: { b: 1e12, kb: 1e9, mb: 1000000, gb: 1000 },
  },
  time: {
    s: {
      ms: 1000,
      min: 1 / 60,
      hr: 1 / 3600,
      day: 1 / 86400,
      week: 1 / 604800,
    },
    min: { s: 60, ms: 60000, hr: 1 / 60, day: 1 / 1440, week: 1 / 10080 },
    hr: { s: 3600, min: 60, day: 1 / 24, week: 1 / 168, ms: 3600000 },
    day: { s: 86400, min: 1440, hr: 24, week: 1 / 7, ms: 86400000 },
    week: { day: 7, hr: 168, min: 10080, s: 604800 },
  },
};

function convertTemperature(
  value: number,
  from: string,
  to: string,
): number | null {
  const f = from.toLowerCase();
  const t = to.toLowerCase();
  if (f === t) return value;
  if (f === "c" && t === "f") return (value * 9) / 5 + 32;
  if (f === "f" && t === "c") return ((value - 32) * 5) / 9;
  if (f === "c" && t === "k") return value + 273.15;
  if (f === "k" && t === "c") return value - 273.15;
  if (f === "f" && t === "k") return ((value - 32) * 5) / 9 + 273.15;
  if (f === "k" && t === "f") return ((value - 273.15) * 9) / 5 + 32;
  return null;
}

export const UnitConverterTool: ToolImplementation = {
  definition: {
    name: "convert_units",
    description:
      "Convert between units of measurement. Categories: length (m, km, mi, ft, in, cm, yd), " +
      "weight (kg, g, lb, oz, ton, st), volume (l, ml, gal, cup, fl_oz, tbsp, tsp), " +
      "temperature (c, f, k), speed (km/h, mph, m/s, knot), area (sqm, sqft, acre, ha, sqkm), " +
      "data (b, kb, mb, gb, tb), time (ms, s, min, hr, day, week).",
    parameters: {
      type: "object",
      properties: {
        value: {
          type: "number",
          description: "The numeric value to convert",
        },
        from: {
          type: "string",
          description: "Source unit (e.g., 'km', 'lb', 'c', 'gal')",
        },
        to: {
          type: "string",
          description: "Target unit (e.g., 'mi', 'kg', 'f', 'l')",
        },
      },
      required: ["value", "from", "to"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const value = Number(args.value);
    const from = String(args.from).toLowerCase();
    const to = String(args.to).toLowerCase();

    if (isNaN(value)) return "Error: Invalid numeric value.";
    if (from === to) return `${value} ${from} = ${value} ${to}`;

    // Temperature special case
    const tempUnits = ["c", "f", "k"];
    if (tempUnits.includes(from) && tempUnits.includes(to)) {
      const result = convertTemperature(value, from, to);
      if (result !== null) {
        return `${value} °${from.toUpperCase()} = ${Number(result.toFixed(4))} °${to.toUpperCase()}`;
      }
    }

    // Search all categories for matching conversion
    for (const [category, units] of Object.entries(CONVERSIONS)) {
      if (units[from]?.[to] !== undefined) {
        const result = value * units[from][to];
        return `${value} ${from} = ${Number(result.toFixed(6))} ${to} (${category})`;
      }
    }

    // Try reverse lookup
    for (const [category, units] of Object.entries(CONVERSIONS)) {
      if (units[to]?.[from] !== undefined) {
        const result = value / units[to][from];
        return `${value} ${from} = ${Number(result.toFixed(6))} ${to} (${category})`;
      }
    }

    return (
      `Error: Cannot convert from "${from}" to "${to}".\n` +
      `Supported categories: length, weight, volume, temperature, speed, area, data, time.\n` +
      `Use standard unit abbreviations (e.g., km, mi, kg, lb, l, gal, c, f).`
    );
  },
};
