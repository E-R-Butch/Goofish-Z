const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.text='';this.value='';this.listeners={};this.disabled=false; }
  set textContent(v){this.text=String(v);this.children=[];}
  get textContent(){return this.text+this.children.map(e=>e.textContent).join('');}
  set innerHTML(_){throw new Error('HTML sink forbidden');}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.text='';this.children=children;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  querySelectorAll(){return [];}
}
function setup(respond) {
  const nodes=new Map(); const requests=[];
  const document={createElement:tag=>new Element(tag),getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},querySelectorAll:()=>[{value:'jd'}]};
  document.getElementById('priceLimit').value='20';
  document.getElementById('priceForm').querySelectorAll=()=>[document.getElementById('priceQuery'),document.getElementById('priceCollectButton')];
  const context=vm.createContext({document,URL,Date,confirm:()=>false,fetch:async(url,opts)=>{requests.push([url,opts]);const data=await respond(url,opts);return {ok:!data.detail,status:data.detail?400:200,json:async()=>data};}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/goofish_z/gui/prices.js'),'utf8'),context);
  return {get:id=>document.getElementById(id),requests,submit:()=>document.getElementById('priceForm').listeners.submit({preventDefault(){}})};
}
function result(query,title='synthetic item') {
  return {query,collected_at:'2026-01-01T00:00:00Z',status:'succeeded',sources:[{platform:'jd',status:'ok',count:1,login_url:'https://www.jd.com/'}],
    analysis:{basis:'synthetic analysis',groups:[]},quotes:[{platform:'jd',title,url:'https://item.jd.com/100.html',shop:'synthetic',price_low:2400,price_text:'¥2400',price_kind:'listed',conditions:[],reasons:[],match:'title_match'}]};
}
test('new keyword removes previous quotes immediately and failures never restore them',async()=>{
  let finish;
  const s=setup(async(_url,opts)=>JSON.parse(opts.body).query==='4090'?result('4090','old 4090'):new Promise(resolve=>{finish=resolve;}));
  s.get('priceQuery').value='4090';await s.submit();assert.match(s.get('priceQuotes').textContent,/old 4090/);
  s.get('priceQuery').value='4080S 32G';const pending=s.submit();
  assert.equal(s.get('priceQuotes').textContent,'');assert.equal(s.get('priceQuery').disabled,true);
  finish({detail:'synthetic timeout'});await pending;
  assert.equal(s.get('priceQuotes').textContent,'');assert.match(s.get('priceStatus').textContent,/4080S 32G.*失败.*timeout/);assert.equal(s.get('priceQuery').disabled,false);
  assert.equal(s.requests.length,2);
});
test('query mismatch is rejected and source text remains literal',async()=>{
  const s=setup(async()=>result('wrong','<img onerror=synthetic()>'));
  s.get('priceQuery').value='requested';await s.submit();
  assert.match(s.get('priceStatus').textContent,/关键词不一致/);assert.equal(s.get('priceQuotes').textContent,'');
  const t=setup(async()=>result('requested','<img onerror=synthetic()>'));
  t.get('priceQuery').value='requested';await t.submit();assert.match(t.get('priceQuotes').textContent,/<img onerror=synthetic\(\)>/);
});
test('history is explicitly labelled and does not collect from a marketplace',async()=>{
  const s=setup(async()=>({runs:[result('5060')]}));s.get('priceQuery').value='5060';
  await s.get('priceHistoryButton').listeners.click();s.get('priceRuns').children[0].listeners.click();
  assert.match(s.get('priceStatus').textContent,/历史记录/);assert.equal(s.requests.length,1);assert.match(s.requests[0][0],/^\/api\/price\/history/);
});
test('login-required sources expose scoped opt-in import and cancel sends nothing',async()=>{
  const r=result('5060');r.status='failed';r.quotes=[];r.sources[0].status='login_required';r.sources[0].count=0;
  const s=setup(async()=>r);s.get('priceQuery').value='5060';await s.submit();
  const button=s.get('priceSources').children[0].children.find(e=>e.tagName==='BUTTON');
  await button.listeners.click();assert.equal(s.requests.length,1);assert.match(s.get('priceQuotes').textContent,/历史结果不会替代/);
});
test('partially priced cards show observed and usable price counts separately',async()=>{
  const r=result('5060');r.status='partial';
  Object.assign(r.sources[0],{status:'partial',count:2,priced_count:1,missing_prices:1,message:'1 条商品未解析到有效价格'});
  const s=setup(async()=>r);s.get('priceQuery').value='5060';await s.submit();
  assert.match(s.get('priceSources').textContent,/部分完成.*识别 2 条.*有效价格 1 条.*1 条商品未解析到有效价格/);
  assert.match(s.get('priceStatus').textContent,/部分平台未完成/);
});
