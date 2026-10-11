// Fixed orchestration supplied by prepare. No pixel inspection or candidate registration here.
const sheetBatch = __BATCH_CONFIG__;
const sheetShell = async cmd => {
  const value = await tools.exec_command({cmd, workdir: sheetBatch.cwd, max_output_tokens: 2000});
  if (value.exit_code !== 0) throw new Error(value.output || 'Receipt persistence did not complete');
  return value;
};
const sheetProbe = JSON.parse((await sheetShell(sheetBatch.probe_command)).output);
if (sheetProbe.returned) {
  text(JSON.parse((await sheetShell(sheetBatch.capture_command)).output));
  exit();
}
let sheetCalls = load(sheetBatch.key + ':calls');
if (!sheetCalls) {
  let sheetRequestText = '', sheetPartCount = null;
  for (let part = 0; sheetPartCount === null || part < sheetPartCount; part++) {
    const sheetRequestRead = await tools.exec_command({cmd: sheetBatch.read_command.replace('__REQUEST_PART__', String(part)),
      workdir: sheetBatch.cwd, max_output_tokens: 10000});
    if (sheetRequestRead.exit_code !== 0) throw new Error(sheetRequestRead.output);
    const chunk = JSON.parse(sheetRequestRead.output);
    if (chunk.part !== part || !Number.isInteger(chunk.total_parts) || chunk.total_parts < 1
        || (sheetPartCount !== null && chunk.total_parts !== sheetPartCount) || typeof chunk.text !== 'string'
        || !chunk.text) throw new Error('Incomplete frozen request fragment; no image calls made');
    sheetPartCount = chunk.total_parts;
    sheetRequestText += chunk.text;
  }
  const sheetRequests = JSON.parse(sheetRequestText).requests;
  if (!Array.isArray(sheetRequests) || JSON.stringify(sheetRequests.map(r => r.request_id))
      !== JSON.stringify(sheetBatch.request_ids)) throw new Error('Incomplete or wrong frozen request batch; no image calls made');
  if (sheetProbe.started || load(sheetBatch.key + ':started')) {
    sheetCalls = sheetRequests.map(r => load(sheetBatch.key + ':' + r.request_id + ':receipt'));
    if (sheetCalls.some(call => !call)) {
      text({next_action: 'recover_receipts', receipt_file: sheetBatch.receipt_file,
            instruction: '本批已经开始；恢复store中的真实回执或等待在途工具，不重发生图。'});
      exit();
    }
  } else {
    await sheetShell(sheetBatch.start_command);
    store(sheetBatch.key + ':started', true);
    await Promise.allSettled(sheetRequests.map(async request => {
      let value;
      try {
        value = await tools.image_gen__imagegen(request.arguments);
      } catch (error) {
        store(sheetBatch.key + ':' + request.request_id + ':receipt',
          {request_id: request.request_id, tool_call_id: null, error: String(error)});
        return;
      }
      // Storage/display errors are never labelled as failures of a successful image tool.
      store(sheetBatch.key + ':' + request.request_id + ':raw', value);
      store(sheetBatch.key + ':' + request.request_id + ':receipt',
        {request_id: request.request_id, tool_call_id: null, result: value});
    }));
    sheetCalls = sheetRequests.map(r => load(sheetBatch.key + ':' + r.request_id + ':receipt'));
    if (sheetCalls.some(call => !call)) {
      text({next_action: 'recover_receipts', receipt_file: sheetBatch.receipt_file,
            instruction: '回执保存未完成；恢复实际返回，不重调工具、不登记为生图失败。'});
      exit();
    }
  }
  store(sheetBatch.key + ':calls', sheetCalls);
}
// Only binary image bodies are omitted from the disk receipt. Actual text/path metadata is kept.
// The complete return remains in store; paths and call ids are never manufactured.
const sheetDisk = JSON.stringify({calls: sheetCalls}, (key, value) => {
  if (typeof value === 'string' && /^data:image\//.test(value)) return undefined;
  if (value && typeof value === 'object' && value.type === 'image' && typeof value.data === 'string') {
    const {data, ...metadata} = value;
    return metadata;
  }
  return value;
});
const sheetBase64 = value => {
  const bytes = [];
  for (const char of value) {
    const c = char.codePointAt(0);
    if (c < 128) bytes.push(c);
    else if (c < 2048) bytes.push(192 | c >> 6, 128 | c & 63);
    else if (c < 65536) bytes.push(224 | c >> 12, 128 | c >> 6 & 63, 128 | c & 63);
    else bytes.push(240 | c >> 18, 128 | c >> 12 & 63, 128 | c >> 6 & 63, 128 | c & 63);
  }
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  let out = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const n = bytes[i] << 16 | (bytes[i + 1] || 0) << 8 | (bytes[i + 2] || 0);
    out += alphabet[n >> 18 & 63] + alphabet[n >> 12 & 63]
      + (i + 1 < bytes.length ? alphabet[n >> 6 & 63] : '=')
      + (i + 2 < bytes.length ? alphabet[n & 63] : '=');
  }
  return out;
};
const sheetQuote = value => sheetBatch.windows
  ? "'" + value.replace(/'/g, "''") + "'" : "'" + value.replace(/'/g, "'\"'\"'") + "'";
const sheetPython = code => (sheetBatch.windows ? '& ' : '') + sheetQuote(sheetBatch.python)
  + ' -B -X utf8 -c ' + sheetQuote(code);
const sheetEncoded = sheetBase64(sheetDisk);
for (let i = 0; i < sheetEncoded.length; i += 6000) {
  const chunk = sheetEncoded.slice(i, i + 6000);
  const code = sheetBatch.write_prefix + 'f=t.open("' + (i ? 'ab' : 'wb')
    + '"); f.write(base64.b64decode("' + chunk + '")); f.close();'
    + (i + 6000 >= sheetEncoded.length ? ' t.replace(p)' : '');
  await sheetShell(sheetPython(code));
}
const sheetCaptured = await sheetShell(sheetBatch.capture_command);
text(JSON.parse(sheetCaptured.output));
// Preview failures cannot erase the saved receipt or lead to another generation.
for (const call of sheetCalls) {
  const full = load(sheetBatch.key + ':' + call.request_id + ':raw');
  if (full) {
    try { generatedImage(full); }
    catch (_) { text({preview_display: 'unavailable', receipt_saved: true, request_id: call.request_id}); }
  }
}
