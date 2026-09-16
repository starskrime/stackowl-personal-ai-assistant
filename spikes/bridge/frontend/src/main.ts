// Story 1.5 entry point: mounts the one Svelte root component onto the
// page. Svelte 5's `mount()` API replaces `new Component()` (Svelte 4) --
// see App.svelte for the actual scene/Trusted-Types wiring.
import { mount } from "svelte";
import App from "./App.svelte";

const target = document.getElementById("app");
if (!target) {
  throw new Error("bridge-spike-frontend: #app mount point is missing from index.html");
}

mount(App, { target });
