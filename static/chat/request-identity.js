"use strict";
// Keep an uncertain submission's identity until its response is acknowledged.
class ChatRequestIdentity {
  constructor(makeId) { this.makeId = makeId; this.pending = null; }
  get(payload) {
    const signature = JSON.stringify(payload);
    if (this.pending?.signature !== signature) this.pending = { signature, id: this.makeId() };
    return this.pending.id;
  }
  clear() { this.pending = null; }
}
if (typeof module !== "undefined") module.exports = ChatRequestIdentity;
