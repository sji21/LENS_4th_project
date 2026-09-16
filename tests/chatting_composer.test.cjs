const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("../static/chat/app.js"), "utf8");
const submit = source.slice(source.indexOf('  form.addEventListener("submit"'), source.indexOf('  const reportForm'));

for (const failure of [false, true]) {
  for (const newDraft of ["", "다음 질문"]) {
    test(`composer clears immediately; failure=${failure}, draft=${newDraft}`, async () => {
      let handler, operation, resolve, reject, reloaded = false;
      const response = new Promise((yes, no) => { resolve = yes; reject = no; });
      const input = {value: "첫 질문", focus() {}};
      const context = {
        window: {location: {reload() { reloaded = true; }}},
        input, replyingTo: "pending-1", conversationId: "chat-1", lastRendered: "",
        form: {dataset: {sendUrl: "/send"}, addEventListener: (_, fn) => { handler = fn; }},
        action: (_, fn) => { operation = fn(); },
        $: () => ({value: "", append() {}}),
        renderMessage: () => ({}), scrollBottom() {}, render() {},
        submission: {get: () => "request-1", clear() {}}, post: () => response,
      };
      vm.runInNewContext(submit, context);
      handler({preventDefault() {}});
      assert.equal(input.value, "");
      assert.equal(context.replyingTo, null);
      input.value = newDraft;
      if (failure) {
        reject(new Error("network"));
        await assert.rejects(operation, /network/);
        assert.equal(input.value, newDraft || "첫 질문");
        assert.equal(context.replyingTo, newDraft ? null : "pending-1");
      } else {
        resolve({room_created: true});
        await operation;
        assert.equal(input.value, newDraft);
        assert.equal(reloaded, !newDraft);
      }
    });
  }
}
