import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
    return str.replace(/'/g, "'\\''");
}

export const AppleCalendarTool: ToolImplementation = {
    definition: {
        name: "apple_calendar",
        description:
            "Manage macOS Calendar — list today's events, add new events, or search. Use for scheduling, checking availability, and time management.",
        parameters: {
            type: "object",
            properties: {
                action: {
                    type: "string",
                    enum: ["list", "add", "search"],
                    description: "Action to perform: list today's events, add a new event, or search events.",
                },
                title: {
                    type: "string",
                    description: "Event title (required for 'add').",
                },
                date: {
                    type: "string",
                    description: "Event date in YYYY-MM-DD format (required for 'add').",
                },
                time: {
                    type: "string",
                    description: "Event start time in HH:MM 24h format (required for 'add').",
                },
                duration: {
                    type: "number",
                    description: "Event duration in minutes (required for 'add').",
                },
                keyword: {
                    type: "string",
                    description: "Search keyword (required for 'search').",
                },
            },
            required: ["action"],
        },
    },

    async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
        const action = args.action as string;

        try {
            switch (action) {
                case "list": {
                    const script = `
tell application "Calendar"
    set today to current date
    set time of today to 0
    set tomorrow to today + (1 * days)
    set output to ""
    repeat with cal in calendars
        set calName to name of cal
        try
            set evts to (every event of cal whose start date >= today and start date < tomorrow)
            repeat with evt in evts
                set evtTitle to summary of evt
                set evtStart to start date of evt
                set evtEnd to end date of evt
                set output to output & calName & " | " & evtTitle & " | " & (evtStart as string) & " - " & (evtEnd as string) & linefeed
            end repeat
        end try
    end repeat
    if output is "" then
        return "No events found for today."
    end if
    return output
end tell`;
                    const { stdout } = await execAsync(`osascript -e '${escapeForShell(script)}'`, { timeout: 15000 });
                    return stdout.trim() || "No events found for today.";
                }

                case "add": {
                    const title = args.title as string;
                    const date = args.date as string;
                    const time = args.time as string;
                    const duration = args.duration as number;

                    if (!title || !date || !time || !duration) {
                        return "Error: 'add' action requires title, date, time, and duration parameters.";
                    }

                    const [year, month, day] = date.split("-");
                    const [hour, minute] = time.split(":");

                    const script = `
tell application "Calendar"
    set startDate to current date
    set year of startDate to ${year}
    set month of startDate to ${month}
    set day of startDate to ${day}
    set hours of startDate to ${hour}
    set minutes of startDate to ${minute}
    set seconds of startDate to 0
    set endDate to startDate + (${duration} * minutes)
    tell calendar 1
        make new event with properties {summary:"${escapeForShell(title)}", start date:startDate, end date:endDate}
    end tell
    return "Event created: ${escapeForShell(title)} on ${escapeForShell(date)} at ${escapeForShell(time)} for ${duration} minutes."
end tell`;
                    const { stdout } = await execAsync(`osascript -e '${escapeForShell(script)}'`, { timeout: 15000 });
                    return stdout.trim() || `Event "${title}" created successfully.`;
                }

                case "search": {
                    const keyword = args.keyword as string;
                    if (!keyword) {
                        return "Error: 'search' action requires a keyword parameter.";
                    }

                    const script = `
tell application "Calendar"
    set output to ""
    set searchTerm to "${escapeForShell(keyword)}"
    repeat with cal in calendars
        set calName to name of cal
        try
            set evts to (every event of cal whose summary contains searchTerm)
            repeat with evt in evts
                set evtTitle to summary of evt
                set evtStart to start date of evt
                set evtEnd to end date of evt
                set output to output & calName & " | " & evtTitle & " | " & (evtStart as string) & " - " & (evtEnd as string) & linefeed
            end repeat
        end try
    end repeat
    if output is "" then
        return "No events found matching: " & searchTerm
    end if
    return output
end tell`;
                    const { stdout } = await execAsync(`osascript -e '${escapeForShell(script)}'`, { timeout: 15000 });
                    return stdout.trim() || `No events found matching "${keyword}".`;
                }

                default:
                    return `Error: Unknown action "${action}". Use "list", "add", or "search".`;
            }
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            return `Error interacting with Calendar: ${msg}`;
        }
    },
};
