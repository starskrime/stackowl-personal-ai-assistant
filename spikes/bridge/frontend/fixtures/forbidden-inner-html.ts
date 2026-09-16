// Story 1.5 fixture (never part of src/): proves the lint ban on
// `.innerHTML =` actually fires. scripts/verify-lint.mjs lints this file's
// text directly and fails the whole `npm run lint` run if ESLint reports
// zero errors against it.
const target = document.getElementById("forbidden-inner-html-fixture");
if (target) {
  target.innerHTML = "<b>this must never pass lint</b>";
}
