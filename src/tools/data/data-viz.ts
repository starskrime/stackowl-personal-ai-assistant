/**
 * StackOwl — Data Visualization Tool
 *
 * Creates charts and graphs using QuickChart.io API (Chart.js format).
 * Downloads chart images to workspace.
 */

import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";

const VALID_CHART_TYPES = ["bar", "line", "pie", "doughnut"];

export const DataVisualizationTool: ToolImplementation = {
  definition: {
    name: "data_visualization",
    description:
      "Create charts and graphs (bar, line, pie, doughnut) from data. Downloads chart image to workspace.",
    parameters: {
      type: "object",
      properties: {
        chart_type: {
          type: "string",
          description: 'Chart type: "bar", "line", "pie", or "doughnut".',
        },
        labels: {
          type: "string",
          description:
            'JSON array of label strings. Example: \'["Jan", "Feb", "Mar"]\'',
        },
        data: {
          type: "string",
          description:
            'JSON array of numbers. Example: \'[10, 20, 30]\'',
        },
        title: {
          type: "string",
          description: "Chart title.",
        },
      },
      required: ["chart_type", "labels", "data", "title"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const chartType = args["chart_type"] as string;
      const labelsStr = args["labels"] as string;
      const dataStr = args["data"] as string;
      const title = args["title"] as string;

      if (!chartType) return "Error: 'chart_type' parameter is required.";
      if (!labelsStr) return "Error: 'labels' parameter is required.";
      if (!dataStr) return "Error: 'data' parameter is required.";
      if (!title) return "Error: 'title' parameter is required.";

      if (!VALID_CHART_TYPES.includes(chartType)) {
        return `Error: Invalid chart type '${chartType}'. Must be one of: ${VALID_CHART_TYPES.join(", ")}`;
      }

      let labels: string[];
      let data: number[];
      try {
        labels = JSON.parse(labelsStr);
      } catch {
        return "Error: 'labels' must be a valid JSON array string.";
      }
      try {
        data = JSON.parse(dataStr);
      } catch {
        return "Error: 'data' must be a valid JSON array string.";
      }

      if (!Array.isArray(labels) || !Array.isArray(data)) {
        return "Error: 'labels' and 'data' must be arrays.";
      }
      if (labels.length !== data.length) {
        return `Error: labels (${labels.length}) and data (${data.length}) must have the same length.`;
      }

      const chartConfig = {
        type: chartType,
        data: {
          labels,
          datasets: [
            {
              label: title,
              data,
            },
          ],
        },
        options: {
          title: {
            display: true,
            text: title,
          },
        },
      };

      const chartUrl = `https://quickchart.io/chart?c=${encodeURIComponent(JSON.stringify(chartConfig))}`;

      // Download chart image
      const chartsDir = resolve(_context.cwd, "workspace", "charts");
      await mkdir(chartsDir, { recursive: true });

      const filename = `chart-${Date.now()}.png`;
      const chartPath = resolve(chartsDir, filename);

      try {
        const response = await fetch(chartUrl, {
          signal: AbortSignal.timeout(30_000),
        });

        if (!response.ok) {
          return `Chart URL generated but download failed (HTTP ${response.status}).\nURL: ${chartUrl}`;
        }

        const buffer = Buffer.from(await response.arrayBuffer());
        await writeFile(chartPath, buffer);

        return `Chart created:\n- File: ${chartPath}\n- URL: ${chartUrl}`;
      } catch (downloadError: any) {
        return `Chart URL generated but download failed: ${downloadError.message}\nURL: ${chartUrl}`;
      }
    } catch (error: any) {
      return `Error creating chart: ${error.message ?? String(error)}`;
    }
  },
};
