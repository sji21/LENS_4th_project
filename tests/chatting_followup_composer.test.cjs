const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../static/chat/app.js'), 'utf8');
const render = source.slice(source.indexOf('  function renderConversation('), source.indexOf('  function render(data)'));

test('pending keeps ordinary composer, draft and reply identity', () => {
  const input = {value:'두 달 남았어', setAttribute(){}};
  const nodes = {'messages': {children:[], querySelectorAll(){return [];}}, 'answer-actions':{}, 'active-document':{}};
  const form = {dataset:{}};
  const context = {input, form, $: id => nodes[id]};
  vm.createContext(context); vm.runInContext(render, context);
  context.renderConversation({conversation:{enabled:true,pending:{question:'계약 만료일이 언제인가요?',message_id:'a1'}},documents:[]});
  assert.equal(input.placeholder,'궁금한 내용을 편하게 물어보세요.');
  assert.equal(input.value,'두 달 남았어');
  assert.equal(form.dataset.replyTo,'a1');
  context.renderConversation({conversation:{enabled:true,pending:null},documents:[]});
  assert.equal(input.placeholder,'궁금한 내용을 편하게 물어보세요.');
  assert.equal(input.value,'두 달 남았어');
  assert.equal(form.dataset.replyTo,'');
});

test('free text reply uses pending identity through the existing composer', async () => {
  const submit = source.slice(source.indexOf('  form.addEventListener("submit"'), source.indexOf('  const reportForm'));
  let handler, operation, payload;
  const context = {
    input:{value:'두 달 남았어',focus(){}}, replyingTo:null, conversationId:'chat',lastRendered:'',
    form:{dataset:{sendUrl:'/send',replyTo:'pending-1'},addEventListener:(_,fn)=>handler=fn},
    action:(_,fn)=>operation=fn(), $:()=>({value:'',append(){}}),
    renderMessage:()=>({}),scrollBottom(){},render(){},
    submission:{get:p=>{payload=p;return 'request';},clear(){}}, post:async()=>({}),
  };
  vm.runInNewContext(submit,context);
  handler({preventDefault(){}}); await operation;
  assert.equal(payload.message,'두 달 남았어');
  assert.equal(payload.reply_to,'pending-1');
  assert.equal(context.input.value,'');
});

test('suggested replies submit but direct input only focuses and preserves draft', () => {
  const makeNode = text => ({text,children:[],handlers:{},append(...nodes){this.children.push(...nodes);},addEventListener(name,fn){this.handlers[name]=fn;}});
  const bubble=makeNode(''); const row={dataset:{messageId:'a1'},querySelector:()=>bubble};
  const nodes={'messages':{children:[row],querySelectorAll:()=>[]},'answer-actions':{},'active-document':{},'document-select':{value:''}};
  let focusCount=0, submissions=0;
  const input={value:'작성 중인 답',focus(){focusCount++;},setAttribute(){}};
  const context={input,form:{dataset:{},requestSubmit(){submissions++;}},replyingTo:null,controls(){},$:id=>nodes[id],element:(_,text)=>makeNode(text)};
  vm.createContext(context);vm.runInContext(render,context);
  context.renderConversation({conversation:{enabled:true,pending:{message_id:'a1',question:'계약 만료일은요?',choices:[{label:'모르겠어요',message:'모르겠어요'}]}},documents:[]});
  const buttons=bubble.children[0].children;
  assert.deepEqual(buttons.map(b=>b.text),['모르겠어요','직접 입력']);
  buttons[1].handlers.click();
  assert.equal(input.value,'작성 중인 답');assert.equal(focusCount,1);assert.equal(submissions,0);
  buttons[0].handlers.click();
  assert.equal(input.value,'모르겠어요');assert.equal(submissions,1);assert.equal(context.replyingTo,'a1');
});
