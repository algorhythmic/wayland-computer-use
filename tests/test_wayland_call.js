const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync(require('node:path').join(__dirname, '../scripts/wayland_call.js'), 'utf8');
async function test() {
  let writes, displayed;
  async function run(chunks, request = {name:'pointer', arguments:{x:1,y:2}}, clock = () => 0) {
    writes = []; displayed = [];
    const tools = {
      write_stdin: async args => { writes.push(args); assert.ok(chunks.length); return chunks.shift(); },
      view_image: async args => { displayed.push(args.path); return {image_url:'data:image/png;base64,test'}; }
    };
    const text = () => {};
    const image = value => displayed.push(value);
    const Date = {now: clock};
    return eval(source)(123, request);
  }
  const result = JSON.stringify({jsonrpc:'2.0',id:3,result:{isError:false,content:[
    {type:'text',text:'{"frame_id":"new"}'},
    {type:'image',saved_image:'/tmp/hush-mcp-test.png'}
  ]}})+'\r\n';
  await run([{output:'{"name":"pointer"}\r\n'+result}]);
  assert.equal(writes.length,1); assert.equal(displayed.length,2);
  await run([{output:result.slice(0,50)},{output:result.slice(50)}]);
  assert.equal(writes.length,2); assert.equal(writes[1].chars,'');
  await assert.rejects(run([{output:'',exit_code:1}]), /outcome unknown/);
  assert.equal(writes.length,1);
  await assert.rejects(run([{output:result.replace('/tmp/hush-mcp-test.png','/home/david/secret.png')}]), /Unexpected screenshot path/);
  await run([{output:result.replace('"isError":false','"isError":true')}]);
  assert.equal(writes.length,1); // Rejections are displayed, never automatically retried.
  let times = [0,25000];
  await run([{output:''},{output:result}],
    {name:'wait_for',arguments:{timeout_ms:30000}}, () => times.shift() ?? 25000);
  assert.equal(writes.length,2); assert.equal(writes[1].chars,'');
  times = [0,51000];
  await assert.rejects(run([{output:''}],
    {name:'wait_for',arguments:{timeout_ms:30000}}, () => times.shift() ?? 51000), /outcome unknown/);
  console.log('PASS: single execution, fragmented result, no replay, safe image path, rejection review');
}
test().catch(error => { console.error(error); process.exitCode=1; });
