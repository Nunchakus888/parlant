// MongoDB Playground
// Use Ctrl+Space inside a snippet or a string literal to trigger completions.

// The current database to use.
use("omni_agent_sessions");

// Find a document in a collection.
db.getCollection("inspections").find({
  // correlation_id: "RNl5GT2rozz::process",
  session_id: "t-tks-1118-003",
});
