import noEmptyCatch from "./rules/no-empty-catch.js";
import catchMustLog from "./rules/catch-must-log.js";

export default {
  rules: {
    "no-empty-catch": noEmptyCatch,
    "catch-must-log":  catchMustLog,
  },
};
