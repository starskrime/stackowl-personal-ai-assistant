/** Flags catch clauses whose body is empty (no statements at all). */
export default {
  meta: {
    type: "problem",
    docs: {
      description: "Disallow empty catch blocks — always log the error",
      recommended: true,
    },
    schema: [],
    messages: {
      emptyBody:
        "Empty catch block. Log the error with log.<module>.error('description', err, { fields }) " +
        "or rethrow as a wrapped Error.",
    },
  },
  create(context) {
    return {
      CatchClause(node) {
        if (node.body.body.length === 0) {
          context.report({ node, messageId: "emptyBody" });
        }
      },
    };
  },
};
