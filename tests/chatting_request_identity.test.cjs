const {test} = require("node:test");
const assert = require("node:assert/strict");
const Identity = require("../static/chat/request-identity.js");

test("lost responses reuse identity; acknowledged deliberate repeats get new identity", () => {
  let count = 0;
  const identity = new Identity(() => String(++count));
  const payload = {conversation_id: "a", message: "같은 질문", document_id: null};
  const first = identity.get(payload);
  assert.equal(identity.get({...payload}), first);
  identity.clear();
  assert.notEqual(identity.get(payload), first);
});

for (const field of ["message", "document_id", "conversation_id", "reply_to"]) {
  test(`changed ${field} cannot reuse a previous operation`, () => {
    let count = 0;
    const identity = new Identity(() => String(++count));
    const payload = {conversation_id: "a", message: "질문", document_id: "doc", reply_to: "pending"};
    assert.notEqual(identity.get(payload), identity.get({...payload, [field]: "different"}));
  });
}
