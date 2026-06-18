/**
 * StackOwl — LLM Task Queue
 *
 * Shared task queue for all background LLM API calls.
 * concurrency=1 ensures at most one background LLM call runs at any moment,
 * preventing background tasks from competing with the foreground conversation.
 *
 * Usage:
 *   llmTaskQueue.enqueue("task-name", async () => { ... }, "low");
 */
import { TaskQueue } from "./task-queue.js";

export const llmTaskQueue = new TaskQueue({ concurrency: 1 });
