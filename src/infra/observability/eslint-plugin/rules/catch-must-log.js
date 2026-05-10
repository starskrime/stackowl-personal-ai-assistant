"use strict";

/**
 * Requires every catch block to either:
 *   (a) contain a call to log.<x>.error/warn/fatal(msg, err, …) or
 *       getLogger(…).error/warn/fatal(msg, err, …), OR
 *   (b) rethrow the error (throw statement present).
 *
 * Also warns on typeless catch clauses (`catch { … }`) that drop the binding.
 */

function hasLogCall(body) {
  return body.some((stmt) => containsLogCall(stmt));
}

function containsLogCall(node) {
  if (!node) return false;

  // ExpressionStatement wrapping a CallExpression
  if (
    node.type === "ExpressionStatement" &&
    node.expression.type === "CallExpression"
  ) {
    if (isLogCall(node.expression)) return true;
  }

  // Await expression (await log.x.error(…))
  if (
    node.type === "ExpressionStatement" &&
    node.expression.type === "AwaitExpression" &&
    node.expression.argument?.type === "CallExpression" &&
    isLogCall(node.expression.argument)
  ) {
    return true;
  }

  // Recurse into block bodies
  if (node.type === "BlockStatement") {
    return node.body.some(containsLogCall);
  }
  if (node.type === "IfStatement") {
    return containsLogCall(node.consequent) || containsLogCall(node.alternate);
  }

  return false;
}

function isLogCall(callExpr) {
  const callee = callExpr.callee;
  if (!callee) return false;

  // log.engine.error(…) — MemberExpression chain depth 2
  if (callee.type === "MemberExpression") {
    const method = callee.property?.name;
    if (!["error", "warn", "fatal"].includes(method)) return false;

    const obj = callee.object;
    // log.<module>.<method>
    if (
      obj?.type === "MemberExpression" &&
      obj.object?.name === "log"
    ) return true;

    // getLogger(…).<method>
    if (
      obj?.type === "CallExpression" &&
      obj.callee?.name === "getLogger"
    ) return true;

    // someLogger.<method> — less certain but allow it
    if (obj?.type === "Identifier") return true;
  }

  return false;
}

function hasThrowStatement(body) {
  return body.some((stmt) => {
    if (stmt.type === "ThrowStatement") return true;
    if (stmt.type === "BlockStatement") return hasThrowStatement(stmt.body);
    return false;
  });
}

module.exports = {
  meta: {
    type: "suggestion",
    docs: {
      description:
        "Require catch blocks to log the error via log.<module>.error/warn/fatal(msg, err) or rethrow",
      recommended: true,
    },
    schema: [],
    messages: {
      mustLog:
        "Catch block must call log.<module>.error/warn/fatal(msg, err, fields) or rethrow as a " +
        "wrapped Error. Silent swallowing hides bugs.",
      typeless:
        "Typeless catch clause (catch { … }) drops the error binding. Use catch (err) { … }.",
    },
  },
  create(context) {
    return {
      CatchClause(node) {
        // Warn on typeless catch (no param)
        if (!node.param) {
          context.report({ node, messageId: "typeless" });
          return; // no-empty-catch will handle the empty body case
        }

        const body = node.body.body;
        if (body.length === 0) return; // no-empty-catch handles this

        if (!hasLogCall(body) && !hasThrowStatement(body)) {
          context.report({ node, messageId: "mustLog" });
        }
      },
    };
  },
};
