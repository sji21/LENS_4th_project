"""Exercise the pending UI timer with a controlled clock, no model calls."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_estimate_keeps_elapsed_and_becomes_indeterminate():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for frontend timer check")
    source = Path("static/chat/app.js").read_text()
    action = source[source.index("  async function action("):source.index('  input.addEventListener("input"')]
    script = r'''
const assert = require("node:assert/strict");
let busy=false, externalBusy=false, conversationId="one", estimatedSeconds=30;
let now=0, tick, cleared=false;
Date.now=()=>now;
const nodes={};
const $=id=>nodes[id] ||= {hidden:false, textContent:"", value:0, removeAttribute(k){delete this[k];}};
const controls=()=>{}, notice=()=>{}, syncState=async()=>{};
const setInterval=fn=>{tick=fn;return 1;}, clearInterval=()=>{cleared=true;};
'''+action+r'''
(async()=>{
 let finish;
 const pending=action("답변",()=>new Promise(resolve=>finish=resolve),true);
 now=10000; tick();
 assert.equal($("elapsed").textContent,"10초");
 assert.match($("estimate-text").textContent,/20초/);
 assert.ok($("answer-progress").value<100);
 now=31000; tick();
 assert.equal($("elapsed").textContent,"31초");
 assert.equal($("answer-progress").value,undefined);
 finish(); await pending;
 assert.equal($("pending").hidden,true); assert.equal(cleared,true);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
