<!-- Story 1.5 fixture (never part of src/): proves the lint ban on `{@html}`
     actually fires (eslint-plugin-svelte's svelte/no-at-html-tags). See
     forbidden-inner-html.ts for the sibling innerHTML/insertAdjacentHTML
     fixtures and how scripts/verify-lint.mjs uses all three. -->
<script lang="ts">
  const markup = "<b>this must never pass lint</b>";
</script>

{@html markup}
