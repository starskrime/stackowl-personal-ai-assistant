// Story 1.5 fixture (never part of src/): proves the lint ban on
// `.insertAdjacentHTML(` actually fires. See forbidden-inner-html.ts.
const target = document.getElementById("forbidden-insert-adjacent-html-fixture");
if (target) {
  target.insertAdjacentHTML("beforeend", "<b>this must never pass lint</b>");
}
