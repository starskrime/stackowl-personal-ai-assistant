"use strict";

const noEmptyCatch = require("./rules/no-empty-catch.js");
const catchMustLog = require("./rules/catch-must-log.js");

module.exports = {
  rules: {
    "no-empty-catch": noEmptyCatch,
    "catch-must-log":  catchMustLog,
  },
};
