import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";

export const WeatherTool: ToolImplementation = {
  definition: {
    name: "weather",
    description:
      "Get current weather and 3-day forecast for any location. No API key needed.",
    parameters: {
      type: "object",
      properties: {
        location: {
          type: "string",
          description: 'Location name, e.g. "London" or "New York"',
        },
      },
      required: ["location"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const location = String(args.location);
    log.tool.debug("weather.execute: entry", { location });
    try {
      const url = `https://wttr.in/${encodeURIComponent(location)}?format=j1`;
      log.tool.debug("weather.execute: fetching API", { url });

      const response = await fetch(url, {
        headers: { "User-Agent": "stackowl-weather-tool" },
        signal: AbortSignal.timeout(15000),
      });

      if (!response.ok) {
        return `Error: Failed to fetch weather for "${location}" (HTTP ${response.status})`;
      }

      const data = (await response.json()) as Record<string, unknown>;

      const currentArr = data.current_condition as Record<string, unknown>[];
      if (!currentArr || !currentArr[0]) {
        return `Error: No weather data available for "${location}"`;
      }
      const current = currentArr[0];

      const tempC = current.temp_C;
      const tempF = current.temp_F;
      const feelsLikeC = current.FeelsLikeC;
      const feelsLikeF = current.FeelsLikeF;
      const humidity = current.humidity;
      const windKmph = current.windspeedKmph;
      const windDir = current.winddir16Point;
      const descArr = current.weatherDesc as Record<string, unknown>[];
      const desc = descArr?.[0]?.value ?? "Unknown";

      let result = `Weather for ${location}\n`;
      result += `${"=".repeat(40)}\n\n`;
      result += `Current Conditions:\n`;
      result += `  ${desc}\n`;
      result += `  Temperature: ${tempC}°C / ${tempF}°F\n`;
      result += `  Feels like: ${feelsLikeC}°C / ${feelsLikeF}°F\n`;
      result += `  Humidity: ${humidity}%\n`;
      result += `  Wind: ${windKmph} km/h ${windDir}\n\n`;

      // 3-day forecast
      const forecast = data.weather as Record<string, unknown>[];
      if (forecast && forecast.length > 0) {
        result += `3-Day Forecast:\n`;
        result += `${"-".repeat(40)}\n`;
        for (const day of forecast.slice(0, 3)) {
          const date = day.date;
          const maxC = day.maxtempC;
          const minC = day.mintempC;
          const maxF = day.maxtempF;
          const minF = day.mintempF;
          const hourly = day.hourly as Record<string, unknown>[];
          const dayDesc = hourly?.[4]
            ? ((hourly[4].weatherDesc as Record<string, unknown>[])?.[0]
                ?.value ?? "Unknown")
            : "Unknown";
          result += `  ${date}: ${dayDesc}, ${minC}–${maxC}°C / ${minF}–${maxF}°F\n`;
        }
      }

      log.tool.debug("weather.execute: exit", { success: true, location, forecastDays: forecast?.length ?? 0 });
      return result;
    } catch (error) {
      log.tool.error("weather.execute: failed", error, { location });
      const msg = error instanceof Error ? error.message : String(error);
      return `Error fetching weather: ${msg}`;
    }
  },
};
