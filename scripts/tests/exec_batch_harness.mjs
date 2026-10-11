// Synthetic tool returns only. Run the real returned wrapper and its local persistence commands.
import fs from 'node:fs';
import vm from 'node:vm';
import {spawnSync} from 'node:child_process';

const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const saved = new Map(), messages = [], calls = [];
let failedPersistence = false;
const context = vm.createContext({
  tools: {
    exec_command: async ({cmd, workdir}) => {
      if (fixture.fail_persist_once && !failedPersistence && cmd.includes('f=t.open')) {
        failedPersistence = true;
        return {exit_code: 1, output: 'Synthetic persistence failure AFTER tools returned'};
      }
      const windows = process.platform === 'win32';
      const result = spawnSync(fixture.shell, windows ? ['-NoProfile', '-Command', cmd] : ['-c', cmd],
        {cwd: workdir, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024});
      const output = result.stdout || result.stderr || String(result.error || '');
      return {exit_code: result.status, output: fixture.max_output_bytes
        ? Buffer.from(output).subarray(0, fixture.max_output_bytes).toString('utf8') : output};
    },
    image_gen__imagegen: async args => {
      calls.push(args);
      if (fixture.fail_index === calls.length - 1) throw new Error('Synthetic actual provider failure');
      return fixture.results[calls.length - 1];
    }
  },
  store: (key, value) => saved.set(key, value), load: key => saved.get(key),
  text: value => messages.push(value),
  generatedImage: () => { throw new Error('Synthetic display failure AFTER the tool succeeded'); },
  exit: () => { throw {wrapperExit: true}; }
});
for (let run = 0; run < (fixture.runs || 1); run++) {
  try { await vm.runInContext('(async()=>{\n' + fixture.code + '\n})()', context); }
  catch (error) {
    if (!error.wrapperExit) {
      if (run === 0 && fixture.allow_first_error) messages.push({expected_first_error: String(error)});
      else throw error;
    }
  }
}
console.log(JSON.stringify({call_count: calls.length, calls, messages,
  complete_binary_preserved: [...saved.entries()].filter(([k]) => k.endsWith(':raw'))
    .some(([, value]) => value.image_url === fixture.results[0]?.image_url)}));
